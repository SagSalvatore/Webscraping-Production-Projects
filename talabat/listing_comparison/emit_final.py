"""Emit the final shippable genuinely_new_brand file.

Ships ONLY rows that are both:
  * unique       - re-listings already collapsed by dedupe_final.py
  * area-resolved - the page's own `areaName` was present

`area_name` holds the CORRECTED value and overwrites the crawl-zone value in
place; the wrong one is not carried as a second column. The crawl-zone label
recorded where we searched FROM (the ?aid=), not where the venue is, and was
wrong on ~86% of rows - keeping it invites someone downstream to use it.

Cuisines and the JSON-LD name/coords are joined from the classifier output so
the file stands alone without a second lookup.

    python emit_final.py
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import NEW_CONFIRMED, NEW_RAW, OUT, read_jsonl

sys.stdout.reconfigure(encoding="utf-8")
csv.field_size_limit(10 ** 7)

UNIQUE = OUT / "genuinely_new_UNIQUE.csv"
AREA_CACHE = OUT / "areaname_cache.json"
FINAL = OUT / "talabat_genuinely_new_brands.jsonl"


def num(v, cast=float):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def main():
    rows = list(csv.DictReader(open(UNIQUE, encoding="utf-8-sig")))
    cache = json.loads(AREA_CACHE.read_text(encoding="utf-8"))
    conf = {str(r["branch_id"]): r for r in read_jsonl(NEW_CONFIRMED)}
    raw = {str(r["branch_id"]): r for r in read_jsonl(NEW_RAW)}
    print(f"  unique rows in           : {len(rows):,}")

    out, dropped = [], 0
    for r in rows:
        bid = str(r["branch_id"])
        area = ((cache.get(bid) or {}).get("area_name_real") or "").strip()
        if not area or area.lower() in ("null", "none"):
            dropped += 1
            continue                      # areaName is mandatory
        c = conf.get(bid, {})
        w = raw.get(bid, {})
        cuisines = c.get("serves_cuisine") or []
        if isinstance(cuisines, str):
            cuisines = [x.strip() for x in cuisines.split(",") if x.strip()]
        out.append({
            "source": "talabat",
            "country": "AE",
            "branch_id": num(bid, int),                 # unique per venue
            "restaurant_id": num(r.get("restaurant_id"), int),   # chain
            "name": r.get("name") or c.get("name"),
            "branch_name": (cache.get(bid) or {}).get("branch_name") or "",
            "area_name": area,                          # CORRECTED
            "url": r.get("url"),
            "lat": num(r.get("lat") or c.get("lat")),
            "lon": num(r.get("lon") or c.get("lon")),
            "cuisines": cuisines,
            "matched_cuisines": c.get("matched_cuisines") or [],
            "area_id_crawled_from": num(w.get("area_id"), int),
            "page_found": num(w.get("page_found"), int),
            "scraped_at": w.get("scraped_at"),
            "classified_at": c.get("filtered_at"),
            "category": "genuinely_new_brand",
        })

    assert len({o["branch_id"] for o in out}) == len(out), "duplicate branch_id"
    assert all(o["area_name"] for o in out), "empty area_name survived"

    with open(FINAL, "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    print(f"  dropped (no areaName)    : {dropped:,}")
    print(f"  FINAL SHIPPED            : {len(out):,}")
    print(f"\n  distinct branch_id  : {len({o['branch_id'] for o in out}):,}")
    print(f"  distinct chains     : {len({o['restaurant_id'] for o in out}):,}")
    print(f"  distinct areas      : {len({o['area_name'] for o in out}):,}")
    print(f"  with coordinates    : {sum(1 for o in out if o['lat']):,}")
    print(f"  with >=1 cuisine    : {sum(1 for o in out if o['cuisines']):,}")
    print(f"\n  top areas: {dict(Counter(o['area_name'] for o in out).most_common(8))}")
    print(f"\n  -> {FINAL.name}")
    print("\n  SAMPLE RECORD:")
    print(json.dumps(out[0], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
