"""Stage 4 - gpt-4.1-mini decides restaurant_type and outlet_type.

Async, batched 30 per request. Usage tier 2 allows 4,300 RPM; 223 batches at
concurrency 12 uses a tiny fraction of that, so the model is never the
bottleneck.

WHAT THE MODEL IS AND IS NOT ASKED:
  asked     restaurant_type, outlet_type, and - only when Chain - whether the
            chain is Local or MNC.
  NOT asked chained_outlet_type for Independents. That is DERIVED as "N/A".
            Letting the model emit it free-form is what produced 'N/A ' x135
            and 'Local Chain ' x4 in the previous output.

Every returned value is validated against the allowed sets and coerced:
  * unknown restaurant_type -> falls back to the rule hint, else Low confidence
  * outlet_type disagreeing with Google Maps evidence is OVERRIDDEN by the
    evidence, because the client's standard is what Maps shows, not what a
    model infers from a name.

    python classify_llm.py --dry-run        cost estimate, no calls
    python classify_llm.py --limit 60       small live test
    python classify_llm.py                  full run
"""
import argparse
import asyncio
import csv
import json
import os
import sys
import time
from collections import Counter

from dotenv import load_dotenv
from loguru import logger

import config as C
from rules import norm_name

load_dotenv(C.ROOT / ".env", override=True)

CHECKPOINT_EVERY = 10          # batches


def load_json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_cache(c):
    tmp = C.LLM_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    tmp.replace(C.LLM_CACHE)


def build_payload(h, foot, tav):
    """One compact line per restaurant. Keep it short - it is the token bill."""
    fp = foot.get(h["name"], {})
    loc = fp.get("uae_locations", 0)
    # `capped` means SERPER returned 10 RAW results - Google's page limit - not
    # that 10 of them were this brand. Annotating a brand whose MATCHED count is
    # 0 with "(>=10, capped)" tells the model the opposite of the truth: 10
    # businesses came back and none of them was this one is evidence of ABSENCE.
    # On September all 297 capped brands had a matched count below 10 and 241 of
    # them matched nothing, and `coerce` has no override for loc == 0 - the
    # model's answer stands there - so the annotation was the only signal and it
    # pointed the wrong way. It is only meaningful once the matched count itself
    # reaches the cap.
    cap = " (>=10, capped)" if fp.get("capped") and loc >= 10 else ""
    bits = [f'id={h["branch_id"]}', f'name="{h["name"]}"',
            f'google_locations_uae={loc}{cap}']
    if h["cuisines"]:
        bits.append("cuisines=" + ",".join(h["cuisines"][:6]))
    if h["taxonomy_share"]:
        bits.append("menu=" + ",".join(f"{k}:{v:.0%}"
                                       for k, v in h["taxonomy_share"].items()))
    if h["top_terms"]:
        bits.append("top_items=" + ",".join(h["top_terms"][:4]))
    if h["cuisine_hint"]:
        bits.append(f'rule_hint={h["cuisine_hint"]}')
    elif h["menu_hint"]:
        bits.append(f'menu_hint={h["menu_hint"]}')
    t = (tav.get(h["name"]) or {}).get("text")
    if t:
        bits.append("web=" + t[:300].replace("\n", " "))
    return " | ".join(bits)


def coerce(rec, h, foot):
    """Validate + derive. Google Maps evidence wins over the model on chains."""
    notes = []
    rt = (rec.get("restaurant_type") or "").strip()
    if rt not in C.VALID_TYPE:
        rt = h.get("cuisine_hint") or h.get("menu_hint") or ""
        notes.append("type_fallback_to_rule" if rt else "type_invalid")

    fp = foot.get(h["name"], {})
    loc = fp.get("uae_locations", 0)
    ot = (rec.get("outlet_type") or "").strip()
    if ot not in C.VALID_OUTLET:
        ot = fp.get("outlet_type") or "Independent"
        notes.append("outlet_from_evidence")
    # the client's standard is the Maps footprint - override disagreement
    if loc >= C.CHAIN_MIN_LOCATIONS and ot != "Chain":
        ot, _ = "Chain", notes.append("outlet_overridden_by_maps")
    elif loc < C.CHAIN_MIN_LOCATIONS and ot != "Independent":
        # SAGAR, SEPT 2026 - FORWARD ONLY, August's shipped numbers stand.
        # The Maps count decides, with no escape hatch:
        #     >=2 distinct UAE locations -> Chain
        #      0 or 1                    -> Independent
        # The previous version kept the model's Chain at loc == 1 whenever it
        # had also filled in a sub-type - but the model fills that in whenever
        # it says Chain, so the gate never rejected anything. Its comment said
        # "if the web says so"; no web evidence was ever fetched for those
        # brands, since Tavily only runs for footprints of >=2. On September it
        # produced 109 single-pin "chains" out of 158, none with any evidence.
        ot, _ = "Independent", notes.append("outlet_overridden_maps_below_min")

    origin = (rec.get("country_of_origin") or "").strip()
    # Sagar's rule: branches in ANY other country => MNC. Read "multinational"
    # literally - a UAE brand that expanded into Saudi is multinational.
    outside = bool(rec.get("operates_outside_uae"))

    if ot == "Independent":
        ct = "N/A"                                   # DERIVED, never asked
    else:
        # THREE GATES. Google Maps proves "Chain"; it says nothing about
        # multinational, so MNC is decided separately and conservatively.
        ct = (rec.get("chained_outlet_type") or "").strip()
        if norm_name(h["name"]) in C.MNC_SEED:
            ct = "MNC Chain"                          # gate 1+2: known brand
            notes.append("mnc_from_seed")
        elif outside:
            # GATE 3 IS REVIEW-ONLY from September 2026 (Sagar). It used to set
            # MNC Chain outright on the model's `operates_outside_uae`. Measured
            # across two months: 11 promotions, 1 upheld (Kyan Cafe). September's
            # GRATEFUL CAFE came with the reasoning "Cafe Gratitude is a US-based
            # chain" - a different brand entirely - and still reported origin and
            # foreign operations as facts.
            #
            # Sagar's test is PROMINENCE: "MNC would be the one's which are
            # prominent brands like KFC, mcdonalds". A brand nobody recognises is
            # a local chain however the model answers, so the default flips to
            # Local Chain and the claim goes to a review file instead. The seed
            # list above is untouched and still decides real multinationals.
            ct = "Local Chain"
            notes.append("mnc_claim_pending_review")
        elif ct == "MNC Chain":
            # model said MNC but neither gate agrees - unfamiliar brands are
            # local far more often than multinational, so demote it.
            ct = "Local Chain"
            notes.append("mnc_demoted_no_evidence")
        elif ct not in ("Local Chain", "MNC Chain"):
            ct = "Local Chain"                        # documented default
            notes.append("chain_default_local")

    # human-reviewed override beats every gate
    ov = C.CHAIN_TYPE_OVERRIDE.get(norm_name(h["name"]))
    if ov and ot == "Chain" and ct != ov:
        ct = ov
        notes.append("chain_type_manual_override")

    conf = (rec.get("confidence") or "").strip().title()
    return {
        "restaurant_type": rt, "outlet_type": ot, "chained_outlet_type": ct,
        "confidence": conf if conf in ("High", "Medium", "Low") else "Low",
        "reasoning": (rec.get("reasoning") or "")[:160],
        "google_locations_uae": loc,
        "country_of_origin": origin or "unknown",
        "operates_outside_uae": outside,
        "coercions": ";".join(n for n in notes if n),
    }


async def do_batch(client, batch, hints, foot, tav, cache, stats, sem):
    payload = "\n".join(build_payload(hints[b], foot, tav) for b in batch)
    for attempt in range(5):
        try:
            async with sem:
                r = await client.chat.completions.create(
                    model=C.OPENAI_MODEL, temperature=0, max_tokens=2600,
                    messages=[{"role": "system", "content": C.SYSTEM_PROMPT},
                              {"role": "user", "content":
                               f"Classify these {len(batch)} UAE restaurants:\n{payload}"}])
            txt = r.choices[0].message.content or ""
            stats["prompt_tokens"] += r.usage.prompt_tokens
            stats["completion_tokens"] += r.usage.completion_tokens
            got = 0
            for line in txt.splitlines():
                line = line.strip().strip("`")
                if not line.startswith("{"):
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    stats["bad_json_line"] += 1
                    continue
                bid = rec.get("id")
                if bid in hints:
                    cache[str(bid)] = coerce(rec, hints[bid], foot)
                    got += 1
            stats["classified"] += got
            missing = len(batch) - got
            if missing:
                stats["missing_in_reply"] += missing
            return
        except Exception as exc:
            stats[type(exc).__name__] += 1
            await asyncio.sleep(2 * (2 ** attempt))
    stats["failed_batches"] += 1


async def main(args):
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
    logger.add(C.LOGS / "classify.log", level="DEBUG", encoding="utf-8", rotation="10 MB")

    import rules
    hints = rules.build()
    foot = load_json(C.PLACES_OUT, {})
    tav = load_json(C.TAVILY_CACHE, {})
    logger.info(f"restaurants {len(hints):,} | serper brands {len(foot):,} "
                f"| tavily notes {len(tav):,}")
    if not foot:
        logger.warning("no Serper footprint - outlet_type will be guesswork. "
                       "Run serper_places.py first.")

    cache = load_json(C.LLM_CACHE, {})
    todo = [b for b in hints if str(b) not in cache]
    if args.limit:
        todo = todo[: args.limit]
        logger.warning(f"--limit: {len(todo)} restaurants")
    batches = [todo[i:i + C.OPENAI_BATCH] for i in range(0, len(todo), C.OPENAI_BATCH)]
    est_in = len(batches) * 5300
    est_out = len(batches) * 1800
    cost = est_in / 1e6 * 0.40 + est_out / 1e6 * 1.60
    logger.info(f"cached {len(hints)-len(todo):,} | to classify {len(todo):,} "
                f"in {len(batches):,} batches of {C.OPENAI_BATCH}")
    logger.info(f"est cost ~${cost:.2f} ({C.OPENAI_MODEL} @ $0.40/1M in, $1.60/1M out)")

    if args.dry_run:
        logger.warning("--dry-run: no API calls")
        if batches:
            logger.info("sample payload line:\n  " + build_payload(hints[batches[0][0]], foot, tav)[:400])
        return 0
    if not os.getenv("OPEN_AI_API"):
        logger.error("OPEN_AI_API missing from talabat/.env")
        return 1

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.environ["OPEN_AI_API"])
    sem = asyncio.Semaphore(C.OPENAI_CONCURRENCY)
    stats = Counter()
    t0 = time.time()
    try:
        for i in range(0, len(batches), C.OPENAI_CONCURRENCY):
            chunk = batches[i:i + C.OPENAI_CONCURRENCY]
            await asyncio.gather(*(do_batch(client, b, hints, foot, tav, cache, stats, sem)
                                   for b in chunk))
            if (i // C.OPENAI_CONCURRENCY) % CHECKPOINT_EVERY == 0:
                save_cache(cache)
                done = min(i + C.OPENAI_CONCURRENCY, len(batches))
                logger.info(f"  {done}/{len(batches)} batches | "
                            f"classified {stats['classified']:,}")
    finally:
        save_cache(cache)

    spend = (stats["prompt_tokens"] / 1e6 * 0.40 +
             stats["completion_tokens"] / 1e6 * 1.60)
    logger.success(f"LLM done in {(time.time()-t0)/60:.1f} min | "
                   f"ACTUAL cost ${spend:.2f}")
    for k in ("classified", "missing_in_reply", "bad_json_line", "failed_batches"):
        if stats[k]:
            logger.info(f"    {k:20} {stats[k]:,}")
    write_final(hints, cache, foot)
    return 0


def write_final(hints, cache, foot):
    rows, miss = [], 0
    for bid, h in hints.items():
        v = cache.get(str(bid))
        # THE MAPS COUNT IS RE-ASSERTED HERE, not only in coerce(). The cache
        # stores the already-coerced record, so a regenerate never calls
        # coerce() again - which is exactly how a rule change would appear to
        # do nothing. Same reason CHAIN_TYPE_OVERRIDE is applied in both.
        if v:
            loc = foot.get(h["name"], {}).get("uae_locations", 0)
            want = "Chain" if loc >= C.CHAIN_MIN_LOCATIONS else "Independent"
            if v.get("outlet_type") != want:
                v = {**v, "outlet_type": want,
                     "chained_outlet_type": ("N/A" if want == "Independent"
                                             else (v.get("chained_outlet_type")
                                                   if v.get("chained_outlet_type")
                                                   in ("Local Chain", "MNC Chain")
                                                   else "Local Chain")),
                     "coercions": ";".join(filter(None, [
                         v.get("coercions", ""), "outlet_from_maps_count"]))}
        if v and v.get("outlet_type") == "Chain":
            # ONLY THE SEED LIST OR A HUMAN MAY SET MNC - re-asserted here as an
            # invariant, not just in coerce(), because the cache stores records
            # coerced under whatever rule was live when they were written. A
            # cached "mnc_outside_uae" from before gate 3 went review-only would
            # otherwise survive a regenerate untouched.
            ov = C.CHAIN_TYPE_OVERRIDE.get(norm_name(h["name"]))
            if (v.get("chained_outlet_type") == "MNC Chain"
                    and norm_name(h["name"]) not in C.MNC_SEED
                    and ov != "MNC Chain"):
                v = {**v, "chained_outlet_type": "Local Chain",
                     "coercions": ";".join(filter(None, [
                         v.get("coercions", ""), "mnc_claim_pending_review"]))}
            if ov and v.get("chained_outlet_type") != ov:
                v = {**v, "chained_outlet_type": ov,
                     "coercions": ";".join(filter(None, [v.get("coercions", ""),
                                                         "chain_type_manual_override"]))}
        if not v:
            miss += 1
            v = {"restaurant_type": h.get("cuisine_hint") or h.get("menu_hint") or "",
                 "outlet_type": foot.get(h["name"], {}).get("outlet_type", "Independent"),
                 "chained_outlet_type": "", "confidence": "Low",
                 "reasoning": "not classified", "google_locations_uae":
                 foot.get(h["name"], {}).get("uae_locations", 0),
                 "country_of_origin": "unknown", "operates_outside_uae": False,
                 "coercions": "unclassified"}
            if v["outlet_type"] == "Independent":
                v["chained_outlet_type"] = "N/A"
            else:
                v["chained_outlet_type"] = "Local Chain"
        rows.append({
            "branch_id": bid, "restaurant_id": h["restaurant_id"],
            "restaurant_name": h["name"], "area_name": h["area_name"],
            "cuisines": ", ".join(h["cuisines"]),
            **{k: v[k] for k in ("restaurant_type", "outlet_type",
                                 "chained_outlet_type", "confidence",
                                 "reasoning", "google_locations_uae",
                                 "country_of_origin", "operates_outside_uae",
                                 "coercions")},
        })

    # invariants - the whole point of deriving chained_outlet_type
    bad = [r for r in rows if r["outlet_type"] == "Independent"
           and r["chained_outlet_type"] != "N/A"]
    assert not bad, f"{len(bad)} Independents without N/A"
    bad2 = [r for r in rows if r["outlet_type"] == "Chain"
            and r["chained_outlet_type"] not in ("Local Chain", "MNC Chain")]
    assert not bad2, f"{len(bad2)} Chains without a sub-type"
    for r in rows:
        for k in ("restaurant_type", "outlet_type", "chained_outlet_type"):
            assert r[k] == r[k].strip(), f"whitespace in {k}: {r[k]!r}"

    with open(C.FINAL_OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(C.FINAL_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), quoting=csv.QUOTE_ALL)
        w.writeheader(); w.writerows(rows)

    # GATE-3 REVIEW QUEUE. These ship as Local Chain; the file exists so the
    # claim is not lost. `reasoning` is included deliberately - it is the only
    # place a brand-confusion fabrication is visible ("Cafe Gratitude is a
    # US-based chain", returned for GRATEFUL CAFE). Promote by adding to
    # config.CHAIN_TYPE_OVERRIDE and regenerating - that costs $0.00.
    claims = [r for r in rows
              if "mnc_claim_pending_review" in (r.get("coercions") or "")
              or "mnc_outside_uae" in (r.get("coercions") or "")]
    with open(C.MNC_REVIEW, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["restaurant_name", "branch_id", "google_locations_uae",
                    "shipped_as", "model_country_of_origin",
                    "model_operates_outside_uae", "model_reasoning",
                    "verdict_MNC_or_Local", "notes"])
        for r in claims:
            w.writerow([r["restaurant_name"], r["branch_id"],
                        r["google_locations_uae"], r["chained_outlet_type"],
                        r["country_of_origin"], r["operates_outside_uae"],
                        r["reasoning"], "", ""])
    if claims:
        logger.warning(f"   {len(claims)} MNC claim(s) shipped as Local Chain "
                       f"pending review -> {C.MNC_REVIEW.name}")

    rep = {
        "restaurants": len(rows), "unclassified_fallback": miss,
        "mnc_claims_pending_review": len(claims),
        "restaurant_type": dict(Counter(r["restaurant_type"] for r in rows)),
        "outlet_type": dict(Counter(r["outlet_type"] for r in rows)),
        "chained_outlet_type": dict(Counter(r["chained_outlet_type"] for r in rows)),
        "confidence": dict(Counter(r["confidence"] for r in rows)),
        "coercions": dict(Counter(c for r in rows for c in r["coercions"].split(";") if c)),
        "mnc_origins": dict(Counter(r["country_of_origin"] for r in rows
                                    if r["chained_outlet_type"] == "MNC Chain")),
        "model": C.OPENAI_MODEL,
    }
    C.REPORT_OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.success(f"-> {C.FINAL_OUT.name} / {C.FINAL_CSV.name} ({len(rows):,} rows)")
    for k in ("restaurant_type", "outlet_type", "chained_outlet_type", "confidence"):
        logger.info(f"   {k:20} {rep[k]}")
    if rep["coercions"]:
        logger.info(f"   coercions applied  {rep['coercions']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--cohort", default="aug",
                   help="aug | sep - consumed by config at import time, "
                        "declared here only so argparse accepts it")
    sys.exit(asyncio.run(main(p.parse_args())))
