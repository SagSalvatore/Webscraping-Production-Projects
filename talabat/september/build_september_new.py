"""Final September new-listing set: apply the drops, verify, emit the review CSV.

INPUT   sept_full_2026-09-08_area_corrected.jsonl   1,170 rows with real areas
DROPS   1  same VENUE   - two September rows, same name, within 500 m
        2  same RESTAURANT - name >= 88 against July/August export AND the
                             cuisine set is IDENTICAL (Sagar's rule: any
                             difference at all means a different restaurant)
OUTPUT  september_new_listings_202609.csv  - url + name so each can be opened
                                             and checked by hand

The reference is the SOURCE exports, not unified.jsonl: unified has been through
"NA" normalisation, city kNN fills, Arabic fixes and label-conflict resolution,
so comparing to it compares against our own processing rather than the data.

    python build_september_new.py --dry-run
    python build_september_new.py
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import ijson
import orjson
from rapidfuzz import fuzz, process

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "listing_comparison"))
from common import norm_name                                    # noqa: E402

AREA = ROOT / "listing_comparison" / "output" / \
    "sept_full_2026-09-08_area_corrected.jsonl"
CONF = ROOT / "Restaurant Identifier" / "data" / "restaurants_confirmed_sep.jsonl"
RAW = ROOT / "data" / "urls" / "talabat_restaurant_urls_sep.jsonl"
EXPORTS = [("july", ROOT / "export" / "talabat_export.json"),
           ("august", ROOT / "August_menu" / "data" / "August_export.json")]
OUT_CSV = HERE / "data" / "september_new_listings_202609.csv"
OUT_DROP = HERE / "data" / "september_dropped_202609.csv"
MAX_KM = 0.5
FUZZ_MIN = 88
sys.stdout.reconfigure(encoding="utf-8")


def km(a, b):
    try:
        la1, lo1, la2, lo2 = map(radians, (float(a[0]), float(a[1]),
                                           float(b[0]), float(b[1])))
    except (TypeError, ValueError):
        return None
    h = sin((la2-la1)/2)**2 + cos(la1)*cos(la2)*sin((lo2-lo1)/2)**2
    return 6371.0 * 2 * asin(sqrt(h))


def cset(v):
    if not v:
        return set()
    if isinstance(v, str):
        return {c.strip().lower() for c in v.split(",") if c.strip()}
    return {str(x).strip().lower() for x in v if str(x).strip()}


def main(args):
    print("=" * 78)
    print("  BUILD SEPTEMBER NEW LISTINGS")
    print("=" * 78)

    rows = [orjson.loads(l) for l in open(AREA, "rb")]
    print(f"  area-corrected rows : {len(rows):,}")

    cui, url = {}, {}
    for line in open(CONF, encoding="utf-8"):
        try:
            r = orjson.loads(line)
        except Exception:
            continue
        cui[str(r.get("branch_id"))] = cset(r.get("serves_cuisine"))
    for line in open(RAW, encoding="utf-8"):
        r = orjson.loads(line)
        url[str(r["branch_id"])] = (r.get("url"), r.get("area_name"),
                                    r.get("scraped_at"))

    drops = {}

    # ---- drop 1: two September rows are the same venue -------------------
    by_name = defaultdict(list)
    for r in rows:
        by_name[norm_name(r.get("name"))].append(r)
    for k, v in by_name.items():
        if not k or len(v) < 2:
            continue
        for i in range(len(v)):
            for j in range(i + 1, len(v)):
                d = km((v[i].get("lat"), v[i].get("lon")),
                       (v[j].get("lat"), v[j].get("lon")))
                if d is not None and d <= MAX_KM:
                    # keep the LOWER branch_id - it is the original listing
                    loser = max(v[i], v[j], key=lambda x: int(x["branch_id"]))
                    keep = min(v[i], v[j], key=lambda x: int(x["branch_id"]))
                    drops[str(loser["branch_id"])] = (
                        "same venue as branch "
                        f"{keep['branch_id']} ({d*1000:.0f} m apart)")

    # ---- drop 2: already in July/August, name + IDENTICAL cuisine --------
    names, ecui, esrc, esid = [], defaultdict(set), {}, {}
    for label, p in EXPORTS:
        with open(p, "rb") as f:
            for rec in ijson.items(f, "item"):
                k = norm_name(rec.get("name"))
                if not k:
                    continue
                if k not in ecui:
                    names.append(k)
                    esrc[k], esid[k] = label, rec.get("source_id")
                ecui[k] |= cset(rec.get("key_cuisines"))
    print(f"  export brand names  : {len(names):,}  (July + August)")

    keys = [norm_name(r.get("name")) for r in rows]
    res = process.cdist(keys, names, scorer=fuzz.token_sort_ratio,
                        score_cutoff=FUZZ_MIN, workers=-1)
    for i, row in enumerate(res):
        bj, bs = -1, 0
        for j, s in enumerate(row):
            if s > bs:
                bs, bj = s, j
        if bj < 0:
            continue
        k2 = names[bj]
        mine = cui.get(str(rows[i]["branch_id"]), set())
        if mine and mine == ecui[k2]:
            drops.setdefault(str(rows[i]["branch_id"]),
                             f"already in {esrc[k2]} export as "
                             f"'{k2}' (source_id {esid[k2]}), name {bs:.0f}, "
                             f"identical cuisine")

    kept = [r for r in rows if str(r["branch_id"]) not in drops]
    print(f"\n  dropped : {len(drops)}")
    for b, why in drops.items():
        nm = next((r.get("name") for r in rows if str(r["branch_id"]) == b), "?")
        print(f"     {b:<10} {str(nm)[:34]:36} {why[:60]}")
    print(f"  KEPT    : {len(kept):,}")

    # ---- final assertion: none of the kept rows is in either export -------
    eids = set()
    for _, p in EXPORTS:
        with open(p, "rb") as f:
            for rec in ijson.items(f, "item"):
                if rec.get("source_id") is not None:
                    eids.add(str(rec["source_id"]))
    clash = [r for r in kept if str(r["branch_id"]) in eids]
    print(f"\n  ASSERT kept branch_id not an export source_id : "
          f"{'PASS' if not clash else f'FAIL ({len(clash)})'}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    OUT_CSV.parent.mkdir(exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["branch_id", "restaurant_id", "name", "area_name_real",
                    "crawl_zone", "cuisines", "lat", "lon", "url", "scraped_at"])
        for r in sorted(kept, key=lambda x: str(x.get("area_name_real") or "")):
            b = str(r["branch_id"])
            u, zone, ts = url.get(b, ("", "", ""))
            w.writerow([b, r.get("restaurant_id"), r.get("name"),
                        r.get("area_name_real"), zone,
                        ", ".join(sorted(cui.get(b, set()))),
                        r.get("lat"), r.get("lon"), u, ts])
    with open(OUT_DROP, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["branch_id", "name", "reason", "url"])
        for b, why in drops.items():
            nm = next((r.get("name") for r in rows if str(r["branch_id"]) == b), "")
            w.writerow([b, nm, why, url.get(b, ("",))[0]])
    print(f"\n  -> {OUT_CSV.name}   ({len(kept):,} rows)")
    print(f"  -> {OUT_DROP.name}  ({len(drops)} rows)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
