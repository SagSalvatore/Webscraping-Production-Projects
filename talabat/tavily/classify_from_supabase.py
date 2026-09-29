"""
classify_from_supabase.py — Async pipeline: Supabase → classify → CSV.

Fills 3 columns for ~15,532 UAE restaurants:
  • restaurant_type   → Full-Service Restaurants / Quick-Service Restaurants /
                         Cafes / Bakery / Cloud Kitchen
  • outlet_type       → Independent / Chain
  • chained_outlet_type → Local Chain / MNC Chain / N/A

Architecture (3 stages, all async):
  Stage 1 — Rule-based pre-classification (FREE, 0 API calls)
    a. Name-frequency analysis → Chain if ≥2 branches in our DB
    b. Known MNC / Local Chain lookup (config.py)  → chained_outlet_type
    c. Cuisine tag + name keyword rules → restaurant_type

  Stage 2 — Tavily web search for remaining (async, 23-key rotation)
    • asyncio.Semaphore(20) — 20 concurrent searches
    • Cache to cache/supabase_tavily_cache.json (zero re-billing on resume)
    • All 23 keys from tav_keys.csv; auto-rotate on 429 / credit exhaustion

  Stage 3 — OpenAI GPT-4o-mini batch classification (async)
    • 30 restaurants per batch; all batches fire concurrently (sem=20)
    • Full-jitter exponential back-off on 429 / timeout  (utils/retry.py)
    • AutoSave every 50 items → safe resume on crash  (utils/autosave.py)
    • RPM headroom: GPT-4o-mini Tier-2 = 4,200 RPM; 20 concurrent << 70 req/s

Output → data/classified/classified_YYYYMMDD_HHMMSS.csv (no Supabase write)

Usage:
  cd talabat/tavily
  python classify_from_supabase.py --test           # 20 restaurants (smoke test)
  python classify_from_supabase.py --limit 200      # 200 restaurants (dry run)
  python classify_from_supabase.py                  # full 15,532
  python classify_from_supabase.py --resume         # alias for default (autosave always applies)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from openai import AsyncOpenAI, RateLimitError, APITimeoutError, APIConnectionError

# ── Local imports ─────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from key_rotator    import TavilyKeyRotator
from supabase_loader import load_restaurants
from utils           import get_logger, retry_async, AutoSaveManager
from utils.retry     import DEFAULT_MAX_RETRIES, DEFAULT_BASE_DELAY, DEFAULT_CAP_DELAY
from config          import clean_name, KNOWN_MNC, KNOWN_LOCAL_CHAIN

log = get_logger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
TALABAT_ENV    = BASE_DIR.parent / ".env"          # Supabase creds
CACHE_DIR      = BASE_DIR / "cache"
KEYS_CSV       = BASE_DIR / "tav_keys.csv"
OUTPUT_DIR     = BASE_DIR / "data" / "classified"

TAVILY_CACHE_F  = CACHE_DIR / "supabase_tavily_cache.json"
AUTOSAVE_F      = CACHE_DIR / "supabase_classify_autosave.json"

# ── Concurrency + batching ────────────────────────────────────────────────────
TAVILY_CONCURRENCY = 40   # ~2 concurrent/key across 23 keys — reduces server-side queuing
OPENAI_CONCURRENCY = 20
OPENAI_BATCH_SIZE  = 30
TAVILY_TIMEOUT_S   = 30   # 30s HTTP-level timeout per call (via search(timeout=) param)

# ── OpenAI model ──────────────────────────────────────────────────────────────
OPENAI_MODEL       = "gpt-4o-mini"
OPENAI_MAX_TOKENS  = 4096   # 30 items × ~100 tokens each = ~3000 needed; 2000 was truncating
OPENAI_TEMPERATURE = 0.0

# ── Valid output values ───────────────────────────────────────────────────────
VALID_REST_TYPES = {
    "Full-Service Restaurants",
    "Quick-Service Restaurants",
    "Cafes",
    "Bakery",
    "Cloud Kitchen",
}

# Map old taxonomy (from config.py KNOWN_MNC tuples) → new 5-category taxonomy
_OLD_TYPE_MAP: dict[str, str] = {
    "Quick-Service Restaurant (QSR)": "Quick-Service Restaurants",
    "Fast-Casual Restaurant":         "Quick-Service Restaurants",
    "Food Court Stall":               "Quick-Service Restaurants",
    "Casual Dining Restaurant":       "Full-Service Restaurants",
    "Fine Dining Restaurant":         "Full-Service Restaurants",
    "Bar & Grill":                    "Full-Service Restaurants",
    "Café / Coffee Shop":             "Cafes",
    "Coffee Shop":                    "Cafes",
    "Bakery & Café":                  "Bakery",
    "Patisserie / Dessert Shop":      "Bakery",
    "Patisserie":                     "Bakery",
    "Cloud Kitchen":                  "Cloud Kitchen",
    "Catering Service":               "Cloud Kitchen",
}

# ── Stage-1 cuisine-rule signals ──────────────────────────────────────────────
_CLOUD_KW   = {"cloud kitchen", "dark kitchen", "virtual kitchen",
               "delivery only", "delivery kitchen"}
_BAKERY_KW  = {"bakery", "pastry", "pastries", "cakes", "cake", "bread",
               "patisserie", "croissants"}
_CAFE_KW    = {"coffee", "cafe", "café", "tea", "juices", "juice",
               "smoothies", "desserts", "ice cream", "gelato",
               "beverages", "milkshake", "shakes"}
_SAVORY_KW  = {"indian", "arabic", "grills", "biryani", "pizza", "burger",
               "chicken", "beef", "lamb", "seafood", "sushi", "rice",
               "noodles", "pasta", "shawarma", "kebab", "mandi", "curry"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_env() -> None:
    """Load talabat/.env (Supabase) + tavily/.env (OpenAI key, quoted-key format)."""
    if TALABAT_ENV.exists():
        load_dotenv(TALABAT_ENV)
    # tavily/.env uses "KEY"=value format — load_dotenv won't handle the quotes
    local_env = BASE_DIR / ".env"
    if local_env.exists():
        with open(local_env, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip().strip("\"'")
                v = v.strip().strip("\"'")
                if k:
                    os.environ.setdefault(k, v)


def _format_cuisines(row: dict) -> str:
    parts = []
    for col in ("serves_cuisine", "key_cuisines"):
        val = row.get(col)
        if not val:
            continue
        if isinstance(val, list):
            parts.append(", ".join(str(x) for x in val if x))
        elif isinstance(val, str) and val.strip():
            parts.append(val.strip())
    return " | ".join(p for p in parts if p) or ""


def _cuisine_tags(row: dict) -> list[str]:
    """Return list of lowercase cuisine tags (handles list or string columns)."""
    tags: list[str] = []
    for col in ("serves_cuisine", "key_cuisines"):
        val = row.get(col)
        if not val:
            continue
        if isinstance(val, list):
            tags += [str(x).lower().strip() for x in val if x]
        elif isinstance(val, str):
            tags += [t.lower().strip() for t in val.split(",")]
    return [t for t in tags if t]


def _rule_restaurant_type(name: str, tags: list[str]) -> Optional[str]:
    """Stage-1 restaurant_type from name keywords + cuisine tags. Returns None if unsure."""
    name_low  = name.lower()
    tag_set   = set(tags)
    tag_str   = " ".join(tags)

    # Cloud kitchen by name
    if any(kw in name_low for kw in _CLOUD_KW):
        return "Cloud Kitchen"

    has_savory  = any(kw in tag_str for kw in _SAVORY_KW)

    # Bakery: has bakery tags, no savory meal tags
    has_bakery  = any(kw in tag_str for kw in _BAKERY_KW)
    if has_bakery and not has_savory:
        return "Bakery"

    # Cafes: has cafe/beverage tags, no savory meal tags
    has_cafe    = any(kw in tag_str for kw in _CAFE_KW)
    if has_cafe and not has_savory:
        return "Cafes"

    return None


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except PermissionError:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalise_type(raw: str) -> str:
    """Fuzzy-map slight variants back to a valid VALID_REST_TYPES value."""
    r = raw.lower().strip()
    if "full" in r or "casual dining" in r or "fine dining" in r:
        return "Full-Service Restaurants"
    if "quick" in r or "qsr" in r or "fast food" in r or "fast-casual" in r or "counter" in r:
        return "Quick-Service Restaurants"
    if "cafe" in r or "café" in r or "coffee" in r or "tea" in r or "juice" in r or "dessert" in r:
        return "Cafes"
    if "bakery" in r or "bakeri" in r or "patiss" in r or "pastry" in r:
        return "Bakery"
    if "cloud" in r or "virtual" in r or "delivery" in r or "dark kitchen" in r:
        return "Cloud Kitchen"
    # Already valid?
    for v in VALID_REST_TYPES:
        if v.lower() == r:
            return v
    log.warning("Unrecognised restaurant_type '{}' — defaulting to Quick-Service Restaurants", raw)
    return "Quick-Service Restaurants"


# ── Stage-1 pre-classification ────────────────────────────────────────────────

def stage1_preclassify(
    df: pd.DataFrame,
) -> tuple[dict[int, dict], list[int]]:
    """
    Returns:
        classified   : {branch_id: result_dict}   — pre-classified (no API needed)
        api_needed   : [branch_id, ...]            — need Tavily + OpenAI
    """
    # Build name-frequency map across ALL restaurants
    name_counts: Counter = Counter(
        clean_name(r).lower() for r in df["restaurant_name"].dropna()
    )

    classified: dict[int, dict] = {}
    api_needed: list[int]        = []

    for _, row in df.iterrows():
        bid  = int(row["branch_id"])
        name = str(row.get("restaurant_name") or "").strip()
        tags = _cuisine_tags(row)

        result_type  : Optional[str] = None
        outlet_type  : Optional[str] = None
        chained_type : Optional[str] = None
        source       : str           = "api"
        confidence   : str           = "High"
        reasoning    : str           = ""

        # ── (a) Known MNC / Local Chain lookup ───────────────────────────────
        clean = clean_name(name)
        low   = clean.lower()

        mnc_match   = next((v for k, v in KNOWN_MNC.items()        if k in low), None)
        local_match = next((v for k, v in KNOWN_LOCAL_CHAIN.items() if k in low), None)

        if mnc_match:
            _, outlet_type, chained_type, old_type = mnc_match
            result_type = _OLD_TYPE_MAP.get(old_type, "Quick-Service Restaurants")
            source      = "rule_mnc"
            reasoning   = f"Known MNC chain: {clean}"

        elif local_match:
            _, outlet_type, chained_type, old_type = local_match
            result_type = _OLD_TYPE_MAP.get(old_type, "Quick-Service Restaurants")
            source      = "rule_local_chain"
            reasoning   = f"Known local chain: {clean}"

        else:
            # ── (b) Name-frequency → Chain ──────────────────────────────────
            freq = name_counts.get(low, 0)
            if freq >= 2:
                outlet_type  = "Chain"
                chained_type = None      # need OpenAI to decide Local vs MNC
                source       = "rule_chain_freq"
                reasoning    = f"Name appears {freq}× in DB → Chain"

            # ── (c) Cuisine-tag → restaurant_type ───────────────────────────
            rule_type = _rule_restaurant_type(name, tags)
            if rule_type:
                result_type = rule_type
                if source == "api":
                    source = "rule_cuisine"
                    reasoning = f"Cuisine tag rule: {rule_type}"

        # If we still need OpenAI for at least one column → defer to API stage
        if result_type is None or outlet_type is None or chained_type is None:
            # Partial pre-fill stored inside the row for later use
            api_needed.append(bid)
            # Store partial pre-class info so API stage can use chain hints
            classified[bid] = {
                "_partial": True,
                "restaurant_type":    result_type,
                "outlet_type":        outlet_type,
                "chained_outlet_type": chained_type,
                "confidence":         confidence,
                "classification_source": source,
                "reasoning":          reasoning,
            }
        else:
            classified[bid] = {
                "_partial": False,
                "restaurant_type":    result_type,
                "outlet_type":        outlet_type,
                "chained_outlet_type": chained_type,
                "confidence":         confidence,
                "classification_source": source,
                "reasoning":          reasoning,
            }

    fully_done = sum(1 for v in classified.values() if not v.get("_partial"))
    log.info(
        "Stage 1: {} fully pre-classified | {} need API",
        fully_done, len(api_needed),
    )
    return classified, api_needed


# ── Stage-2 Tavily search ─────────────────────────────────────────────────────

async def stage2_tavily_search(
    df: pd.DataFrame,
    api_needed_ids: list[int],
    rotator: TavilyKeyRotator,
    sem: asyncio.Semaphore,
    cache: dict,
    cache_lock: asyncio.Lock,
) -> dict[int, str]:
    """
    Fire concurrent Tavily searches for all api_needed restaurants.
    Returns {branch_id: search_text}.
    Cache prevents re-billing on resume.
    """
    id_to_row = {int(r["branch_id"]): r for _, r in df.iterrows()}
    results: dict[int, str] = {}

    total       = len(api_needed_ids)
    completed   = 0
    cache_hits  = 0
    t0_stage2   = time.perf_counter()

    async def _search_one(bid: int) -> None:
        nonlocal completed, cache_hits
        row  = id_to_row[bid]
        name = clean_name(str(row.get("restaurant_name") or ""))
        key  = name.lower().strip()

        # Cache hit?
        async with cache_lock:
            if key in cache:
                results[bid] = cache[key]
                completed += 1
                cache_hits += 1
                return

        if rotator.all_exhausted:
            log.warning("All Tavily keys exhausted — skipping: {}", name)
            results[bid] = ""
            completed += 1
            return

        # Broad query: service model + chain/locations (no domain filter)
        query = (
            f'"{name}" restaurant UAE '
            f'dine-in OR "counter service" OR "table service" OR "fast food" '
            f'OR locations OR branches OR chain'
        )
        async with sem:
            try:
                # timeout is set on AsyncTavilyClient itself (HTTP-level) — no asyncio wrapper needed
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
                    parts.append(
                        f"Source: {r.get('url','')}\n{r.get('content','')[:400]}"
                    )
                text = "\n\n".join(parts)
            except RuntimeError:
                text = ""   # all keys exhausted mid-run
            except Exception as exc:
                # Includes Tavily's own TimeoutError / ConnectError
                err_str = str(exc)
                if "timed out" in err_str.lower() or "timeout" in err_str.lower():
                    log.warning("Tavily timeout for '{}' — no web context", name)
                else:
                    log.error("Tavily error for '{}': {}", name, exc)
                text = ""

        async with cache_lock:
            cache[key] = text
            results[bid] = text
            completed += 1
            # Flush cache every 100 new entries
            if len(cache) % 100 == 0:
                _save_json(TAVILY_CACHE_F, cache)

        # Live progress: log every 100 completions + first 5
        if completed <= 5 or completed % 100 == 0 or completed == total:
            elapsed    = time.perf_counter() - t0_stage2
            rate       = completed / elapsed if elapsed > 0 else 0
            eta_s      = (total - completed) / rate if rate > 0 else 0
            actual_api = completed - cache_hits
            log.info(
                "[Tavily] {}/{} ({:.1f}%) | {:.1f} req/s | ETA {:.0f}m{:02d}s | "
                "cache={} api={} | keys_active={}/{}",
                completed, total,
                100 * completed / total,
                rate,
                int(eta_s // 60), int(eta_s % 60),
                cache_hits, actual_api,
                rotator.keys_remaining, len(rotator._keys),
            )

    tasks = [asyncio.create_task(_search_one(bid)) for bid in api_needed_ids]
    log.info(
        "Stage 2: firing {} Tavily searches (concurrency={}, cache_preloaded={})",
        total, TAVILY_CONCURRENCY, len(cache),
    )
    await asyncio.gather(*tasks)
    _save_json(TAVILY_CACHE_F, cache)   # final flush
    elapsed = time.perf_counter() - t0_stage2
    log.info(
        "Stage 2 DONE in {:.1f}s ({:.1f}m) | {} searches | {} cache hits | {}",
        elapsed, elapsed / 60,
        rotator.searches_made, cache_hits,
        rotator.status_line(),
    )
    return results


# ── Stage-3 OpenAI batch classification ──────────────────────────────────────

def _build_prompt(batch: list[dict]) -> str:
    items_block = ""
    for i, item in enumerate(batch, 1):
        chain_hint = item.get("chain_hint") or ""
        items_block += (
            f"\nRESTAURANT {i}:\n"
            f"  Name       : {item['name']}\n"
            f"  Cuisines   : {item.get('cuisines') or 'Not available'}\n"
            f"  Chain hint : {chain_hint}\n"
            f"  Web context: {str(item.get('tavily_text') or 'No web results')[:700]}\n"
            "---"
        )

    return f"""You are a UAE foodservice market research analyst. Classify each restaurant below.

══ CRITICAL UAE CONTEXT ══
• Restaurants on Talabat (UAE delivery app) are PREDOMINANTLY QSR/Fast Casual.
• Full-Service Restaurants are the MINORITY — require EXPLICIT evidence of table service with a waiter.
• "Fast Casual" = counter order + limited seating → classify as Quick-Service Restaurants.
• AED 10-30 average bill = strong QSR signal.
• "Kahwa", "Karak", "Arabic coffee", "Chai", "Tea house" = Cafes (NOT FSR).
• "Regional Chain" (e.g. Qatar→UAE, Saudi→UAE) = Local Chain in our taxonomy.
• When web context is unclear, DEFAULT to Quick-Service Restaurants (NOT Full-Service).

══ RESTAURANT_TYPE — choose exactly ONE ══
1. Full-Service Restaurants — Requires CLEAR evidence: server/waiter brings food to table, proper dine-in,
   full menu with starters/mains/desserts. Fine dining, casual sit-down restaurants.
2. Quick-Service Restaurants — Counter order, self-carry tray/bag, takeaway-first, fast food, food courts,
   kiosk ordering. ANY "fast casual" concept where you order at counter. Default for ambiguous cases.
3. Cafes — Coffee shops, tea cafes, karak/kahwa spots, juice bars, dessert lounges, smoothie bars,
   bubble tea, milkshake bars. PRIMARY draw = beverages or light bites (not a full meal).
4. Bakery — Bakeries, patisseries, cake shops, bread shops. PRIMARY draw = baked goods/pastries.
5. Cloud Kitchen — Delivery-ONLY, zero dine-in, no physical customer-facing storefront, virtual brand.

══ OUTLET_TYPE — choose ONE ══
- Chain       — ONLY if the web context EXPLICITLY names THIS SAME brand operating 2+ UAE locations
- Independent — single location, OR no explicit confirmation of multiple UAE branches

CHAIN SKEPTICISM RULES (CRITICAL):
• Generic text like "popular in Dubai" or "many restaurants serve X" does NOT prove Chain
• Web results about a DIFFERENT restaurant with a similar name → Independent
• "Multiple branches" must name THIS brand specifically, not just restaurants in UAE generally
• If uncertain → default to Independent (do NOT assume Chain from vague context)

══ CHAINED_OUTLET_TYPE — choose ONE ══
- MNC Chain   — global brand with presence in 3+ countries (USA, UK, Europe, Asia, etc.)
- Local Chain — UAE/GCC/regional chain only (e.g. Qatar→UAE, KSA→UAE, UAE-only)
- N/A         — for Independent outlets ONLY

══ MANDATORY RULES ══
1. If chain_hint says "Chain" → outlet_type MUST be "Chain"
2. "Resto-Cafe", "Cafe-Restaurant" with beverages-first concept → Cafes, NOT FSR
3. Indian/Pakistani budget restaurants (AED 15-25) → Quick-Service Restaurants
4. Sushi bars, lounges, bistros with table service → Full-Service Restaurants
5. Pizza/pasta/sandwich delivery concepts → Quick-Service Restaurants (unless clear waiter service)
6. Use "Local Chain" for ANY regional chain not globally known
7. NEVER output "Regional Chain" — use "Local Chain" instead
8. Web context from a WRONG COUNTRY (e.g. "located in Mexico", "based in India") → ignore it, classify as Independent with Low confidence

Return ONLY a valid JSON array with {len(batch)} objects:
[
  {{
    "restaurant_index": 1,
    "restaurant_type": "<one of 5 exact names above>",
    "outlet_type": "Chain" | "Independent",
    "chained_outlet_type": "Local Chain" | "MNC Chain" | "N/A",
    "confidence": "High" | "Medium" | "Low",
    "reasoning": "<8 words max — key evidence only>"
  }},
  ...
]

RESTAURANTS TO CLASSIFY:
{items_block}"""


def _parse_openai_response(raw: str, expected: int) -> list[dict]:
    """Parse OpenAI JSON, pad with fallback if short."""
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
    except (json.JSONDecodeError, Exception) as e:
        log.error("JSON parse error: {} | raw[:200]={}", e, raw[:200])
        return [
            {"restaurant_type": "Quick-Service Restaurants",
             "outlet_type": "Independent", "chained_outlet_type": "N/A",
             "confidence": "Low", "reasoning": f"Parse error: {e}"}
            for _ in range(expected)
        ]

    results = []
    for item in (parsed or [])[:expected]:
        rt = _normalise_type(item.get("restaurant_type", ""))
        ot = item.get("outlet_type", "Independent")
        ct = item.get("chained_outlet_type", "N/A")
        if ot not in ("Chain", "Independent"):
            ot = "Independent"
        # Map "Regional Chain" → "Local Chain" (model sometimes outputs this)
        if ct == "Regional Chain":
            ct = "Local Chain"
        if ct not in ("Local Chain", "MNC Chain", "N/A"):
            ct = "N/A"
        results.append({
            "restaurant_type":    rt,
            "outlet_type":        ot,
            "chained_outlet_type": ct,
            "confidence":         item.get("confidence", "Medium"),
            "reasoning":          item.get("reasoning", ""),
        })

    while len(results) < expected:
        results.append({
            "restaurant_type": "Quick-Service Restaurants",
            "outlet_type": "Independent", "chained_outlet_type": "N/A",
            "confidence": "Low", "reasoning": "Padding — OpenAI returned fewer items",
        })
    return results


class OpenAIClassifier:
    """Async batch classifier with retry, autosave, and concurrency cap."""

    def __init__(self, client: AsyncOpenAI, sem: asyncio.Semaphore) -> None:
        self._client     = client
        self._sem        = sem
        self.calls_made  = 0
        self.tokens_used = 0

    async def classify_batch(self, batch: list[dict]) -> list[dict]:
        prompt = _build_prompt(batch)
        async with self._sem:
            resp = await retry_async(
                self._client.chat.completions.create,
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a UAE restaurant market research expert. "
                            "Always respond with valid JSON only, no extra text."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=OPENAI_TEMPERATURE,
                max_tokens=OPENAI_MAX_TOKENS,
                response_format={"type": "json_object"},
                max_retries=DEFAULT_MAX_RETRIES,
                base_delay=DEFAULT_BASE_DELAY,
                cap_delay=DEFAULT_CAP_DELAY,
            )
        self.calls_made  += 1
        self.tokens_used += resp.usage.prompt_tokens + resp.usage.completion_tokens
        return _parse_openai_response(resp.choices[0].message.content, len(batch))


async def stage3_openai_classify(
    df: pd.DataFrame,
    api_needed_ids: list[int],
    tavily_results: dict[int, str],
    pre_classified: dict[int, dict],
    classifier: OpenAIClassifier,
    autosave: AutoSaveManager,
) -> dict[int, dict]:
    """
    Classify all api_needed restaurants in concurrent batches.
    Returns {branch_id: result_dict} for all processed entries.
    """
    id_to_row = {int(r["branch_id"]): r for _, r in df.iterrows()}

    # Filter out already autosaved entries
    to_classify: list[int] = []
    for bid in api_needed_ids:
        if autosave.contains(str(bid)):
            log.debug("Skip (autosaved): branch_id={}", bid)
        else:
            to_classify.append(bid)

    if not to_classify:
        log.info("Stage 3: all already autosaved — nothing to classify")
        return {}

    # Build batches
    batches: list[tuple[list[int], list[dict]]] = []
    buf_ids : list[int]  = []
    buf_data: list[dict] = []

    for bid in to_classify:
        row       = id_to_row[bid]
        partial   = pre_classified.get(bid, {})
        chain_hint = ""
        if partial.get("outlet_type") == "Chain":
            chain_hint = "Chain (≥2 branches in our DB)"

        buf_ids.append(bid)
        buf_data.append({
            "name":       str(row.get("restaurant_name") or ""),
            "cuisines":   _format_cuisines(row),
            "chain_hint": chain_hint,
            "tavily_text": tavily_results.get(bid, ""),
        })
        if len(buf_ids) >= OPENAI_BATCH_SIZE:
            batches.append((buf_ids[:], buf_data[:]))
            buf_ids.clear()
            buf_data.clear()
    if buf_ids:
        batches.append((buf_ids[:], buf_data[:]))

    results: dict[int, dict] = {}
    total_batches = len(batches)
    log.info(
        "Stage 3: {} restaurants → {} batches (batch_size={}, concurrency={})",
        len(to_classify), total_batches, OPENAI_BATCH_SIZE, OPENAI_CONCURRENCY,
    )
    t0 = time.perf_counter()

    t0_stage3 = time.perf_counter()

    async def _process_batch(idx_list: list[int], data_list: list[dict]) -> None:
        batch_res = await classifier.classify_batch(data_list)
        for bid, res in zip(idx_list, batch_res):
            results[bid] = res
            await autosave.add(str(bid), res)
        elapsed = time.perf_counter() - t0_stage3
        done_pct = 100 * classifier.calls_made / total_batches
        rate     = classifier.calls_made / elapsed if elapsed > 0 else 0
        eta_s    = (total_batches - classifier.calls_made) / rate if rate > 0 else 0
        est_cost = (classifier.tokens_used / 1_000_000) * 0.375
        log.info(
            "[OpenAI] batch {}/{} ({:.1f}%) | ETA {:.0f}m{:02d}s | "
            "tokens={:,} | est_cost=${:.3f}",
            classifier.calls_made, total_batches, done_pct,
            int(eta_s // 60), int(eta_s % 60),
            classifier.tokens_used, est_cost,
        )

    tasks = [
        asyncio.create_task(_process_batch(ids, data))
        for ids, data in batches
    ]
    await asyncio.gather(*tasks)

    log.info(
        "Stage 3: done in {:.1f}s | {} calls | {:,} tokens | est cost ~${:.3f}",
        time.perf_counter() - t0,
        classifier.calls_made,
        classifier.tokens_used,
        (classifier.tokens_used / 1_000_000) * 0.375,
    )
    return results


# ── Output builder ────────────────────────────────────────────────────────────

def build_output_csv(
    df: pd.DataFrame,
    pre_classified: dict[int, dict],
    api_results: dict[int, dict],
    autosave_data: dict,
) -> pd.DataFrame:
    """Merge all classification results into a single DataFrame."""
    rows = []
    for _, row in df.iterrows():
        bid  = int(row["branch_id"])
        name = str(row.get("restaurant_name") or "")

        # Priority: API result > autosave > pre-classified (non-partial)
        res: Optional[dict] = None

        # From Stage 3 (just ran)
        if bid in api_results:
            res = api_results[bid]
            # Merge in partial pre-class (outlet_type from chain freq, if API didn't override)
            partial = pre_classified.get(bid, {})
            if partial.get("outlet_type") == "Chain" and res.get("outlet_type") != "Chain":
                res = dict(res)
                res["outlet_type"]           = "Chain"
                res["chained_outlet_type"]   = res.get("chained_outlet_type") or "Local Chain"
            src = partial.get("classification_source") or "tavily+openai"
            if "rule" in src:
                src = f"{src}+openai"
            res["classification_source"] = src

        # From autosave (resumed run)
        elif str(bid) in autosave_data:
            res  = autosave_data[str(bid)]
            partial = pre_classified.get(bid, {})
            src  = partial.get("classification_source") or "tavily+openai"
            res.setdefault("classification_source", src)

        # From Stage 1 (fully pre-classified, no API)
        elif bid in pre_classified and not pre_classified[bid].get("_partial"):
            res = pre_classified[bid]
        else:
            res = {
                "restaurant_type":    "Quick-Service Restaurants",
                "outlet_type":        "Independent",
                "chained_outlet_type": "N/A",
                "confidence":         "Low",
                "classification_source": "fallback",
                "reasoning":          "No classification available",
            }

        # Enforce chained_outlet_type logic
        if res.get("outlet_type") == "Independent":
            res["chained_outlet_type"] = "N/A"

        rows.append({
            "branch_id":             bid,
            "restaurant_name":       name,
            "restaurant_type":       res.get("restaurant_type", "Full-Service Restaurants"),
            "outlet_type":           res.get("outlet_type", "Independent"),
            "chained_outlet_type":   res.get("chained_outlet_type", "N/A"),
            "confidence":            res.get("confidence", "Low"),
            "classification_source": res.get("classification_source", "unknown"),
            "reasoning":             res.get("reasoning", ""),
            "classified_at":         datetime.now(timezone.utc).isoformat(),
        })

    return pd.DataFrame(rows)


# ── Main async pipeline ───────────────────────────────────────────────────────

async def main(limit: Optional[int] = None, test_mode: bool = False) -> None:
    if test_mode:
        limit = limit or 20
    _load_env()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    run_label = "TEST (20)" if test_mode else (f"LIMIT={limit}" if limit else "FULL RUN")
    print(f"\n{'='*60}")
    print(f"  UAE RESTAURANT CLASSIFIER — {run_label}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Tavily concurrency: {TAVILY_CONCURRENCY} | OpenAI concurrency: {OPENAI_CONCURRENCY}")
    print(f"  Batch size: {OPENAI_BATCH_SIZE} | Model: {OPENAI_MODEL}")
    print(f"{'='*60}\n")

    openai_key = os.getenv("OPENAI_API_KEY", "").strip("\"'")
    if not openai_key:
        raise EnvironmentError("OPENAI_API_KEY not set in talabat/tavily/.env")

    # ── Load data ─────────────────────────────────────────────────────────────
    df = await load_restaurants(env_path=TALABAT_ENV, limit=limit)
    if df.empty:
        log.error("No restaurants loaded — check Supabase credentials")
        return

    # ── Stage 1 — Rule-based ─────────────────────────────────────────────────
    log.info("=== Stage 1: Rule-based pre-classification ===")
    pre_classified, api_needed_ids = stage1_preclassify(df)
    fully_done = sum(1 for v in pre_classified.values() if not v.get("_partial"))
    log.info(
        "Stage 1 result: {} fully done | {} need API (out of {} total)",
        fully_done, len(api_needed_ids), len(df),
    )

    # ── Stage 2 — Tavily search ───────────────────────────────────────────────
    log.info("=== Stage 2: Tavily web search ===")
    rotator    = TavilyKeyRotator.from_csv(KEYS_CSV, timeout=TAVILY_TIMEOUT_S)
    tavily_sem = asyncio.Semaphore(TAVILY_CONCURRENCY)
    cache_lock = asyncio.Lock()
    tavily_cache: dict = _load_json(TAVILY_CACHE_F)
    log.info("Tavily cache: {} entries pre-loaded", len(tavily_cache))

    tavily_results = await stage2_tavily_search(
        df, api_needed_ids, rotator, tavily_sem, tavily_cache, cache_lock
    )

    # ── Stage 3 — OpenAI classification ──────────────────────────────────────
    log.info("=== Stage 3: OpenAI GPT-4o-mini classification ===")
    oai_client = AsyncOpenAI(api_key=openai_key)
    oai_sem    = asyncio.Semaphore(OPENAI_CONCURRENCY)
    clf        = OpenAIClassifier(oai_client, oai_sem)

    async with AutoSaveManager(AUTOSAVE_F, flush_every=50) as autosave:
        api_results   = await stage3_openai_classify(
            df, api_needed_ids, tavily_results, pre_classified, clf, autosave
        )
        autosave_data = autosave.all_items()

    # ── Build + save CSV ──────────────────────────────────────────────────────
    log.info("=== Building output CSV ===")
    out_df = build_output_csv(df, pre_classified, api_results, autosave_data)

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"classified_{ts}.csv"
    out_df.to_csv(out_path, index=False, encoding="utf-8-sig")

    size_kb = out_path.stat().st_size / 1024
    log.info("Saved {} rows → {} ({:.0f} KB)", len(out_df), out_path, size_kb)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"  CLASSIFICATION COMPLETE  ({len(out_df):,} restaurants)")
    print("=" * 60)
    print(f"\n  restaurant_type distribution:")
    for k, v in out_df["restaurant_type"].value_counts().items():
        print(f"    {k:<35} {v:>6,}")
    print(f"\n  outlet_type distribution:")
    for k, v in out_df["outlet_type"].value_counts().items():
        print(f"    {k:<35} {v:>6,}")
    print(f"\n  chained_outlet_type distribution:")
    for k, v in out_df["chained_outlet_type"].value_counts().items():
        print(f"    {k:<35} {v:>6,}")
    print(f"\n  confidence distribution:")
    for k, v in out_df["confidence"].value_counts().items():
        print(f"    {k:<35} {v:>6,}")
    print(f"\n  source distribution:")
    for k, v in out_df["classification_source"].value_counts().items():
        print(f"    {k:<35} {v:>6,}")
    print(f"\n  OpenAI: {clf.calls_made} calls | {clf.tokens_used:,} tokens | "
          f"~${(clf.tokens_used / 1_000_000) * 0.375:.3f}")
    print(f"  Tavily: {rotator.searches_made} searches | {rotator.rotations} key rotations")
    print(f"\n  Output: {out_path}")
    print("=" * 60)


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Classify UAE restaurants from Supabase",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python classify_from_supabase.py --test         # 20 restaurants\n"
            "  python classify_from_supabase.py --limit 200    # 200 restaurants\n"
            "  python classify_from_supabase.py                # all 15,532\n"
        ),
    )
    p.add_argument("--test",   action="store_true", help="Run with 20 restaurants (smoke test)")
    p.add_argument("--limit",  type=int, default=None, help="Process only N restaurants")
    p.add_argument("--resume", action="store_true", help="Alias for default (autosave always resumes)")
    return p.parse_args()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    asyncio.run(main(limit=args.limit, test_mode=args.test))
