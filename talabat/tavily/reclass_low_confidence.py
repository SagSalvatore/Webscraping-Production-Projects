"""
reclass_low_confidence.py — Second-pass re-classification for Low confidence entries.

Improvements over first pass:
  1. Simpler Tavily query: "{name} Dubai" — avoids triggering generic UAE market articles
  2. Cuisine-enriched query as fallback when name is very short
  3. Smaller OpenAI batch (10) for better focus per restaurant
  4. New prompt: "no chain evidence → Independent Medium" (not Low)
  5. Rule-boosting: strong name signals (shawarma/kabab/coffee/bakery) → skip Tavily entirely
  6. Only writes output if new confidence is better than Low
  7. Merges re-classified rows back into the main CSV and saves new file

Usage:
  cd talabat/tavily
  python reclass_low_confidence.py --csv data/classified/classified_20260622_201829.csv
  python reclass_low_confidence.py                         # auto-picks latest CSV
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from openai import AsyncOpenAI

sys.path.insert(0, str(Path(__file__).parent))

from key_rotator     import TavilyKeyRotator
from utils           import get_logger, retry_async, AutoSaveManager
from utils.retry     import DEFAULT_MAX_RETRIES, DEFAULT_BASE_DELAY, DEFAULT_CAP_DELAY
from config          import clean_name

log = get_logger(__name__)

BASE_DIR   = Path(__file__).parent
TALABAT_ENV = BASE_DIR.parent / ".env"
KEYS_CSV    = BASE_DIR / "tav_keys.csv"
CACHE_DIR   = BASE_DIR / "cache"
OUTPUT_DIR  = BASE_DIR / "data" / "classified"

TAVILY_CACHE_F  = CACHE_DIR / "supabase_tavily_cache.json"
AUTOSAVE_F      = CACHE_DIR / "reclass_autosave.json"          # separate file

# ── Tuning ────────────────────────────────────────────────────────────────────
TAVILY_CONCURRENCY = 30    # gentler — these are hard-to-find restaurants
TAVILY_TIMEOUT_S   = 45    # longer timeout for obscure restaurants
OPENAI_CONCURRENCY = 20
OPENAI_BATCH_SIZE  = 10    # small batches → better per-item focus
OPENAI_MAX_TOKENS  = 2000  # 10 items × ~150 tokens = plenty
OPENAI_MODEL       = "gpt-4o-mini"

# ── Strong name-keyword rules (skip Tavily entirely) ─────────────────────────
_QSR_KEYWORDS  = {
    "shawarma","kabab","kebab","biryani","mandi","curry","grill","grills",
    "tikka","burger","pizza","falafel","wrap","sandwich","tacos","noodles",
    "fried chicken","wok","schnitzel","kofta","cafeteria","canteen",
    "sushi","ramen","poke","taco","taqueria","bao","dumpling","dim sum",
    "thali","dosa","paratha","roti","rice bowl","bowls","butchery",
    "bbq","barbecue","roast","rotisserie","wings","chicken","fried rice",
    "arabic kitchen","mandi house","maqlouba","harees","machboos",
    "koshari","koshary","kusheri","mahashy","mahashi","maqlouba","fatayer",
    "bites","grillhouse","steakhouse","smokehouse","spice","spices",
    "buns","subs","hoagies","calzone","pasta","lasagna","risotto",
    "street food","hawker","canteen","chaat","pani puri",
}
_CAFE_KEYWORDS = {
    "coffee","cafe","café","caffe","karak","kahwa","chai","tea","juice","smoothie",
    "milkshake","shake","bubble tea","dessert","ice cream","gelato","açaí",
    "creamery","sorbet","frozen yogurt","boba","matcha","espresso","latte",
    "juicery","cold press","fresh juice","juic",
}
_BAKERY_KEYWORDS = {
    "bakery","bakeri","patisserie","pastry","cake","cakes","bread","croissant",
    "biscuit","boulangerie","sweets","confectionery",
    "chocolate","bonbon","truffle","donut","doughnut","waffle","pancake",
    "cookies","cookie","brownie","muffin","cupcake","tart","macaron",
    "bomboloni","churros","baklava","knafeh","konafa",
}
_CLOUD_KEYWORDS = {"cloud kitchen","dark kitchen","virtual kitchen","delivery only"}


_QSR_CUISINE_KEYWORDS = {
    "arabic","indian","pakistani","bangladeshi","turkish","iranian","persian",
    "lebanese","yemeni","jordanian","sudanese","ethiopian","egyptian",
    "filipino","thai","vietnamese","indonesian","malaysian","sri lankan",
    "chinese","korean","japanese","nepali","african",
}

def _name_rule(name: str, cuisines: str) -> Optional[dict]:
    """Fast rule from name/cuisine keywords — returns partial result or None."""
    text = (name + " " + cuisines).lower()
    for kw in _CLOUD_KEYWORDS:
        if kw in text:
            return {"restaurant_type": "Cloud Kitchen",
                    "confidence": "Medium", "reasoning": f"Cloud kitchen keyword: {kw}"}
    for kw in _BAKERY_KEYWORDS:
        if kw in text:
            return {"restaurant_type": "Bakery",
                    "confidence": "Medium", "reasoning": f"Bakery keyword: {kw}"}
    for kw in _CAFE_KEYWORDS:
        if kw in text:
            return {"restaurant_type": "Cafes",
                    "confidence": "Medium", "reasoning": f"Cafe/beverage keyword: {kw}"}
    for kw in _QSR_KEYWORDS:
        if kw in text:
            return {"restaurant_type": "Quick-Service Restaurants",
                    "confidence": "Medium", "reasoning": f"QSR keyword: {kw}"}
    # Cuisine tag → QSR (most ethnic/national cuisine restaurants on UAE delivery apps are QSR)
    for kw in _QSR_CUISINE_KEYWORDS:
        if kw in text:
            return {"restaurant_type": "Quick-Service Restaurants",
                    "confidence": "Medium", "reasoning": f"Cuisine type suggests QSR: {kw}"}
    return None


def _load_env() -> None:
    if TALABAT_ENV.exists():
        load_dotenv(TALABAT_ENV)
    local_env = BASE_DIR / ".env"
    if local_env.exists():
        with open(local_env, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip().strip("\"'")
                v = v.strip().strip("\"'")
                if k:
                    os.environ.setdefault(k, v)


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _format_cuisines(row: pd.Series) -> str:
    parts = []
    for col in ("serves_cuisine", "key_cuisines"):
        val = row.get(col, "")
        if not val or str(val) in ("nan", "None", ""):
            continue
        parts.append(str(val).strip())
    return " | ".join(p for p in parts if p)


# ── Stage A: Tavily re-search ─────────────────────────────────────────────────

async def tavily_repass(
    rows: list[dict],
    rotator: TavilyKeyRotator,
    sem: asyncio.Semaphore,
    cache: dict,
    cache_lock: asyncio.Lock,
) -> dict[int, str]:
    """
    Re-search with a SIMPLER query for each Low-confidence restaurant.
    Uses two query tiers:
      Tier 1: "{name} Dubai"  — short, avoids triggering generic UAE results
      Tier 2: "{name} {cuisine} UAE restaurant"  — when name is too generic
    """
    results: dict[int, str] = {}
    total = len(rows)
    completed = 0
    t0 = time.perf_counter()

    async def _search_one(row: dict) -> None:
        nonlocal completed
        bid   = row["branch_id"]
        name  = clean_name(str(row.get("restaurant_name") or ""))
        cuis  = str(row.get("_cuisines", "")).strip()
        key   = f"__repass__{name.lower()}"   # separate cache namespace

        async with cache_lock:
            if key in cache:
                results[bid] = cache[key]
                completed += 1
                return

        if rotator.all_exhausted:
            results[bid] = ""
            completed += 1
            return

        # Tier 1: short direct query
        if len(name) >= 4:
            query = f"{name} Dubai restaurant"
        else:
            query = f"{name} {cuis} UAE restaurant"

        async with sem:
            try:
                resp = await rotator.search(
                    query,
                    search_depth="basic",
                    max_results=5,
                    include_answer=True,
                )
                parts = []
                if resp.get("answer"):
                    parts.append(f"Summary: {resp['answer']}")
                for r in resp.get("results", []):
                    parts.append(f"Source: {r.get('url','')}\n{r.get('content','')[:400]}")
                text = "\n\n".join(parts)
            except Exception as exc:
                err = str(exc)
                if "timed out" in err.lower() or "timeout" in err.lower():
                    log.warning("Tavily timeout (repass) for '{}'", name)
                else:
                    log.error("Tavily error (repass) for '{}': {}", name, exc)
                text = ""

        async with cache_lock:
            cache[key] = text
            results[bid] = text
            completed += 1
            if len(cache) % 200 == 0:
                _save_json(TAVILY_CACHE_F, cache)

        if completed % 100 == 0 or completed == total:
            elapsed = time.perf_counter() - t0
            rate    = completed / elapsed if elapsed > 0 else 0
            eta_s   = (total - completed) / rate if rate > 0 else 0
            log.info(
                "[Repass Tavily] {}/{} ({:.1f}%) | {:.1f} req/s | ETA {:.0f}m{:02d}s | keys={}/{}",
                completed, total,
                100 * completed / total,
                rate,
                int(eta_s // 60), int(eta_s % 60),
                rotator.keys_remaining, len(rotator._keys),
            )

    tasks = [asyncio.create_task(_search_one(r)) for r in rows]
    log.info("Repass Tavily: {} searches (concurrency={}, timeout={}s)",
             total, TAVILY_CONCURRENCY, TAVILY_TIMEOUT_S)
    await asyncio.gather(*tasks)
    _save_json(TAVILY_CACHE_F, cache)
    return results


# ── Stage B: OpenAI second-pass prompt ───────────────────────────────────────

def _build_repass_prompt(batch: list[dict]) -> str:
    items_block = ""
    for i, item in enumerate(batch, 1):
        items_block += (
            f"\nRESTAURANT {i}:\n"
            f"  Name     : {item['name']}\n"
            f"  Cuisines : {item.get('cuisines') or 'Not available'}\n"
            f"  Web info : {str(item.get('tavily_text') or 'No results found')[:600]}\n"
            "---"
        )

    return f"""You are a UAE food-delivery market analyst. Classify each restaurant below.
These are HARD cases where earlier classification was uncertain. Make your BEST call.

══ UAE CONTEXT ══
• Most restaurants on UAE delivery apps are Quick-Service Restaurants (QSR/counter order).
• Full-Service Restaurants require CLEAR evidence: waiter brings food to table.
• "Kahwa/Karak/Arabic coffee/Tea" = Cafes. "Juice bar/Smoothie" = Cafes.
• Short ambiguous names (e.g. "Buns Out", "404 Shawarma") → use cuisine + name clues.
• If web info is about UAE market trends (not this specific restaurant) → IGNORE it.

══ RESTAURANT_TYPE ══
1. Full-Service Restaurants — table service with waiters. Bistros, lounges, fine dining.
2. Quick-Service Restaurants — counter order, takeaway, fast food, fast casual. DEFAULT.
3. Cafes — coffee/tea/beverages primary. Karak, kahwa, juice bars, bubble tea.
4. Bakery — baked goods primary. Pastry shops, cake shops, bread.
5. Cloud Kitchen — delivery-only, no physical customer-facing outlet.

══ OUTLET_TYPE ══
- Chain       — ONLY if web info EXPLICITLY names THIS brand with 2+ UAE locations.
- Independent — single location OR no clear chain evidence. USE THIS AS DEFAULT.

══ CHAIN_TYPE ══
- MNC Chain   — global brand (3+ countries)
- Local Chain — UAE/GCC/regional only
- N/A         — for Independent (DEFAULT when outlet_type=Independent)

══ CONFIDENCE ══
- High   — strong clear evidence
- Medium — reasonable inference from name/cuisine (USE THIS when uncertain, NOT Low)
- Low    — only if genuinely impossible to classify

IMPORTANT: Default to "Independent" + "Medium" confidence when chain status is unclear.
Do NOT output "Low" confidence unless the restaurant is completely unclassifiable.

Return ONLY valid JSON array with {len(batch)} objects:
[
  {{
    "restaurant_index": 1,
    "restaurant_type": "<exact name from list above>",
    "outlet_type": "Chain" | "Independent",
    "chained_outlet_type": "Local Chain" | "MNC Chain" | "N/A",
    "confidence": "High" | "Medium" | "Low",
    "reasoning": "<8 words max>"
  }},
  ...
]

RESTAURANTS:
{items_block}"""


_VALID_TYPES = {
    "Full-Service Restaurants", "Quick-Service Restaurants",
    "Cafes", "Bakery", "Cloud Kitchen",
}

def _normalise_type(raw: str) -> str:
    r = raw.lower().strip()
    if "full" in r or "casual dining" in r or "fine dining" in r or "table service" in r:
        return "Full-Service Restaurants"
    if "quick" in r or "qsr" in r or "fast" in r or "counter" in r or "takeaway" in r:
        return "Quick-Service Restaurants"
    if "cafe" in r or "café" in r or "coffee" in r or "tea" in r or "juice" in r or "beverage" in r:
        return "Cafes"
    if "bakery" in r or "pastry" in r or "patiss" in r:
        return "Bakery"
    if "cloud" in r or "virtual" in r or "delivery only" in r or "dark kitchen" in r:
        return "Cloud Kitchen"
    for v in _VALID_TYPES:
        if v.lower() == r:
            return v
    return "Quick-Service Restaurants"


def _parse_repass_response(raw: str, expected: int) -> list[dict]:
    fallback = {
        "restaurant_type": "Quick-Service Restaurants",
        "outlet_type": "Independent", "chained_outlet_type": "N/A",
        "confidence": "Medium", "reasoning": "Repass fallback",
    }
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            for k in ("results", "restaurants", "data", "classifications"):
                if k in parsed and isinstance(parsed[k], list):
                    parsed = parsed[k]
                    break
            else:
                lists = [v for v in parsed.values() if isinstance(v, list)]
                parsed = lists[0] if lists else []
    except Exception as e:
        log.error("Repass JSON error: {} | raw[:100]={}", e, raw[:100])
        return [fallback.copy() for _ in range(expected)]

    results = []
    for item in (parsed or [])[:expected]:
        rt = _normalise_type(item.get("restaurant_type", ""))
        ot = item.get("outlet_type", "Independent")
        ct = item.get("chained_outlet_type", "N/A")
        if ot not in ("Chain", "Independent"):
            ot = "Independent"
        if ct == "Regional Chain":
            ct = "Local Chain"
        if ct not in ("Local Chain", "MNC Chain", "N/A"):
            ct = "N/A"
        conf = item.get("confidence", "Medium")
        if conf not in ("High", "Medium", "Low"):
            conf = "Medium"
        results.append({
            "restaurant_type":    rt,
            "outlet_type":        ot,
            "chained_outlet_type": ct,
            "confidence":         conf,
            "reasoning":          item.get("reasoning", ""),
        })

    while len(results) < expected:
        results.append(fallback.copy())
    return results


async def openai_repass(
    rows: list[dict],
    tavily_results: dict[int, str],
    client: AsyncOpenAI,
    sem: asyncio.Semaphore,
    autosave: AutoSaveManager,
) -> dict[int, dict]:
    """Classify Low-confidence restaurants in small batches."""
    to_do = [r for r in rows if not autosave.contains(str(r["branch_id"]))]
    if not to_do:
        log.info("All repass entries already autosaved.")
        return {}

    batches: list[tuple[list[int], list[dict]]] = []
    buf_ids: list[int]  = []
    buf_data: list[dict] = []
    for row in to_do:
        bid = row["branch_id"]
        buf_ids.append(bid)
        buf_data.append({
            "name":       clean_name(str(row.get("restaurant_name") or "")),
            "cuisines":   row.get("_cuisines", ""),
            "tavily_text": tavily_results.get(bid, ""),
        })
        if len(buf_ids) >= OPENAI_BATCH_SIZE:
            batches.append((buf_ids[:], buf_data[:]))
            buf_ids.clear(); buf_data.clear()
    if buf_ids:
        batches.append((buf_ids[:], buf_data[:]))

    results: dict[int, dict] = {}
    total_batches = len(batches)
    calls_made    = 0
    tokens_used   = 0
    t0 = time.perf_counter()

    log.info("Repass OpenAI: {} restaurants → {} batches (size={}, concurrency={})",
             len(to_do), total_batches, OPENAI_BATCH_SIZE, OPENAI_CONCURRENCY)

    async def _process_batch(idx_list: list[int], data_list: list[dict]) -> None:
        nonlocal calls_made, tokens_used
        prompt = _build_repass_prompt(data_list)
        async with sem:
            resp = await retry_async(
                client.chat.completions.create,
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content":
                     "UAE restaurant analyst. Respond with valid JSON only. "
                     "Default to Independent+Medium when chain evidence is absent."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=OPENAI_MAX_TOKENS,
                response_format={"type": "json_object"},
                max_retries=DEFAULT_MAX_RETRIES,
                base_delay=DEFAULT_BASE_DELAY,
                cap_delay=DEFAULT_CAP_DELAY,
            )
        calls_made  += 1
        tokens_used += resp.usage.prompt_tokens + resp.usage.completion_tokens
        batch_res    = _parse_repass_response(resp.choices[0].message.content, len(data_list))

        for bid, res in zip(idx_list, batch_res):
            results[bid] = res
            await autosave.add(str(bid), res)

        elapsed  = time.perf_counter() - t0
        done_pct = 100 * calls_made / total_batches
        rate     = calls_made / elapsed if elapsed > 0 else 0
        eta_s    = (total_batches - calls_made) / rate if rate > 0 else 0
        cost     = (tokens_used / 1_000_000) * 0.375
        log.info(
            "[Repass OpenAI] batch {}/{} ({:.1f}%) | ETA {:.0f}m{:02d}s | "
            "tokens={:,} | cost=${:.3f}",
            calls_made, total_batches, done_pct,
            int(eta_s // 60), int(eta_s % 60),
            tokens_used, cost,
        )

    tasks = [asyncio.create_task(_process_batch(ids, data)) for ids, data in batches]
    await asyncio.gather(*tasks)
    log.info("Repass OpenAI done | {} calls | {:,} tokens | ~${:.3f}",
             calls_made, tokens_used, (tokens_used / 1_000_000) * 0.375)
    return results


# ── Merge & save ──────────────────────────────────────────────────────────────

def merge_and_save(
    main_df: pd.DataFrame,
    reclass: dict[int, dict],
    autosave_data: dict,
) -> pd.DataFrame:
    """
    For each branch_id in the reclass results, update the main_df row
    ONLY if the new confidence is better than Low.
    """
    CONF_RANK = {"High": 3, "Medium": 2, "Low": 1}
    updated = 0

    for idx, row in main_df.iterrows():
        bid = int(row["branch_id"])
        new = reclass.get(bid) or autosave_data.get(str(bid))
        if new is None:
            continue
        old_conf = CONF_RANK.get(row["confidence"], 0)
        new_conf = CONF_RANK.get(new.get("confidence", "Low"), 0)
        if new_conf > old_conf:
            main_df.at[idx, "restaurant_type"]       = new["restaurant_type"]
            main_df.at[idx, "outlet_type"]           = new["outlet_type"]
            main_df.at[idx, "chained_outlet_type"]   = new["chained_outlet_type"]
            main_df.at[idx, "confidence"]            = new["confidence"]
            main_df.at[idx, "reasoning"]             = new.get("reasoning", "")
            main_df.at[idx, "classification_source"] = "repass"
            main_df.at[idx, "classified_at"]         = datetime.now(timezone.utc).isoformat()
            # Enforce: Independent → N/A
            if main_df.at[idx, "outlet_type"] == "Independent":
                main_df.at[idx, "chained_outlet_type"] = "N/A"
            updated += 1

    log.info("Merged: {} rows improved (confidence upgraded)", updated)
    return main_df


# ── Main ─────────────────────────────────────────────────────────────────────

async def main(csv_path: Path) -> None:
    _load_env()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    openai_key = os.getenv("OPENAI_API_KEY", "").strip("\"'")
    if not openai_key:
        raise EnvironmentError("OPENAI_API_KEY not set")

    # ── Load main CSV ─────────────────────────────────────────────────────────
    log.info("Loading main CSV: {}", csv_path)
    main_df = pd.read_csv(csv_path, encoding="utf-8-sig")

    low_df  = main_df[main_df["confidence"] == "Low"].copy()
    log.info("Low confidence entries to re-classify: {:,} / {:,}", len(low_df), len(main_df))

    if low_df.empty:
        log.info("No Low confidence entries — nothing to do.")
        return

    # Load cuisine info from Supabase loader for these branch_ids
    # (cuisine tags are not in the classified CSV — pull from DB or skip)
    # For simplicity: cuisine column not present in classified CSV; use empty string.
    low_rows = low_df.to_dict("records")
    for r in low_rows:
        r["_cuisines"] = ""   # no cuisine in classified CSV

    print(f"\n{'='*60}")
    print(f"  LOW-CONFIDENCE REPASS")
    print(f"  Restaurants: {len(low_rows):,}")
    print(f"  Tavily concurrency: {TAVILY_CONCURRENCY} | timeout: {TAVILY_TIMEOUT_S}s")
    print(f"  OpenAI batch: {OPENAI_BATCH_SIZE} | concurrency: {OPENAI_CONCURRENCY}")
    print(f"{'='*60}\n")

    # ── Name-based rule pre-filter ────────────────────────────────────────────
    rule_results: dict[int, dict] = {}
    still_need_api: list[dict]    = []

    for row in low_rows:
        bid  = row["branch_id"]
        name = str(row.get("restaurant_name") or "")
        rule = _name_rule(name, row.get("_cuisines", ""))
        if rule:
            rule_results[bid] = {
                "restaurant_type":    rule["restaurant_type"],
                "outlet_type":        "Independent",
                "chained_outlet_type": "N/A",
                "confidence":         rule["confidence"],
                "reasoning":          rule["reasoning"],
                "classification_source": "repass_rule",
            }
        else:
            still_need_api.append(row)

    log.info("Name-rule pre-filter: {} resolved | {} still need API",
             len(rule_results), len(still_need_api))

    # ── Stage A: Tavily re-search ─────────────────────────────────────────────
    rotator    = TavilyKeyRotator.from_csv(KEYS_CSV, timeout=TAVILY_TIMEOUT_S)
    tavily_sem = asyncio.Semaphore(TAVILY_CONCURRENCY)
    cache_lock = asyncio.Lock()
    cache      = _load_json(TAVILY_CACHE_F)
    log.info("Tavily cache pre-loaded: {:,} entries", len(cache))

    tavily_results = await tavily_repass(
        still_need_api, rotator, tavily_sem, cache, cache_lock
    )

    # ── Stage B: OpenAI re-classification ─────────────────────────────────────
    oai_client = AsyncOpenAI(api_key=openai_key)
    oai_sem    = asyncio.Semaphore(OPENAI_CONCURRENCY)

    async with AutoSaveManager(AUTOSAVE_F, flush_every=50) as autosave:
        api_results  = await openai_repass(
            still_need_api, tavily_results, oai_client, oai_sem, autosave
        )
        autosave_data = autosave.all_items()

    # Merge rule + api results
    all_new = {**rule_results, **api_results}
    for bid_str, res in autosave_data.items():
        bid = int(bid_str)
        if bid not in all_new:
            all_new[bid] = res

    # ── Merge into main DF ────────────────────────────────────────────────────
    merged_df = merge_and_save(main_df, all_new, {})

    # ── Save ──────────────────────────────────────────────────────────────────
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"classified_final_{ts}.csv"
    merged_df.to_csv(out_path, index=False, encoding="utf-8-sig")

    size_kb = out_path.stat().st_size / 1024
    log.info("Saved {} rows → {} ({:.0f} KB)", len(merged_df), out_path, size_kb)

    # ── Summary ───────────────────────────────────────────────────────────────
    remaining_low = (merged_df["confidence"] == "Low").sum()
    print(f"\n{'='*60}")
    print(f"  REPASS COMPLETE")
    print(f"{'='*60}")
    print(f"\n  restaurant_type distribution:")
    for k, v in merged_df["restaurant_type"].value_counts().items():
        print(f"    {k:<38} {v:>6,}")
    print(f"\n  outlet_type distribution:")
    for k, v in merged_df["outlet_type"].value_counts().items():
        print(f"    {k:<38} {v:>6,}")
    print(f"\n  confidence distribution (after repass):")
    for k, v in merged_df["confidence"].value_counts().items():
        print(f"    {k:<38} {v:>6,}")
    print(f"\n  Low confidence remaining : {remaining_low:,}")
    print(f"  Tavily: {rotator.searches_made} searches | keys_active={rotator.keys_remaining}/23")
    print(f"\n  Output: {out_path}")
    print(f"{'='*60}\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Re-classify Low confidence UAE restaurants")
    p.add_argument(
        "--csv", type=Path, default=None,
        help="Path to classified CSV (default: auto-picks latest in data/classified/)",
    )
    return p.parse_args()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()

    if args.csv:
        csv_path = args.csv
    else:
        files = sorted(OUTPUT_DIR.glob("classified_2*.csv"))
        if not files:
            print("No classified CSV found in data/classified/")
            sys.exit(1)
        csv_path = files[-1]
        print(f"Auto-selected: {csv_path.name}")

    asyncio.run(main(csv_path))
