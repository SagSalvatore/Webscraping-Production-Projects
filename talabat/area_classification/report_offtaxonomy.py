"""Report every shipped area that is NOT in area_list.csv, for Tech.

Derived from the CLEANED OUTPUT, not from the intermediate stage files. Those
files are written per stage and go stale the moment a later stage adds rows -
the first version of this CSV missed the 9 values the Tavily tier contributed.
Reading the deliverable itself means the report cannot disagree with it.

WHY OFF-TAXONOMY VALUES EXIST AT ALL. area_list.csv is Talabat's DELIVERY ZONE
list, not a gazetteer of the UAE. 'Rabdan', 'Al Danah' and 'Dubai South' are real
places with no zone of their own, and forcing them to the nearest zone is
actively wrong (Wadi Al Safa 4 -> Al Safa 2 is a different community). These
were read from each row's own address text, so they are evidence, not guesses.

    python report_offtaxonomy.py
"""
import csv
import json
import sys
from collections import Counter, defaultdict

import config as C
from taxonomy import load

OUT = C.DATA / "offtaxonomy_areas_for_tech.csv"
sys.stdout.reconfigure(encoding="utf-8")


def main():
    areas, TAX = load(C.HERE / "area_list.csv")
    rows = json.loads(C.OUTPUT.read_text(encoding="utf-8"))

    n = Counter()
    cities = defaultdict(Counter)
    sample = {}
    for r in rows:
        a = (r.get("location") or {}).get("area")
        if not a or a in TAX:
            continue
        n[a] += 1
        cities[a][(r.get("location") or {}).get("city") or "?"] += 1
        sample.setdefault(a, r.get("name") or "")

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["area_not_in_area_list", "rows", "cities",
                    "example_restaurant", "note"])
        for a, c in n.most_common():
            w.writerow([a, c, "; ".join(sorted(cities[a])), sample[a],
                        "real UAE area, not a Talabat delivery zone"])

    print(f"  off-taxonomy areas shipped: {len(n):,} distinct, "
          f"{sum(n.values()):,} rows")
    print(f"  -> {OUT.name}")
    for a, c in n.most_common(10):
        print(f"     {c:>5}  {a[:40]:42} {'; '.join(sorted(cities[a]))[:30]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
