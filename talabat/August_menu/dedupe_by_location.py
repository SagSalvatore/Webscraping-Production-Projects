"""Remove records that describe the same physical outlet twice.

WHAT final_cleanup_pass.py MISSED. It deduped on (source_id, lat, lng) using
FULL float precision. A base record carries Talabat's coordinate and its
verified-location twin carries Google's; for the same outlet those agree to
about a metre but differ in the 5th decimal, so exact equality never fired.
344 pairs survived - same source_id, same address text, same place:

    Bakery House  sid 602367  BASE  @ 24.2459,55.7205
    Bakery House  sid 602367  VERIF @ 24.2459,55.7205   same address

Rounding to 4 dp (~11 m) catches them. That is deliberately loose: two genuinely
distinct branches are never 11 m apart, while one outlet's two coordinate
sources routinely differ by a few metres.

THE KEY IS (name, rounded coords) - name paired with a location, per Sagar's
rule. A brand repeating is not a duplicate; a brand repeating at one point is.
The BASE record always wins: it carries Talabat's own geometry and is the row
Tech joins on.

NOT TOUCHED:
  * different brands at one coordinate - 430 points, food courts and cloud
    kitchens, all legitimate.
  * same brand, same coarse `area` string, coordinates kilometres apart - 56
    groups. Those are real separate branches that collide only because our
    address fallback uses area_name. Distance, not text, decides.

    python dedupe_by_location.py --dry-run
    python dedupe_by_location.py
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPORT = HERE / "data" / "August_export.json"

DP = 4          # ~11 m

sys.stdout.reconfigure(encoding="utf-8")


def nm(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s)).strip().lower()


def main(args):
    d = json.loads(EXPORT.read_text(encoding="utf-8"))
    n0 = len(d)
    base0 = sum(1 for r in d if not r.get("is_verified_location"))
    print("=" * 72)
    print(f"  DEDUPE BY LOCATION  ({n0:,} records)")
    print("=" * 72)

    groups = defaultdict(list)
    for i, r in enumerate(d):
        g = r.get("geo") or {}
        if g.get("lat") is None:
            continue
        groups[(nm(r.get("name")), round(float(g["lat"]), DP),
                round(float(g["lng"]), DP))].append(i)

    drop = set()
    stats = Counter()
    for k, idxs in groups.items():
        if len(idxs) < 2:
            continue
        bases = [i for i in idxs if not d[i].get("is_verified_location")]
        sids = {d[i]["source_id"] for i in idxs}
        if bases:
            keep = bases[0]
            for i in idxs:
                if i == keep:
                    continue
                drop.add(i)
                stats["verified_same_place_as_base" if len(sids) == 1
                      else "same_brand_same_point_other_sid"] += 1
        else:
            for i in idxs[1:]:
                drop.add(i)
                stats["verified_dupe_of_verified"] += 1

    print(f"\n  groups sharing (name, ~11 m coords): "
          f"{sum(1 for v in groups.values() if len(v) > 1):,}")
    for k, v in stats.most_common():
        print(f"    {k:36} {v:>6,}")
    print(f"    total records to drop              {len(drop):>6,}")

    out = [r for i, r in enumerate(d) if i not in drop]
    base1 = sum(1 for r in out if not r.get("is_verified_location"))
    ver1 = len(out) - base1
    print(f"\n  records      {n0:,} -> {len(out):,}")
    print(f"  base         {base0:,} -> {base1:,}"
          + ("  <-- MUST NOT CHANGE" if base1 != base0 else "  (unchanged)"))
    print(f"  verified locations remaining: {ver1:,}")
    assert base1 == base0, "base records lost - aborting"

    # prove the invariant now holds
    chk = defaultdict(int)
    for r in out:
        g = r.get("geo") or {}
        if g.get("lat") is not None:
            chk[(nm(r.get("name")), round(float(g["lat"]), DP),
                 round(float(g["lng"]), DP))] += 1
    left = sum(v - 1 for v in chk.values() if v > 1)
    print(f"  remaining (name, coords) repeats: {left}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0
    assert left == 0, "duplicates remain"

    tmp = EXPORT.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    tmp.replace(EXPORT)
    print(f"\n  -> {EXPORT.name} ({EXPORT.stat().st_size/1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
