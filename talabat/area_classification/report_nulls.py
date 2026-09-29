"""Report every row still carrying a null area, with the keys to reconcile it.

WHY source_id IS NOT ENOUGH. 342 source_ids cover more than one row - a chain's
branches share one listing id - so a null keyed only on source_id cannot be
matched back to the branch it came from. That ambiguity is the join-key trap that
has already smeared values across branches three times on this project. Every row
here therefore carries:

    row_index   position in ri-db.restaurants_full.json, 0-based. THE key the
                pipeline's own files use - recovered_nulls.json,
                offtaxonomy_areas.json, final_nulls_resolved.json and
                city_mismatch_fixes.json are all keyed by it, so this is what
                lets a null be pushed back through any stage.
    _id         the row-unique Mongo id ($oid unwrapped). Tech's key.
    source_id   Talabat's listing id - NOT unique, kept for lookups only
    chain_id    groups branches of one brand

Plus `raw_text`, the original location.area that could not be resolved, and
`fragment`, the area-shaped piece of that text if there is one - the starting
point for any further recovery pass.

    python report_nulls.py
"""
import csv
import json
import sys
from collections import Counter, defaultdict

import config as C
from fix_city_mismatch import looks_like_area
from taxonomy import load, segments

OUT = C.DATA / "nulls_by_city.csv"
sys.stdout.reconfigure(encoding="utf-8")


def oid(v):
    """{'$oid': 'abc'} -> 'abc'; anything else passes through as a string."""
    if isinstance(v, dict):
        return v.get("$oid") or v.get("$numberLong") or json.dumps(v)
    return "" if v is None else str(v)


def main():
    areas, TAX = load(C.HERE / "area_list.csv")
    cities = {c.lower() for c in C.UAE_CITIES} | {"uae", "united arab emirates"}
    src = json.loads(C.INPUT.read_text(encoding="utf-8"))
    out = json.loads(C.OUTPUT.read_text(encoding="utf-8"))
    assert len(src) == len(out), "input and output are different lengths"

    rows = []
    for i, (a, b) in enumerate(zip(src, out)):
        if (b.get("location") or {}).get("area"):
            continue
        S, O = a.get("location") or {}, b.get("location") or {}
        raw = S.get("area") or ""
        frags = [f for f in (looks_like_area(s, cities)
                             for s in segments(raw)) if f]
        rows.append({
            "row_index": i,
            "_id": oid(a.get("_id")),
            "source_id": a.get("source_id"),
            "mordor_restaurant_id": oid(a.get("mordor_restaurant_id")),
            "chain_id": a.get("chain_id"),
            "name": a.get("name") or "",
            # city as SHIPPED, so the file agrees with the deliverable
            "city": O.get("city") or "(no city)",
            "raw_text": raw or "(empty)",
            "sublocality": S.get("sublocality") or "",
            "fragment": frags[-1] if frags else "",
            "fragment_on_taxonomy": bool(frags) and frags[-1] in TAX,
        })

    rows.sort(key=lambda r: (r["city"], r["name"], r["row_index"]))
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # the point of the id columns is that a row can be found again - prove it
    assert len({r["row_index"] for r in rows}) == len(rows), \
        "row_index is not unique"
    dup_sid = len(rows) - len({r["source_id"] for r in rows})
    n_id = len({r["_id"] for r in rows})

    print(f"  nulls: {len(rows):,}   distinct restaurants "
          f"{len({r['name'] for r in rows}):,}")
    print(f"  row_index unique: yes    _id unique: "
          f"{'yes' if n_id == len(rows) else f'NO ({n_id:,})'}")
    print(f"  source_id would be ambiguous for {dup_sid:,} of them"
          "   <- why row_index and _id are here")
    print()
    print(f"  {'city':18}{'rows':>6}{'restaurants':>13}{'has fragment':>14}")
    print("  " + "-" * 49)
    by = defaultdict(list)
    for r in rows:
        by[r["city"]].append(r)
    for c, v in sorted(by.items(), key=lambda x: -len(x[1])):
        print(f"  {c[:17]:18}{len(v):>6}{len({r['name'] for r in v}):>13}"
              f"{sum(1 for r in v if r['fragment']):>14}")
    print(f"  {'TOTAL':18}{len(rows):>6}"
          f"{len({r['name'] for r in rows}):>13}"
          f"{sum(1 for r in rows if r['fragment']):>14}")
    print(f"\n  -> {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
