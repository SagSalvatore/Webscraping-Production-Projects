"""Validate that branches of the SAME brand no longer share one area.

The repair fixed `location.area` only - `location.raw` still holds the smeared
Google address, by instruction. So "is it fixed?" cannot be answered by
re-running the smear detector, which keys on raw. It is answered here:

  TEST 1  the repaired groups. For every brand group that shared one full
          address, are its branches' areas distinct NOW where they were
          identical BEFORE? Measured against the .pre_areafix.bak copies.

  TEST 2  the whole file, not just what we touched. Any brand with two or more
          branches sitting MORE THAN 1 km apart that still report the identical
          area is a remaining collision - whether or not it was in the original
          smear set.

A shared area is only a defect WITHIN one brand. Many different brands sit in
Business Bay honestly, so cross-brand repeats are counted and ignored.

    python validate_areas_unique.py
"""
import csv
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ijson
import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
FILES = [
    ("july_export", ROOT / "export" / "talabat_export.json", "json"),
    ("august_export", ROOT / "August_menu" / "data" / "August_export.json", "json"),
    ("unified_202608", ROOT / "unified" / "data" / "talabat_unified_202608.jsonl", "jsonl"),
]
SPREAD_KM = 1.0
sys.stdout.reconfigure(encoding="utf-8")


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def load(path, shape):
    """branch_id -> (chain_id, area, raw, lat, lng, name), listings only."""
    out = {}
    with open(path, "rb") as f:
        it = (ijson.items(f, "item", use_float=True) if shape == "json"
              else (orjson.loads(l) for l in f if l.strip()))
        for rec in it:
            if rec.get("is_verified_location"):
                continue
            L = rec.get("location") or {}
            g = rec.get("geo") or {}
            try:
                la, ln = float(g.get("lat")), float(g.get("lng"))
            except (TypeError, ValueError):
                la = ln = None
            out[str(rec.get("source_id"))] = (rec.get("chain_id"), L.get("area"),
                                              L.get("raw"), la, ln, rec.get("name"))
    return out


def norm(s):
    return " ".join(str(s or "").split()).casefold()


def main():
    print("=" * 78)
    print("  VALIDATION - do branches of one brand now have different areas?")
    print("=" * 78)

    for name, path, shape in FILES:
        bak = path.with_suffix(path.suffix + ".pre_areafix.bak")
        if not path.exists():
            print(f"  {name}: MISSING")
            continue
        now = load(path, shape)
        # THE BASELINE COMES FROM THE AUDIT TRAIL, not from a .bak file. The
        # backups are per-run and one was already overwritten by a second
        # repair; the audit CSVs record area_before for every row ever changed,
        # so they are the durable record of what the data used to be.
        before_area = {}
        for aud in (DATA / "area_fix_applied.csv",
                    DATA / "area_fallback_fix_applied.csv"):
            if aud.exists():
                for r in csv.DictReader(open(aud, encoding="utf-8-sig")):
                    before_area.setdefault(r["source_id"], r["area_before"])
        before = {sid: (v[0], before_area.get(sid, v[1]), v[2], v[3], v[4], v[5])
                  for sid, v in now.items()}

        # ---- TEST 1: the groups that shared one full address -------------
        # raw was never modified, so grouping on it still identifies the
        # original smear groups; only branches we actually repaired count.
        groups = defaultdict(list)
        for sid, (cid, _a, raw, *_r) in before.items():
            if (cid is None or not raw or sid not in before_area
                    or norm(raw) == norm(before[sid][1])):
                continue
            groups[(cid, norm(raw))].append(sid)
        fixed = still = single = 0
        examples = []
        for key, sids in groups.items():
            if len(sids) < 2:
                continue
            pts = [(before[s][3], before[s][4]) for s in sids
                   if before[s][3] is not None]
            spread = max((km(a, b) for i, a in enumerate(pts)
                          for b in pts[i + 1:]), default=0.0)
            if spread <= SPREAD_KM:
                continue
            b_u = len({norm(before[s][1]) for s in sids})
            n_u = len({norm(now[s][1]) for s in sids if s in now})
            if n_u == len(sids):
                fixed += 1
            elif n_u > b_u:
                single += 1
            else:
                still += 1
                if len(examples) < 5:
                    examples.append((now[sids[0]][5], len(sids), n_u,
                                     now[sids[0]][1]))
        total = fixed + still + single
        print(f"\n  {name}")
        print(f"    TEST 1 - repaired brand groups: {total:,}")
        print(f"      every branch now has a DISTINCT area : {fixed:,} "
              f"({fixed/max(total,1)*100:.1f}%)")
        print(f"      partially separated                  : {single:,}")
        print(f"      still all identical                  : {still:,}")
        for nm, n, u, area in examples:
            print(f"         {str(nm)[:28]:30} {n} branches -> {u} distinct "
                  f"({str(area)[:24]})")

        # ---- TEST 2: any remaining same-brand collision, file-wide -------
        by_brand = defaultdict(list)
        for sid, (cid, area, _r, la, ln, nm) in now.items():
            if cid is not None and area and la is not None:
                by_brand[cid].append((sid, norm(area), la, ln, nm))
        coll, coll_rows = 0, []
        for cid, rs in by_brand.items():
            per_area = defaultdict(list)
            for sid, area, la, ln, nm in rs:
                per_area[area].append((sid, la, ln, nm))
            for area, members in per_area.items():
                if len(members) < 2:
                    continue
                sp = max((km((a[1], a[2]), (b[1], b[2]))
                          for i, a in enumerate(members) for b in members[i + 1:]),
                         default=0.0)
                if sp > SPREAD_KM:
                    coll += 1
                    coll_rows.append((members[0][3], len(members), sp, area))
        print(f"    TEST 2 - same brand, same area, >1 km apart: {coll:,} collisions")
        for nm, n, sp, area in sorted(coll_rows, key=lambda x: -x[1])[:5]:
            print(f"         {str(nm)[:28]:30} {n} branches {sp:>6.1f} km  "
                  f"'{str(area)[:26]}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
