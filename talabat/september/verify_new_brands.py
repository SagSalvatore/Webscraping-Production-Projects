"""Verify September's genuinely_new_brand rows are actually new.

TWO CHECKS, both of which must pass before anything ships.

  A  INTERNAL   no two September rows are the same venue
                - branch_id exact
                - name fuzzy, guarded by DISTANCE

  B  vs TECH    no September row is already in the delivered unified file
                - source_id (== branch_id) exact
                - name fuzzy

WHY orjson AND NOT ijson. talabat_unified_202608.jsonl is JSON *Lines* - one
object per line - so it is read line by line and each line parsed whole. ijson
streams a single large NESTED document and would give nothing here. orjson is
the right tool for the shape we actually have; the 620 MB file reads in one
pass at constant memory because we only keep source_id, name and geo.

WHY A NAME MATCH NEEDS A DISTANCE GUARD. Talabat appends the branch location to
the brand ("Curry Chatti, Muwaileh Commercial"), so brand-level names repeat
legitimately across the country. An unguarded name/slug rule once matched an
Abu Dhabi venue to one 155 km away in RAK at 0.99 confidence. And a single
shared generic word is not identity: a sample once showed 376 of 679 platform
matches were unrelated businesses sharing one common word. So:

    same name + within  MAX_KM  ->  DUPLICATE, report it
    same name + far apart       ->  a different branch, which is expected

    python verify_new_brands.py
"""
import csv
import sys
from collections import defaultdict
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import orjson
from rapidfuzz import fuzz

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "listing_comparison"))
from common import norm_name                                    # noqa: E402

SEPT = ROOT / "listing_comparison" / "output" / \
    "sept_full_2026-09-08_identity_classification.csv"
UNIFIED = ROOT / "unified" / "data" / "talabat_unified_202608.jsonl"

FUZZ_MIN = 90        # name similarity to call two rows the same brand
MAX_KM = 0.5         # same brand within 500 m == the same venue, not a branch
sys.stdout.reconfigure(encoding="utf-8")


def km(a, b):
    try:
        la1, lo1, la2, lo2 = map(radians, (float(a[0]), float(a[1]),
                                           float(b[0]), float(b[1])))
    except (TypeError, ValueError):
        return None
    h = sin((la2-la1)/2)**2 + cos(la1)*cos(la2)*sin((lo2-lo1)/2)**2
    return 6371.0 * 2 * asin(sqrt(h))


def main():
    print("=" * 76)
    print("  VERIFY SEPTEMBER NEW BRANDS")
    print("=" * 76)

    rows = [r for r in csv.DictReader(open(SEPT, encoding="utf-8-sig"))
            if r["category"] == "genuinely_new_brand"]
    print(f"  september genuinely_new_brand : {len(rows):,}")

    # ---------- A1  internal branch_id ------------------------------------
    seen = defaultdict(list)
    for r in rows:
        seen[str(r["branch_id"])].append(r)
    dupe_id = {k: v for k, v in seen.items() if len(v) > 1}
    print(f"\n  A1  duplicate branch_id inside September : {len(dupe_id)}")
    for k, v in list(dupe_id.items())[:5]:
        print(f"        {k}  {[x['name'][:28] for x in v]}")

    # ---------- A2  internal name, distance-guarded -----------------------
    by_key = defaultdict(list)
    for r in rows:
        by_key[norm_name(r["name"])].append(r)
    exact_name = {k: v for k, v in by_key.items() if k and len(v) > 1}
    same_venue, diff_branch = [], 0
    for k, v in exact_name.items():
        for i in range(len(v)):
            for j in range(i + 1, len(v)):
                d = km((v[i].get("lat"), v[i].get("lon")),
                       (v[j].get("lat"), v[j].get("lon")))
                if d is not None and d <= MAX_KM:
                    same_venue.append((v[i], v[j], d))
                else:
                    diff_branch += 1
    print(f"\n  A2  same normalised name inside September: "
          f"{len(exact_name)} name groups")
    print(f"        pairs <= {MAX_KM} km apart -> SAME VENUE  : {len(same_venue)}")
    print(f"        pairs further apart -> different branch  : {diff_branch}")
    for a, b, d in same_venue[:8]:
        print(f"        {a['name'][:34]:36} {a['branch_id']} / "
              f"{b['branch_id']}   {d*1000:.0f} m")

    # ---------- B  vs the delivered unified file --------------------------
    print(f"\n  reading {UNIFIED.name} ...")
    u_ids, u_names, n = set(), defaultdict(list), 0
    with open(UNIFIED, "rb") as f:
        for line in f:
            n += 1
            rec = orjson.loads(line)
            sid = rec.get("source_id")
            if sid is not None:
                u_ids.add(str(sid))
            nm = rec.get("name")
            if nm:
                g = rec.get("geo") or {}
                u_names[norm_name(nm)].append((g.get("lat"), g.get("lng")))
    print(f"  unified records {n:,} | distinct source_id {len(u_ids):,} | "
          f"distinct brand names {len(u_names):,}")

    hit_id = [r for r in rows if str(r["branch_id"]) in u_ids]
    print(f"\n  B1  September branch_id already a unified source_id : "
          f"{len(hit_id)}")
    for r in hit_id[:8]:
        print(f"        {r['branch_id']}  {r['name'][:44]}")

    exact_hits, near_hits = [], []
    for r in rows:
        k = norm_name(r["name"])
        if not k:
            continue
        if k in u_names:
            close = [d for d in
                     (km((r.get("lat"), r.get("lon")), p) for p in u_names[k])
                     if d is not None and d <= MAX_KM]
            (exact_hits if close else near_hits).append(r)
    print(f"\n  B2  September name EXACTLY matches a unified brand : "
          f"{len(exact_hits) + len(near_hits)}")
    print(f"        and within {MAX_KM} km -> SAME VENUE : {len(exact_hits)}")
    print(f"        far away -> a genuine new branch    : {len(near_hits)}")
    for r in exact_hits[:10]:
        print(f"        {r['branch_id']}  {r['name'][:44]}")

    # ---------- fuzzy sweep over the residue ------------------------------
    # Only names NOT already exact-matched, against the unified name keys.
    # O(n*m) is too slow at 1,182 x 16,256, so bucket by first token.
    buckets = defaultdict(list)
    for k in u_names:
        if k:
            buckets[k.split()[0]].append(k)
    fuzzy = []
    done = {norm_name(r["name"]) for r in exact_hits + near_hits}
    for r in rows:
        k = norm_name(r["name"])
        if not k or k in done:
            continue
        cand = buckets.get(k.split()[0], [])
        for c in cand:
            s = fuzz.token_sort_ratio(k, c)
            if s >= FUZZ_MIN:
                close = [d for d in
                         (km((r.get("lat"), r.get("lon")), p)
                          for p in u_names[c]) if d is not None and d <= MAX_KM]
                if close:
                    fuzzy.append((r, c, s, min(close)))
                break
    print(f"\n  B3  fuzzy name >= {FUZZ_MIN} AND within {MAX_KM} km : {len(fuzzy)}")
    for r, c, s, d in fuzzy[:10]:
        print(f"        {r['name'][:30]:32} ~ {c[:28]:30} {s:.0f}  {d*1000:.0f} m")

    bad = len(dupe_id) + len(same_venue) + len(hit_id) + len(exact_hits) + len(fuzzy)
    print("\n" + "=" * 76)
    if bad:
        print(f"  {bad} PROBLEM ROW(S) - review before shipping")
    else:
        print(f"  CLEAN - all {len(rows):,} are new by branch_id AND by name")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    sys.exit(main())
