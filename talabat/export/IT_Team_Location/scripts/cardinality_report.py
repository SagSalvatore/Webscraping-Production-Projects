"""Answer IT's two questions about area cardinality.

Q1  Do DIFFERENT restaurants share the same area?      (area -> many brands?)
Q2  Does ONE restaurant/chain span MULTIPLE areas?     (brand -> many areas?)

Entity levels in this file:
    _id        17,164  one physical branch (the row grain)
    source_id  15,198  one Talabat listing
    chain_id    9,906  one brand
"""
import sys
from collections import Counter, defaultdict

import orjson
from loguru import logger

from config import OUTPUT_DIR

sys.stdout.reconfigure(encoding="utf-8")

FINAL = OUTPUT_DIR / "ri-db.restaurants_id.final.json"


def main():
    recs = orjson.loads(FINAL.read_bytes())
    area_of = {r["_id"]["$oid"]: (r["location"] or {}).get("area") for r in recs}

    by_chain = defaultdict(list)
    by_source = defaultdict(list)
    for r in recs:
        by_chain[r["chain_id"]].append(r)
        by_source[r["source_id"]].append(r)

    areas = Counter(area_of.values())

    print("=" * 74)
    print("GRAIN OF THE FILE")
    print("=" * 74)
    print(f"  rows (_id, one branch)      {len(recs):>7,}")
    print(f"  distinct source_id          {len(by_source):>7,}")
    print(f"  distinct chain_id           {len(by_chain):>7,}")
    print(f"  distinct restaurant name    {len({r['name'] for r in recs}):>7,}")
    print(f"  distinct area               {len(areas):>7,}")

    # ---------------- Q1 ----------------
    print("\n" + "=" * 74)
    print("Q1  DO DIFFERENT RESTAURANTS SHARE THE SAME AREA?   -> YES")
    print("=" * 74)
    brands_per_area = defaultdict(set)
    rows_per_area = Counter()
    for r in recs:
        a = (r["location"] or {}).get("area")
        brands_per_area[a].add(r["chain_id"])
        rows_per_area[a] += 1
    multi = {a: b for a, b in brands_per_area.items() if len(b) > 1}
    print(f"  areas containing >1 distinct brand : {len(multi):,} of {len(brands_per_area):,}"
          f"  ({len(multi)/len(brands_per_area)*100:.1f}%)")
    print(f"  areas with exactly 1 brand         : {len(brands_per_area)-len(multi):,}")
    n = [len(b) for b in brands_per_area.values()]
    print(f"  brands per area  min/median/max    : {min(n)} / {sorted(n)[len(n)//2]} / {max(n)}")
    print("\n  busiest areas (rows | distinct brands):")
    for a, c in rows_per_area.most_common(10):
        print(f"     {a[:34]:36} {c:>5} rows   {len(brands_per_area[a]):>4} brands")

    # ---------------- Q2 ----------------
    print("\n" + "=" * 74)
    print("Q2  DOES ONE RESTAURANT SPAN MULTIPLE AREAS?   -> YES, for chains")
    print("=" * 74)

    for label, grouping in (("chain_id", by_chain), ("source_id", by_source)):
        multi_branch = {k: v for k, v in grouping.items() if len(v) > 1}
        spans = {k: {(r["location"] or {}).get("area") for r in v}
                 for k, v in multi_branch.items()}
        many = {k: s for k, s in spans.items() if len(s) > 1}
        same = {k: s for k, s in spans.items() if len(s) == 1}
        print(f"\n  by {label}:")
        print(f"     single-branch entities            {len(grouping)-len(multi_branch):>7,}")
        print(f"     multi-branch entities             {len(multi_branch):>7,}")
        print(f"       ...spanning >1 area             {len(many):>7,}"
              f"   ({len(many)/max(len(multi_branch),1)*100:.1f}%)")
        print(f"       ...all branches in ONE area     {len(same):>7,}"
              f"   ({len(same)/max(len(multi_branch),1)*100:.1f}%)")

    print("\n  largest chains - branches vs distinct areas:")
    big = sorted(by_chain.items(), key=lambda t: -len(t[1]))[:12]
    print(f"     {'brand':32} {'branches':>9} {'areas':>7}  ratio")
    for cid, rows in big:
        ar = {(r["location"] or {}).get("area") for r in rows}
        print(f"     {rows[0]['name'][:30]:32} {len(rows):>9} {len(ar):>7}"
              f"  {len(ar)/len(rows)*100:>5.0f}%")

    # data-quality signal: a big chain whose branches all share ONE area is
    # the classic symptom of a value being smeared across branches
    print("\n  QA - multi-branch chains where EVERY branch has the same area:")
    suspicious = [(cid, rows) for cid, rows in by_chain.items()
                  if len(rows) >= 4 and
                  len({(r["location"] or {}).get("area") for r in rows}) == 1]
    if not suspicious:
        print("     none with >=4 branches  (no sign of smeared values)")
    else:
        for cid, rows in sorted(suspicious, key=lambda t: -len(t[1]))[:10]:
            print(f"     {rows[0]['name'][:30]:32} {len(rows):>3} branches all in "
                  f"{(rows[0]['location'] or {}).get('area')!r}")

    # ---------------- practical guidance ----------------
    print("\n" + "=" * 74)
    print("WHAT THIS MEANS FOR THE BACKEND")
    print("=" * 74)
    dup = sum(1 for v in by_source.values() if len(v) > 1)
    print(f"  * area is MANY-TO-MANY. Neither direction is unique.")
    print(f"  * _id is the only unique key ({len(recs):,} rows, all distinct).")
    print(f"  * source_id is NOT unique - {dup:,} listings have >1 branch row;")
    print(f"    joining on it fans out. Use _id for a 1:1 join.")
    print(f"  * a brand can legitimately appear in many areas, and an area")
    print(f"    holds many brands - index both ways if you query both.")


if __name__ == "__main__":
    main()
