"""Pull every location record for one brand out of the source exports, as CSV.

Reads the SOURCE exports, not the unified file - unified has been through "NA"
normalisation, city kNN fills and label fixes, so its address fields are our
processing rather than what each cohort shipped.

TWO KINDS OF RECORD live in these files, and the CSV keeps them apart:
    talabat_listing           one per Talabat branch
    google_verified_location  Google Maps locations fanned out for chain brands,
                              each REPEATING the representative branch's
                              source_id - so source_id is not unique here
Every row therefore carries `row_id` = cohort prefix + position in its file,
which is unique and stable for that file.

MATCHING, loosest first, and every distinct matched name is printed so a false
positive is visible before anyone uses the file:
    name      the brand's first word starts a word in the name - catches
              'Baskin-Robbins', 'BASKIN ROBBINS', 'Baskin Robins', 'Baskins'
    arabic    an optional Arabic alias appears in the name
    chain_id  the name does not say it, but the row shares a chain_id with a
              row whose name contains the FULL brand - so one loose false
              positive cannot pull a whole unrelated chain in

DERIVED CHECK COLUMNS - the file's own address values are never altered, but
they are checked, because they can be wrong in a way that looks correct:
    city_from_coordinates  kNN k=3 over Google verified-location records, whose
                           address and coordinates come from the SAME listing -
                           the fill_cities.py method, measured 99.45% held-out.
                           Blank beyond 15 km of any verified point.
    city_check             match / MISMATCH / file city blank / no coordinates
    raw_shared_by          how many matched rows carry this exact raw address
    talabat_url            the restaurant's Talabat page, joined on branch_id
                           from the local scrape-target files (the exports do
                           not carry a url field)
    area_from_url_slug     the area Talabat puts in that URL after the name -
                           '/21097/baskin-robbins-al-barsha' -> 'al barsha'. It
                           is Talabat's own area for the branch, the same free
                           recovery tier september/sanitize_areas.py uses
    coords_map_url         a Google Maps link to the row's own coordinates, so
                           a reviewer can see where the branch really is

The export's own maps_url is kept as-is. For Baskin Robbins' 7 listings it is
one Google place_id repeated - the single listing whose address was copied onto
every branch.

Why they exist: Baskin Robbins' 7 July listings all read "QWJR+387 - Khoor Ras
Al Khaimah - Ras Al Khaimah", yet their coordinates sit in Downtown Dubai, Al
Barsha and Abu Dhabi. One Google listing's address was applied to every branch
of the brand - the brand-level smear the area and city work already paid for.
Without these columns the CSV would state "Ras Al Khaimah" seven times and be
believed.

    python brand_locations.py --brand "baskin robbins" --arabic "باسكن" --dry-run
    python brand_locations.py --brand "baskin robbins" --arabic "باسكن"
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

import ijson
import numpy as np
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
SOURCES = [
    ("JUL", "july", HERE / "export" / "talabat_export.json"),
    ("AUG", "august", HERE / "August_menu" / "data" / "August_export.json"),
    ("SEP", "september", HERE / "september" / "data" / "September_export.json"),
]
OUT_DIR = HERE / "data" / "brand_locations"
COLS = ["row_id", "cohort", "record_type", "match_basis", "source_id", "name",
        "talabat_url", "location_raw", "area", "area_from_url_slug", "city",
        "city_from_coordinates", "city_check", "raw_shared_by", "sublocality",
        "country", "lat", "lng", "maps_url", "coords_map_url", "contact_phone",
        "website", "chain_id", "outlet_type", "chain_type",
        "chain_locations_count", "menu_item_count", "source_file"]
# branch_id -> url, first file wins. Local files only: no network, no Postgres.
URL_SOURCES = [
    HERE / "menu_refresh" / "data" / "refresh_202609" / "scrape_targets_202609.jsonl",
    HERE / "menu_refresh" / "data" / "scrape_targets.jsonl",
    HERE / "August_menu" / "data" / "restaurant_status.jsonl",
    HERE / "september" / "data" / "sept_restaurants_for_classification.jsonl",
]
_SLUG = re.compile(r"/restaurant/\d+/([^?/#]+)")
# same canonical forms and parameters as August_menu/fill_cities.py, so the
# 99.45% held-out figure describes this check too
CANON = {"Ras Al-Khaimah": "Ras Al Khaimah", "Kalba": "Sharjah"}
K, MAX_KM = 3, 15.0
sys.stdout.reconfigure(encoding="utf-8")


def as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def add_urls(hit):
    """talabat_url, area_from_url_slug and coords_map_url on each hit."""
    want = {str(r["source_id"]) for r in hit}
    url = {}
    for p in URL_SOURCES:
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                t = json.loads(line)
                b = str(t.get("branch_id"))
                if b in want and t.get("url"):
                    url.setdefault(b, t["url"])
    for r in hit:
        u = url.get(str(r["source_id"]), "")
        r["talabat_url"] = u
        # same rule as september/sanitize_areas.slug_area: strip the leading
        # restaurant name from the slug, keep the tail, never guess where the
        # name ends if the slug does not start with it
        m = _SLUG.search(u)
        tail = ""
        if m:
            slug = m.group(1).replace("-", " ").strip()
            nm = re.sub(r"\s+", " ",
                        re.sub(r"[^a-z0-9 ]", " ", (r["name"] or "").lower())).strip()
            if nm and slug.lower().startswith(nm):
                tail = slug[len(nm):].strip()
        r["area_from_url_slug"] = tail
        la, ln = as_float(r["lat"]), as_float(r["lng"])
        r["coords_map_url"] = (f"https://www.google.com/maps?q={la},{ln}"
                               if la is not None and ln is not None else "")
    return sum(1 for r in hit if r["talabat_url"])


def check_cities(rows, hit):
    """Fill city_from_coordinates / city_check / raw_shared_by on the hits.
    Labels come only from verified-location records already scanned."""
    X, y = [], []
    for r in rows:
        la, ln = as_float(r["lat"]), as_float(r["lng"])
        if (r["record_type"] == "google_verified_location" and r["city"]
                and la is not None and ln is not None):
            X.append((la, ln))
            y.append(CANON.get(r["city"], r["city"]))
    raw_n = Counter(r["location_raw"] for r in hit if r["location_raw"])
    tree = cKDTree(np.array(X)) if len(X) >= K else None
    y = np.array(y)
    for r in hit:
        r["raw_shared_by"] = raw_n.get(r["location_raw"], 0)
        la, ln = as_float(r["lat"]), as_float(r["lng"])
        if la is None or ln is None or tree is None:
            r["city_from_coordinates"], r["city_check"] = "", "no coordinates"
            continue
        dist, idx = tree.query([(la, ln)], k=K)
        if dist[0][0] * 111 > MAX_KM:
            r["city_from_coordinates"] = ""
            r["city_check"] = "no verified point within 15 km"
            continue
        cc = Counter(y[idx[0]]).most_common(1)[0][0]
        fc = CANON.get(r["city"], r["city"]) if r["city"] else ""
        r["city_from_coordinates"] = cc
        r["city_check"] = ("file city blank" if not fc
                           else "match" if fc == cc else "MISMATCH")
    return len(X)


def compact(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def words(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def light(rec, prefix, cohort, i, path):
    """Address-level fields only. menu_items can run to thousands of entries,
    so only its length is kept - the full record is never held."""
    loc = rec.get("location") or {}
    geo = rec.get("geo") or {}
    return {
        "row_id": f"{prefix}-{i:06d}",
        "cohort": cohort,
        "record_type": ("google_verified_location"
                        if rec.get("is_verified_location") else "talabat_listing"),
        "match_basis": "",
        "source_id": rec.get("source_id"),
        "name": rec.get("name"),
        "location_raw": loc.get("raw"),
        "area": loc.get("area"),
        "city": loc.get("city"),
        "sublocality": loc.get("sublocality"),
        "country": loc.get("country"),
        "lat": geo.get("lat"),
        "lng": geo.get("lng"),
        "maps_url": rec.get("maps_url"),
        "contact_phone": rec.get("contact_phone"),
        "website": rec.get("website"),
        "chain_id": rec.get("chain_id"),
        "outlet_type": rec.get("outlet_type"),
        "chain_type": rec.get("chain_type"),
        "chain_locations_count": rec.get("chain_locations_count"),
        "menu_item_count": len(rec.get("menu_items") or []),
        "source_file": str(path.relative_to(HERE)),
    }


def main(args):
    head = words(args.brand)[0]
    full = compact(args.brand)
    print("=" * 78)
    print(f"  BRAND LOCATIONS  -  '{args.brand}'   (head word '{head}'"
          + (f", arabic '{args.arabic}'" if args.arabic else "") + ")")
    print("=" * 78)

    rows, scanned = [], Counter()
    for prefix, cohort, path in SOURCES:
        if not path.exists():
            print(f"  {cohort:10} MISSING {path}")
            continue
        with open(path, "rb") as f:
            # use_float: ijson yields Decimal otherwise, which csv writes as
            # '24.4539000000000004' noise and json cannot serialise
            for i, rec in enumerate(ijson.items(f, "item", use_float=True)):
                lt = light(rec, prefix, cohort, i, path)
                scanned[(cohort, lt["record_type"])] += 1
                rows.append(lt)
        print(f"  {cohort:10} scanned "
              f"{scanned[(cohort, 'talabat_listing')]:>7,} listings | "
              f"{scanned[(cohort, 'google_verified_location')]:>6,} verified locations")

    for r in rows:
        nm = r["name"] or ""
        if any(w.startswith(head) for w in words(nm)):
            r["match_basis"] = "name"
        elif args.arabic and args.arabic in nm:
            r["match_basis"] = "arabic"

    trusted = {r["chain_id"] for r in rows
               if r["match_basis"] and full in compact(r["name"])
               and r["chain_id"] is not None}
    for r in rows:
        if not r["match_basis"] and r["chain_id"] in trusted:
            r["match_basis"] = "chain_id"

    hit = [r for r in rows if r["match_basis"]]
    n_lab = check_cities(rows, hit)
    n_url = add_urls(hit)
    print(f"\n  MATCHED {len(hit):,} rows   "
          f"(city check against {n_lab:,} verified-location points | "
          f"talabat url found for {n_url:,})")
    for k, n in sorted(Counter((r["cohort"], r["record_type"], r["match_basis"])
                               for r in hit).items()):
        print(f"     {n:>5}  {k[0]:9} {k[1]:25} via {k[2]}")

    print(f"\n  distinct names ({len({r['name'] for r in hit})}):")
    for nm, n in Counter(r["name"] for r in hit).most_common():
        print(f"     {n:>5}  {nm}")
    print(f"\n  chain_ids: {dict(Counter(r['chain_id'] for r in hit).most_common())}")
    print(f"  cities   : {dict(Counter(r['city'] or '(blank)' for r in hit).most_common())}")
    for fld in ("location_raw", "area", "city", "lat"):
        miss = sum(1 for r in hit if r[fld] in (None, "", "NA"))
        print(f"  {fld:13} blank on {miss:,} of {len(hit):,}")
    print(f"\n  city_check: {dict(Counter(r['city_check'] for r in hit).most_common())}")
    for r in hit:
        if r["city_check"] == "MISMATCH" or r["raw_shared_by"] > 1:
            print(f"     {r['row_id']}  {str(r['name'])[:26]:28} file={str(r['city'])[:15]:16}"
                  f"coords={r['city_from_coordinates']:15} raw shared by {r['raw_shared_by']}")
    rep = Counter(r["source_id"] for r in hit)
    print(f"  source_ids repeated across rows: "
          f"{sum(1 for v in rep.values() if v > 1):,} "
          f"(expected for verified-location fan-out)")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{compact(args.brand)}_locations.csv"
    hit.sort(key=lambda r: (r["cohort"], r["record_type"], r["city"] or "",
                            r["area"] or "", r["name"] or ""))
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(hit)
    print(f"\n  -> {out.relative_to(HERE)}  ({len(hit):,} rows)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--brand", required=True)
    p.add_argument("--arabic", default="")
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
