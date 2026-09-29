"""Area sanitization pipeline for ri-db.restaurants_id.json.

Stages
  1. deterministic rules            (~78% of rows, no API cost)
  2. gpt-5-mini on the residue      (distinct values only, batched, cached)
  3. address fallback               (restaurant_locations.json / maping_address.csv)
  4. whatever is still unknown      -> manual_review_needed.csv

Writes NOTHING back to the input file. All output lands in output/.

  python run_clean.py --dry-run          rules only, no API spend
  python run_clean.py --limit 300        test on 300 records (real API)
  python run_clean.py                    full run (17,164)
"""
import argparse
import asyncio
import csv
import re
import sys
from collections import Counter

import orjson
from loguru import logger

from clean_rules import (area_from_address, build_vocabulary, clean_area)
from config import INPUT_JSON, LOG_DIR, MAPPING_CSV, OUTPUT_DIR, ROOT

sys.stdout.reconfigure(encoding="utf-8")
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
logger.add(LOG_DIR / "run_clean.log", level="DEBUG", encoding="utf-8", rotation="20 MB")

LOCATIONS_JSON = ROOT.parent / "restaurant_locations.json"


def load_fallback_sources():
    """address lookups, joined PER BRANCH.

    NB: talabat_restaurants (postgres) is deliberately NOT used - it holds ~1
    row per listing (15,768) against 17,164 branch rows, so a branch_id join
    returns the same area for every branch of a chain (all 31 Peet's Coffee
    branches came back 'Al Barsha 3'). It cannot disambiguate branches.
    """
    by_branch = {}
    try:
        for r in orjson.loads(LOCATIONS_JSON.read_bytes()):
            by_branch[(r["source_id"], (r.get("area") or "").strip())] = r.get("address")
        logger.info(f"restaurant_locations.json: {len(by_branch):,} branch addresses")
    except Exception as exc:
        logger.warning(f"restaurant_locations.json unavailable: {exc}")

    by_sid = {}
    try:
        with open(MAPPING_CSV, encoding="utf-8", errors="replace", newline="") as f:
            for row in csv.DictReader(f):
                row = {k.strip(): (v.strip() if isinstance(v, str) else v)
                       for k, v in row.items()}          # header has stray spaces
                addr = row.get("address") or ""
                addr = re.sub(r"(?:\?\s*){2,}", "", addr)   # Arabic destroyed on save
                addr = re.sub(r"(?:\s*-\s*){2,}", " - ", addr).strip(" -,")
                if addr:
                    by_sid[row["branch_id"]] = addr
        logger.info(f"maping_address.csv: {len(by_sid):,} addresses")
    except Exception as exc:
        logger.warning(f"maping_address.csv unavailable: {exc}")
    return by_branch, by_sid


async def main(args):
    records = orjson.loads(INPUT_JSON.read_bytes())
    if args.limit:
        records = records[:args.limit]
        logger.warning(f"TEST MODE - first {len(records):,} records only")
    logger.info(f"loaded {len(records):,} records")

    raw_areas = [((o.get("location") or {}).get("area") or "").strip() for o in records]
    vocab, doc_freq = build_vocabulary(raw_areas)
    logger.info(f"vocabulary: {len(vocab):,} UAE area names (data-derived)")

    # ---- stage 1: rules ----
    resolved, methods = {}, Counter()
    for a in set(raw_areas):
        val, method = clean_area(a, vocab, doc_freq)
        resolved[a] = (val, method)
        methods[method] += 1
    need_llm = sorted({a for a, (v, _) in resolved.items() if v is None and a})
    rows_need = sum(1 for a in raw_areas if resolved[a][0] is None)
    logger.info(f"rules: {len(raw_areas)-rows_need:,}/{len(raw_areas):,} rows resolved "
                f"({(len(raw_areas)-rows_need)/len(raw_areas)*100:.1f}%)")
    logger.info(f"       {len(need_llm):,} distinct values need the LLM "
                f"({rows_need:,} rows)")

    # ---- stage 2: LLM ----
    llm = {}
    if need_llm and not args.dry_run:
        from llm_client import resolve_areas
        llm = await resolve_areas(need_llm)
    elif args.dry_run:
        logger.warning("--dry-run: skipping the LLM stage (no API spend)")

    # ---- stage 3+4: assemble ----
    addr_branch, addr_sid = load_fallback_sources()
    out_rows, manual, src = [], [], Counter()

    for o in records:
        loc = o.get("location") or {}
        original = (loc.get("area") or "").strip()
        sid = o.get("source_id")
        area, method = resolved.get(original, (None, "empty"))
        conf = 0.0
        if area:
            src[f"rule:{method}"] += 1
            conf = 1.0
        else:
            hit = llm.get(original) or {}
            if hit.get("area"):
                area, conf = hit["area"], hit.get("confidence")
                src["llm"] += 1
                method = "llm"
            else:
                addr = addr_branch.get((sid, original)) or addr_sid.get(sid)
                guess, tier = area_from_address(addr, vocab, doc_freq)
                if tier == "A":
                    area, method, conf = guess, "address_fallback", 0.7
                    src["address_fallback"] += 1
                else:
                    method, conf = "unresolved", 0.0
                    src["unresolved"] += 1
                    manual.append((o, guess, addr))

        out_rows.append({
            "_id": (o.get("_id") or {}).get("$oid"),
            "mordor_restaurant_id": (o.get("mordor_restaurant_id") or {}).get("$oid"),
            "source_id": sid,
            "name": o.get("name"),
            "chain_id": o.get("chain_id"),
            "city": loc.get("city"),
            "sublocality": loc.get("sublocality"),
            "area_original": original,
            "area_2": area,
            "method": method,
            "confidence": conf,
        })

    # ---- write ----
    tag = f"_test{args.limit}" if args.limit else ""
    j = OUTPUT_DIR / f"area_cleaned{tag}.json"
    j.write_bytes(orjson.dumps(out_rows, option=orjson.OPT_INDENT_2))
    c = OUTPUT_DIR / f"area_cleaned{tag}.csv"
    with open(c, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    if manual:
        m = OUTPUT_DIR / f"manual_review_needed{tag}.csv"
        with open(m, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["source_id", "restaurant_name", "chain_id", "city",
                        "original_area", "sublocality", "weak_guess",
                        "address_seen", "mordor_restaurant_id", "_id"])
            for o, guess, addr in manual:
                l = o.get("location") or {}
                w.writerow([o.get("source_id"), o.get("name"), o.get("chain_id"),
                            l.get("city"), l.get("area"), l.get("sublocality"),
                            guess, addr,
                            (o.get("mordor_restaurant_id") or {}).get("$oid"),
                            (o.get("_id") or {}).get("$oid")])
        logger.info(f"manual review -> {m.name} ({len(manual):,} rows)")

    filled = sum(1 for r in out_rows if r["area_2"])
    logger.success(f"{filled:,}/{len(out_rows):,} rows have area_2 "
                   f"({filled/len(out_rows)*100:.1f}%)")
    for k, v in src.most_common():
        logger.info(f"    {k:22} {v:>7,}")
    logger.info(f"distinct areas: {len({r['area_original'] for r in out_rows}):,} in "
                f"-> {len({r['area_2'] for r in out_rows if r['area_2']}):,} out")
    logger.info(f"wrote {j.name} and {c.name}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, help="only process the first N records")
    p.add_argument("--dry-run", action="store_true", help="rules only, no API spend")
    asyncio.run(main(p.parse_args()))
