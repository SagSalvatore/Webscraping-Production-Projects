"""Full name-vs-name sweep: September's new brands against everything we hold.

WHY THIS EXISTS SEPARATELY FROM verify_new_brands.py. That script's fuzzy pass
bucketed unified names by their FIRST TOKEN and stopped at the first candidate
over threshold. Both are wrong:

  * bucketing on token 0 never compares 'Karam Restaurant' with 'Al Karam
    Restaurant', or 'Cafe Rio' with 'Rio Cafe' - exactly the pairs a fuzzy
    check exists to catch.
  * `break` on the first hit hides a better match behind a worse one.

So this does the FULL cartesian sweep with rapidfuzz (C++, so 1,182 x 16,256 is
seconds, not minutes) and reports every candidate over threshold with its
distance, rather than silently resolving it.

DISTANCE IS REPORTED, NEVER USED TO HIDE A MATCH. A same-name pair 40 km apart
is a legitimate second branch; a same-name pair 30 m apart is one venue listed
twice. Both are printed - the operator decides, because Talabat appends the
branch area to the brand name and brand-level repeats are normal.

    python verify_names_full.py
    python verify_names_full.py --min 85
"""
import argparse
import csv
import sys
from collections import defaultdict
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import orjson
from rapidfuzz import fuzz, process

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "listing_comparison"))
from common import norm_name                                    # noqa: E402

SEPT = ROOT / "listing_comparison" / "output" / \
    "sept_full_2026-09-08_identity_classification.csv"
UNIFIED = ROOT / "unified" / "data" / "talabat_unified_202608.jsonl"
RI = ROOT / "Restaurant Identifier" / "data"
sys.stdout.reconfigure(encoding="utf-8")


def km(a, b):
    try:
        la1, lo1, la2, lo2 = map(radians, (float(a[0]), float(a[1]),
                                           float(b[0]), float(b[1])))
    except (TypeError, ValueError):
        return None
    h = sin((la2-la1)/2)**2 + cos(la1)*cos(la2)*sin((lo2-lo1)/2)**2
    return 6371.0 * 2 * asin(sqrt(h))


def main(args):
    print("=" * 78)
    print(f"  FULL NAME SWEEP  (threshold {args.min})")
    print("=" * 78)

    rows = [r for r in csv.DictReader(open(SEPT, encoding="utf-8-sig"))
            if r["category"] == "genuinely_new_brand"]
    keys = [norm_name(r["name"]) for r in rows]
    print(f"  september new brands : {len(rows):,}")

    # ---- the whole known-name universe, from BOTH sources -----------------
    uni_names, uni_geo = [], defaultdict(list)
    with open(UNIFIED, "rb") as f:
        for line in f:
            rec = orjson.loads(line)
            nm = rec.get("name")
            if not nm:
                continue
            k = norm_name(nm)
            if not k:
                continue
            g = rec.get("geo") or {}
            if k not in uni_geo:
                uni_names.append(k)
            uni_geo[k].append((g.get("lat"), g.get("lng")))
    print(f"  unified brand names  : {len(uni_names):,}")

    conf_names, conf_geo = [], defaultdict(list)
    for p in ("restaurants_confirmed.jsonl", "restaurants_confirmed_run2.jsonl"):
        for line in open(RI / p, encoding="utf-8"):
            try:
                rec = orjson.loads(line)
            except Exception:
                continue
            k = norm_name(rec.get("name"))
            if not k:
                continue
            if k not in conf_geo:
                conf_names.append(k)
            conf_geo[k].append((rec.get("lat"), rec.get("lon")))
    print(f"  confirmed brand names: {len(conf_names):,}  (run-1 + run-2)")

    for label, pool, geo in (("UNIFIED (Tech has it)", uni_names, uni_geo),
                             ("CONFIRMED (we hold it)", conf_names, conf_geo)):
        print(f"\n  --- vs {label} ---")
        near, far, exact = [], [], 0
        # full sweep, no bucketing, best match per September row
        res = process.cdist(keys, pool, scorer=fuzz.token_sort_ratio,
                            score_cutoff=args.min, workers=-1)
        for i, row in enumerate(res):
            best_j, best_s = -1, 0
            for j, s in enumerate(row):
                if s > best_s:
                    best_s, best_j = s, j
            if best_j < 0:
                continue
            k2 = pool[best_j]
            if keys[i] == k2:
                exact += 1
            d = min((x for x in (km((rows[i].get("lat"), rows[i].get("lon")), p)
                                 for p in geo[k2]) if x is not None),
                    default=None)
            rec = (rows[i], k2, best_s, d)
            (near if (d is not None and d <= 0.5) else far).append(rec)
        print(f"    rows with a name match >= {args.min} : {len(near)+len(far):,}"
              f"   (of which name is IDENTICAL: {exact})")
        print(f"      within 500 m  -> SAME VENUE, drop   : {len(near)}")
        print(f"      further apart -> new branch, keep   : {len(far)}")
        for r, k2, s, d in near[:12]:
            print(f"        {r['name'][:30]:32} ~ {k2[:26]:28} {s:5.1f} "
                  f"{d*1000:>7.0f} m  id={r['branch_id']}")
        if far:
            print(f"      sample of the 'different branch' calls:")
            for r, k2, s, d in far[:6]:
                dd = f"{d:.1f} km" if d is not None else "no coords"
                print(f"        {r['name'][:30]:32} ~ {k2[:26]:28} {s:5.1f} {dd:>10}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--min", type=float, default=88)
    sys.exit(main(p.parse_args()))
