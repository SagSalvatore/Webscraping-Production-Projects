"""Verification sheet for the 1,885 verified-location records.

One row per location, grouped by brand, so the exact-name rule can be checked by
eye: google_title is carried through beside the brand it was matched to, and
under the rule they must be equal once Arabic and punctuation are stripped.

Writes two files:
  august_verified_locations.csv   one row per location (the audit trail)
  august_brand_location_summary.csv  one row per brand, locations rolled up

    python verify_locations.py
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXPORT = HERE / "data" / "August_export.json"
MAPS = ROOT / "August_classification" / "data" / "google_maps_details.jsonl"
OUT_LOC = HERE / "data" / "august_verified_locations.csv"
OUT_BRAND = HERE / "data" / "august_brand_location_summary.csv"

sys.stdout.reconfigure(encoding="utf-8")

_ARABIC = re.compile("[؀-ۿ]+")


def strict_name(s):
    t = _ARABIC.sub(" ", str(s or ""))
    t = re.sub(r"[^A-Za-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def nrm(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def main():
    d = json.loads(EXPORT.read_text(encoding="utf-8"))
    ver = [r for r in d if r.get("is_verified_location")]
    base = {r["source_id"]: r for r in d if not r.get("is_verified_location")}

    # google titles, so the match can be shown rather than asserted
    titles = defaultdict(list)
    with open(MAPS, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                titles[nrm(r.get("brand"))].append(r)

    by_brand = defaultdict(list)
    for r in ver:
        by_brand[r["name"]].append(r)

    print("=" * 72)
    print(f"  VERIFIED LOCATIONS  {len(ver):,} rows over {len(by_brand):,} brands")
    print("=" * 72)

    rows = []
    for brand, recs in sorted(by_brand.items(), key=lambda x: (-len(x[1]), x[0])):
        cand = {}
        for t in titles.get(nrm(brand), []):
            cand[(t.get("latitude"), t.get("longitude"))] = t
        for r in recs:
            g = cand.get((r["geo"]["lat"], r["geo"]["lng"]), {})
            gt = g.get("title")
            rows.append({
                "brand": brand,
                "chain_id": r["chain_id"],
                "source_id": r["source_id"],
                "source_id_text": f'="{r["source_id"]}"',
                "locations_for_brand": len(recs),
                "google_title": gt,
                "exact_match": ("YES" if strict_name(gt) == strict_name(brand)
                                else "NO" if gt else "?"),
                "address": r["location"]["raw"],
                "city": r["location"]["city"],
                "area": r["location"]["area"],
                "lat": r["geo"]["lat"],
                "lng": r["geo"]["lng"],
                "phone": r["contact_phone"],
                "website": r["website"],
                "maps_url": r["maps_url"],
                "outlet_type": r["outlet_type"],
                "chain_type": r["chain_type"] or "N/A",
                "restaurant_type": r["restaurant_type"],
                "menu_items": len(r["menu_items"]),
            })

    with open(OUT_LOC, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    brand_rows = []
    for brand, recs in sorted(by_brand.items(), key=lambda x: (-len(x[1]), x[0])):
        b = base.get(recs[0]["source_id"], {})
        cities = sorted({r["location"]["city"] for r in recs
                         if r["location"]["city"]})
        brand_rows.append({
            "brand": brand,
            "chain_id": recs[0]["chain_id"],
            "source_id": recs[0]["source_id"],
            "source_id_text": f'="{recs[0]["source_id"]}"',
            "google_locations": len(recs),
            "cities": ", ".join(cities),
            "outlet_type": recs[0]["outlet_type"],
            "chain_type": recs[0]["chain_type"] or "N/A",
            "restaurant_type": recs[0]["restaurant_type"],
            "with_phone": sum(1 for r in recs if r["contact_phone"] != "NA"),
            "with_website": sum(1 for r in recs if r["website"] != "NA"),
            "with_maps_url": sum(1 for r in recs if r["maps_url"] != "NA"),
            "talabat_area": (b.get("location") or {}).get("area"),
            "menu_items": len(recs[0]["menu_items"]),
        })
    with open(OUT_BRAND, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(brand_rows[0].keys()))
        w.writeheader()
        w.writerows(brand_rows)

    ok = sum(1 for r in rows if r["exact_match"] == "YES")
    unk = sum(1 for r in rows if r["exact_match"] == "?")
    print(f"  exact_match YES : {ok:,} / {len(rows):,}")
    print(f"  could not re-check (title not recoverable): {unk:,}")
    print(f"  NO : {sum(1 for r in rows if r['exact_match'] == 'NO'):,}"
          "   <- must be 0")

    multi = [b for b in brand_rows if b["google_locations"] > 1]
    print(f"\n  brands with >1 location: {len(multi):,}")
    print(f"  brands with exactly 1  : {len(brand_rows)-len(multi):,}")
    print("\n  top brands by location count:")
    print(f"     {'brand':32} {'locs':>4}  {'cities':28} chain_id")
    for b in brand_rows[:15]:
        print(f"     {b['brand'][:30]:32} {b['google_locations']:>4}  "
              f"{b['cities'][:26]:28} {b['chain_id']}")

    print(f"\n  -> {OUT_LOC.name}   ({len(rows):,} rows)")
    print(f"  -> {OUT_BRAND.name} ({len(brand_rows):,} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
