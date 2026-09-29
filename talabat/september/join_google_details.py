"""Attach Google Maps details to each September restaurant.

INPUT   google_maps_details.jsonl   523 UAE locations, keyed by BRAND
        sept_restaurants_for_classification.jsonl   1,047 restaurants
OUTPUT  september_google_details_202609.csv         one row per restaurant

MATCHED BY DISTANCE, NOT BY BRAND ALONE. A brand can hold several Google
locations, and a Talabat listing is one physical branch. Both sides carry
coordinates - Talabat's from the listing, Google's from the Maps record - so the
branch is matched to its NEAREST same-brand Google location. Joining on brand
alone would hand a Dubai branch the phone number of the Sharjah one, which is
the same class of error as the source_id smear that cost a whole stage earlier
in this project: the output looks completely plausible and is wrong per row.

MAX_KM caps it. Beyond that the nearest same-brand location is not this branch,
so the row is left empty rather than filled with a neighbour's details. An
absent website is honest; a wrong phone number is not.

    python join_google_details.py --dry-run
    python join_google_details.py
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CLS = ROOT / "August_classification" / "data_sep"
SRC = HERE / "data" / "sept_restaurants_for_classification.jsonl"
DETAILS = CLS / "google_maps_details.jsonl"
CLASSIFIED = CLS / "restaurants_classified.jsonl"
OUT = HERE / "data" / "september_google_details_202609.csv"
MAX_KM = 25.0
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
    print("  JOIN GOOGLE MAPS DETAILS TO SEPTEMBER RESTAURANTS")
    print("=" * 78)

    rests = [json.loads(l) for l in open(SRC, encoding="utf-8")]
    by_brand = defaultdict(list)
    for line in open(DETAILS, encoding="utf-8"):
        d = json.loads(line)
        by_brand[d["brand"]].append(d)
    cls = {}
    for line in open(CLASSIFIED, encoding="utf-8"):
        c = json.loads(line)
        cls[c["branch_id"]] = c
    print(f"  restaurants        : {len(rests):,}")
    print(f"  brands with details: {len(by_brand):,}")
    print(f"  google locations   : {sum(len(v) for v in by_brand.values()):,}")

    rows, far, nogeo = [], 0, 0
    for r in rests:
        cand = by_brand.get(r["name"], [])
        best, bestd = None, None
        for d in cand:
            dist = km((r.get("lat"), r.get("lon")),
                      (d.get("latitude"), d.get("longitude")))
            if dist is None:
                continue
            if bestd is None or dist < bestd:
                best, bestd = d, dist
        if cand and bestd is None:
            nogeo += 1
        if best and bestd is not None and bestd > MAX_KM:
            far += 1
            best = None
        c = cls.get(r["branch_id"], {})
        # Serper returns the KEY with a null when a field is absent, so
        # .get(k, "") yields None, not "". Counting `!= ""` then reported every
        # field at 100% of matched rows - including `website`, which is really
        # 41%. Normalise to "" so the fill rates mean what they say.
        g = {k: ("" if v is None else v) for k, v in (best or {}).items()}
        rows.append({
            "branch_id": r["branch_id"],
            "restaurant_name": r["name"],
            "area_name": r["area_name"],
            "city": r["city"], "emirate": r["emirate"],
            "talabat_url": r["url"],
            "restaurant_type": c.get("restaurant_type", ""),
            "outlet_type": c.get("outlet_type", ""),
            "chained_outlet_type": c.get("chained_outlet_type", ""),
            "google_locations_uae": (c.get("google_locations_uae")
                                     if c.get("google_locations_uae") is not None
                                     else ""),
            "google_title": g.get("title", ""),
            "google_address": g.get("address", ""),
            "phone": g.get("phone", ""),
            "website": g.get("website", ""),
            "google_maps_url": g.get("google_maps_url", ""),
            "place_id": g.get("place_id", ""),
            "google_category": g.get("google_category", ""),
            "rating": g.get("rating", ""),
            "rating_count": g.get("rating_count", ""),
            "price_level": g.get("price_level", ""),
            "match_km": f"{bestd:.2f}" if best and bestd is not None else "",
        })

    got = sum(1 for x in rows if x["google_maps_url"])
    print(f"\n  matched to a Google location : {got:,} ({got/len(rows)*100:.1f}%)")
    print(f"    rejected, nearest >{MAX_KM:.0f} km : {far}")
    print(f"    brand matched but no coords: {nogeo}")
    for fld in ("google_address", "phone", "website", "rating",
                "price_level", "google_category"):
        n = sum(1 for x in rows if x[fld] != "")
        print(f"    {fld:18} {n:>5,}/{len(rows):,}  ({n/len(rows)*100:5.1f}% of all "
              f"restaurants | {n/max(got,1)*100:5.1f}% of matched)")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n  -> {OUT.name}  ({len(rows):,} rows)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
