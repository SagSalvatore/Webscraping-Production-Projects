"""Assert Sagar's invariant: identical menu items carry identical std_term and
identical ingredients - everywhere, in both deliverables.

WHY IT SHOULD HOLD BY CONSTRUCTION. Neither file stores a per-row label; both
look one up per item_key from a mapping that has exactly one entry per key. So a
violation means something wrote a per-row value - which is precisely the bug
class worth catching, because it is invisible in any single record.

THREE SCOPES:
  1. inside July_menu_update.json
  2. inside August_export.json
  3. ACROSS the two - the same dish name appearing in both must agree, or Tech
     sees one item labelled two ways depending on which file they read.

Grouped on the shipped `name`, not item_key, because `name` is what Tech
actually sees and joins on; item_key is not in either file.

Streamed with ijson - the files are 300-550 MB.

    python verify_consistency.py
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import ijson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JULY = HERE / "data" / "July_menu_update.json"
AUG = ROOT / "August_menu" / "data" / "August_export.json"

sys.stdout.reconfigure(encoding="utf-8")


def collect(path):
    """name -> {(std_term, ingredients tuple)}   plus how many rows carry it."""
    seen = defaultdict(set)
    rows = defaultdict(int)
    with open(path, "rb") as f:
        for rec in ijson.items(f, "item", use_float=True):
            for it in rec.get("menu_items") or []:
                nm = it.get("name")
                if not nm:
                    continue
                sig = (it.get("std_term"),
                       tuple(it.get("ingredients") or []))
                seen[nm].add(sig)
                rows[nm] += 1
    return seen, rows


def report(label, seen, rows):
    bad = {n: s for n, s in seen.items() if len(s) > 1}
    n_rows = sum(rows[n] for n in bad)
    print(f"\n  {label}")
    print(f"    distinct item names       : {len(seen):,}")
    print(f"    names with >1 (term,ings) : {len(bad):,}"
          + ("" if not bad else f"   <-- VIOLATION, {n_rows:,} rows"))
    for n, s in list(bad.items())[:6]:
        print(f"       {n[:40]:42} {len(s)} variants")
        for term, ings in list(s)[:2]:
            print(f"          {str(term)[:22]:24} {len(ings)} ingredients "
                  f"{list(ings)[:3]}")
    return bad


def main():
    print("=" * 74)
    print("  CONSISTENCY: identical items must carry identical labels")
    print("=" * 74)

    fails = 0
    jseen, jrows = collect(JULY)
    fails += len(report("July_menu_update.json", jseen, jrows))
    aseen, arows = collect(AUG)
    fails += len(report("August_export.json", aseen, arows))

    # 3. across the two files
    shared = set(jseen) & set(aseen)
    disagree = {n for n in shared if jseen[n] != aseen[n]}
    print(f"\n  ACROSS BOTH FILES")
    print(f"    item names in both        : {len(shared):,}")
    print(f"    names that DISAGREE       : {len(disagree):,}"
          + ("" if not disagree else "   <-- same dish, two answers"))
    for n in list(disagree)[:6]:
        jt = list(jseen[n])[0]
        at = list(aseen[n])[0]
        print(f"       {n[:36]:38}")
        print(f"          July   {str(jt[0])[:20]:22} {len(jt[1])} ingredients")
        print(f"          August {str(at[0])[:20]:22} {len(at[1])} ingredients")

    print("\n" + "=" * 74)
    if fails == 0 and not disagree:
        print("  PASS - every identical item carries identical std_term "
              "and ingredients")
    else:
        print(f"  FAIL - {fails:,} within-file violations, "
              f"{len(disagree):,} cross-file disagreements")
    print("=" * 74)
    return 0 if (fails == 0 and not disagree) else 1


if __name__ == "__main__":
    sys.exit(main())
