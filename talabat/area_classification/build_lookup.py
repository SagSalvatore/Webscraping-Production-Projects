"""Stage 1 - assemble everything July already resolved, so this month reuses it.

THREE INDEPENDENT SOURCES, in precedence order:

  A. source_id -> clean area      from July's cleaned deliverable. Direct reuse
                                  where the join is unambiguous. 61.4% of rows.
  B. raw text -> clean area       from July's caches and mapping tables. Catches
                                  a value we have seen before on a DIFFERENT
                                  restaurant.
  C. canonical vocabulary         the 716 shipped areas. A value already in it
                                  needs no work.

WHY source_id AND NOT _id. Tech regenerated the Mongo _ids, so _id overlap is
ZERO. source_id overlaps on all 15,198 July restaurants.

WHY THE JOIN IS GUARDED. 342 source_ids in the new file carry MULTIPLE rows, and
all 342 have DIFFERENT area text per row - they are chain branches at different
addresses. A blind source_id join would stamp one branch's area onto its
siblings. Only 1:1 pairs are reused; the rest fall through to later stages.

THE CITY CAVEAT (recorded, not silently applied). On 2,337 of the 1:1 pairs the
cities disagree, and 98.9% of those appear in July's coordinate_correction_map:
July corrected the emirate from the branch's own coordinates, while Tech's file
carries the uncorrected original. July's area is the better value and is reused,
but the discrepancy is written to a side file for Tech rather than us silently
editing a field we were not asked to touch.

    python build_lookup.py
"""
import csv
import json
import sys
from collections import Counter, defaultdict

from config import (AREANAME_CACHE, AREAS_LIST, CITY_DISCREPANCIES,
                    COORD_CORRECTIONS, INPUT, IT_CACHE, IT_OUT, JULY_CLEAN,
                    LOOKUP, MAP_TABLES, PROVENANCE)

sys.stdout.reconfigure(encoding="utf-8")


def norm(s):
    return (s or "").strip().lower()


def main():
    print("=" * 74)
    print("  BUILD RECONCILIATION LOOKUP")
    print("=" * 74)

    new = json.loads(INPUT.read_text(encoding="utf-8"))
    july = json.loads(JULY_CLEAN.read_text(encoding="utf-8"))
    areas = json.loads(AREAS_LIST.read_text(encoding="utf-8"))
    vocab = {a["area"] for a in areas if a.get("area")}
    area_city = {a["area"]: a.get("city") for a in areas if a.get("area")}
    print(f"  new file      {len(new):,} records")
    print(f"  July cleaned  {len(july):,} records")
    print(f"  vocabulary    {len(vocab):,} areas")

    # ---- A. source_id -> clean area, 1:1 only ---------------------------
    nd, jd = defaultdict(list), defaultdict(list)
    for r in new:
        nd[str(r["source_id"])].append(r)
    for r in july:
        jd[str(r["source_id"])].append(r)

    by_sid, city_diffs = {}, []
    st = Counter()
    for sid, rows in nd.items():
        jrows = jd.get(sid)
        if not jrows:
            st["no July match"] += len(rows)
            continue
        if len(rows) != 1 or len(jrows) != 1:
            st["ambiguous - skipped"] += len(rows)
            continue
        n, j = rows[0], jrows[0]
        area = (j.get("location") or {}).get("area")
        if not area:
            st["July area empty"] += 1
            continue
        by_sid[sid] = area
        st["1:1 reusable"] += 1
        nc = (n.get("location") or {}).get("city")
        jc = (j.get("location") or {}).get("city")
        if nc != jc:
            city_diffs.append({"source_id": sid, "name": n.get("name"),
                               "city_in_tech_file": nc, "city_july_corrected": jc,
                               "area_july": area})

    print(f"\n  A. source_id -> clean area")
    for k, v in st.most_common():
        print(f"       {v:>7,}  {k}")

    # ---- B. raw text -> clean area --------------------------------------
    lookup = {}
    src = Counter()
    for p in sorted(IT_CACHE.glob("*.json")):
        if p.name == "geocode_cache.json":
            continue                     # keyed by coordinate, not text
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for k, v in d.items():
            val = None
            if isinstance(v, str) and v.strip():
                val = v.strip()
            elif isinstance(v, dict):
                for c in ("area", "area_name_real", "result", "value"):
                    if isinstance(v.get(c), str) and v[c].strip():
                        val = v[c].strip()
                        break
            if val and norm(k) not in lookup:
                lookup[norm(k)] = val
                src[p.name] += 1

    if PROVENANCE.exists():
        with open(PROVENANCE, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                a, b = norm(r.get("area_before")), (r.get("area_after") or "").strip()
                if a and b and a not in lookup:
                    lookup[a] = b
                    src["manual_stage_provenance"] += 1

    for name in MAP_TABLES:
        p = IT_OUT / f"{name}.csv"
        if not p.exists():
            continue
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                a = norm(r.get("from") or r.get("variant"))
                b = (r.get("to") or r.get("canonical") or "").strip()
                if a and b and a not in lookup:
                    lookup[a] = b
                    src[name] += 1

    print(f"\n  B. raw text -> clean area   {len(lookup):,} entries")
    for k, v in src.most_common(8):
        print(f"       {v:>7,}  {k}")

    # ---- Talabat's own areaName -----------------------------------------
    areaname = {}
    if AREANAME_CACHE.exists():
        d = json.loads(AREANAME_CACHE.read_text(encoding="utf-8"))
        for k, v in d.items():
            a = v.get("area_name_real") if isinstance(v, dict) else v
            if a and str(a).strip():
                areaname[str(k)] = str(a).strip()
    print(f"\n  C. Talabat's own areaName   {len(areaname):,} source_ids")

    # ---- projected coverage ---------------------------------------------
    cov = Counter()
    for r in new:
        sid = str(r["source_id"])
        a = ((r.get("location") or {}).get("area") or "").strip()
        if sid in by_sid:
            cov["1_july_direct"] += 1
        elif sid in areaname:
            cov["2_talabat_areaName"] += 1
        elif a and a in vocab:
            cov["3_already_canonical"] += 1
        elif a and norm(a) in lookup:
            cov["4_raw_to_clean"] += 1
        elif not a:
            cov["6_null_input"] += 1
        else:
            cov["5_needs_work"] += 1
    tot = len(new)
    print(f"\n  PROJECTED COVERAGE over {tot:,} rows:")
    for k in sorted(cov):
        print(f"     {k:24} {cov[k]:>7,}  ({cov[k]/tot*100:5.1f}%)")
    free = sum(v for k, v in cov.items() if k[0] in "1234")
    print(f"\n     FREE (no API)          {free:>7,}  ({free/tot*100:5.1f}%)")
    print(f"     needs new work         {cov['5_needs_work']:>7,}  "
          f"({cov['5_needs_work']/tot*100:5.1f}%)")
    print(f"     null input (D5)        {cov['6_null_input']:>7,}")

    LOOKUP.write_text(json.dumps({
        "by_source_id": by_sid,
        "by_raw_text": lookup,
        "by_areaname": areaname,
        "vocabulary": sorted(vocab),
        "area_city": area_city,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"\n  -> {LOOKUP.name}")

    if city_diffs:
        with open(CITY_DISCREPANCIES, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(city_diffs[0].keys()))
            w.writeheader()
            w.writerows(city_diffs)
        print(f"  -> {CITY_DISCREPANCIES.name}  ({len(city_diffs):,} rows "
              f"where Tech's city contradicts the coordinates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
