"""
Batch 2 of mnc_chain_locations loading: 23 more MNC brands beyond KFC/Domino's
(see load_mnc_chain_locations.py for the original KFC/Domino's load and the
full design rationale for this table).

Two source schemas in this batch:

  "rich"   — 22 brand-folder CSVs (<brand>/output/*.csv), same shape as
             KFC_UAE.csv/DOMINOS_UAE.csv: place_id, Name, Emirate, Area, City,
             Address, Street, Neighborhood, Contact_No, Website,
             Google_Maps_URL, Geo_Lat, Geo_Lng, Category, All_Categories,
             Rating, Review_Count, Search_Query.

  "simple" — Starbucks only (starbucks/starbucks.csv): Restaurant, Address,
             urls, website, contact(phone_number). No place_id, no lat/lon,
             no rating. A synthetic place_id is derived (stable hash of
             brand+address) so re-runs upsert cleanly instead of duplicating.

Costa Coffee now has its own dedicated folder (costa_coffee/output/*.csv,
"rich" schema) — the legacy CHAIN.csv extraction path was never needed.

restaurant_id assignment: same rule as batch 1 — canonical value is the mode
across all of that brand's Talabat branches, applied to every row regardless
of a specific geo-match. For decentralized/franchised brands (Starbucks,
McDonald's, Subway, Shake Shack — where most branches DON'T share one
restaurant_id), this canonical value is a best-effort majority pick, not a
universal brand key; the concentration is logged per brand so low-confidence
ones are visible, not silently trusted.

Run:
  python talabat/map_version2/load_mnc_chain_locations_batch2.py
"""

import glob
import hashlib
import re

import numpy as np
import pandas as pd
import psycopg2
from loguru import logger

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_PASSWORD  # noqa: E402

DB_PARAMS = dict(host="localhost", port=5432, database="RestaurantIntelligence", user="postgres", password=PG_PASSWORD)
MATCH_THRESHOLD_KM = 0.1  # 100 meters

# brand_name -> (folder glob pattern, schema, talabat restaurant_name regex)
BRANDS = [
    ("ALBAIK",             "albaik/output/*.csv",             "rich",   r"\balbaik\b"),
    ("% Arabica",          "arabica/output/*.csv",            "rich",   r"%\s*arabica"),
    ("BurgerFuel",         "burger_fuel/output/*.csv",        "rich",   r"\bburger fuel\b"),
    ("Burger King",        "burger_king/output/*.csv",        "rich",   r"\bburger king\b"),
    ("Costa Coffee",       "costa_coffee/output/*.csv",       "rich",   r"\bcosta coffee\b"),
    ("Caffè Nero",         "caffe_nero/output/*.csv",         "rich",   r"\bcaffe nero\b"),
    ("Caribou Coffee",     "caribou_coffee/output/*.csv",     "rich",   r"\bcaribou\b"),
    ("Dunkin' Donuts",     "dunkin_donuts/output/*.csv",      "rich",   r"\bdunkin\b"),
    ("FiLLi Cafe",         "filli_cafe/output/*.csv",         "rich",   r"\bfilli\b"),
    ("Five Guys",          "five_guys/output/*.csv",          "rich",   r"\bfive guys\b"),
    ("German Doner Kebab", "german_doner_kebab/output/*.csv", "rich",   r"\bgerman doner kebab\b|\bgdk\b"),
    ("Jollibee",           "jollibee/output/*.csv",           "rich",   r"\bjollibee\b"),
    ("McDonald's",         "mcdonalds/output/*.csv",          "rich",   r"\bmcdonalds\b"),
    ("New York Fries",     "new_york_fries/output/*.csv",     "rich",   r"\bnew york fries\b"),
    ("Pickl",              "pickl/output/*.csv",              "rich",   r"\bpickl\b"),
    ("Pizza Hut",          "pizza_hut/output/*.csv",          "rich",   r"\bpizza hut\b"),
    ("Popeyes",            "popeyes/output/*.csv",            "rich",   r"\bpopeyes\b"),
    ("Pret A Manger",      "pret_a_manger/output/*.csv",      "rich",   r"\bpret a manger\b"),
    ("Shake Shack",        "shake_shack/output/*.csv",        "rich",   r"\bshake shack\b"),
    ("Subway",             "subway/output/*.csv",             "rich",   r"\bsubway\b"),
    ("Tim Hortons",        "tim_hortons/output/*.csv",        "rich",   r"\btim hortons\b"),
    ("Wendy's",            "wendys/output/*.csv",             "rich",   r"\bwendys\b"),
    ("Zaatar w Zeit",      "zaatar_w_zeit/output/*.csv",      "rich",   r"\bzaatar w ?zeit\b"),
    ("Starbucks",          "starbucks/starbucks.csv",         "simple", r"\bstarbucks\b"),
]


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def clean(v):
    if v is None:
        return None
    if isinstance(v, (float, np.floating)) and np.isnan(v):
        return None
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, np.integer):
        return int(v)
    return v


def normalize_name(s):
    s = str(s).lower()
    s = re.sub(r"[''`]", "", s)
    s = re.sub(r"[^\w\s%]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_rich(path):
    df = pd.read_csv(path)
    rows = []
    for r in df.itertuples():
        rows.append(
            {
                "place_id": r.place_id,
                "name": clean(r.Name),
                "address": clean(r.Address),
                "street": clean(r.Street),
                "neighborhood": clean(r.Neighborhood),
                "emirate": clean(r.Emirate),
                "phone": clean(r.Contact_No),
                "website": clean(getattr(r, "Website", None)),
                "google_maps_url": clean(r.Google_Maps_URL),
                "latitude": float(r.Geo_Lat) if pd.notna(r.Geo_Lat) else None,
                "longitude": float(r.Geo_Lng) if pd.notna(r.Geo_Lng) else None,
                "category": clean(r.Category),
                "rating": clean(r.Rating),
                "review_count": int(r.Review_Count) if pd.notna(r.Review_Count) else None,
            }
        )
    return rows


def load_simple(path, brand_name):
    df = pd.read_csv(path, encoding="cp1252")
    rows = []
    for r in df.itertuples():
        address = clean(r.Address)
        synthetic_id = "manual-" + hashlib.md5(f"{brand_name}|{address}".encode()).hexdigest()
        # addresses consistently end "... - <Emirate> - United Arab Emirates"
        parts = str(address).split(" - ") if address else []
        emirate = parts[-2].strip() if len(parts) >= 2 else None
        rows.append(
            {
                "place_id": synthetic_id,
                "name": clean(getattr(r, "Restaurant", brand_name)),
                "address": address,
                "street": None,
                "neighborhood": None,
                "emirate": emirate,
                "phone": clean(getattr(r, "_5", None)),  # contact(phone_number) -> sanitized attr name
                "website": clean(r.website),
                "google_maps_url": clean(r.urls),
                "latitude": None,
                "longitude": None,
                "category": None,
                "rating": None,
                "review_count": None,
            }
        )
    return rows


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()

    all_talabat = pd.read_sql("SELECT branch_id, restaurant_id, restaurant_name, ld_lat, ld_lon FROM talabat_restaurants", conn)
    all_talabat["norm_name"] = all_talabat["restaurant_name"].apply(normalize_name)

    total_inserted = 0
    total_linked = 0

    for brand_name, glob_pattern, schema, talabat_regex in BRANDS:
        files = glob.glob(glob_pattern)
        if not files:
            logger.error(f"{brand_name}: no file found for pattern {glob_pattern} — skipping")
            continue
        path = files[0]

        if schema == "rich":
            loc_rows = load_rich(path)
        else:
            loc_rows = load_simple(path, brand_name)

        talabat_brand = all_talabat[all_talabat["norm_name"].str.contains(talabat_regex, regex=True, na=False)]

        if talabat_brand.empty:
            logger.warning(f"{brand_name}: no Talabat branches found under this name — restaurant_id will be NULL for all rows")
            canonical_restaurant_id = None
        else:
            canonical_restaurant_id = int(talabat_brand["restaurant_id"].mode().iloc[0])
            dominant_share = (talabat_brand["restaurant_id"] == canonical_restaurant_id).mean()
            logger.info(
                f"{brand_name}: {len(talabat_brand)} Talabat branches found, canonical restaurant_id={canonical_restaurant_id} "
                f"({dominant_share*100:.1f}% concentration)"
            )

        # per-location geo-match distance (only possible when we have lat/lon on both sides)
        distance_by_place_id = {}
        if not talabat_brand.empty and schema == "rich":
            lats = np.array([row["latitude"] for row in loc_rows], dtype=float)
            lngs = np.array([row["longitude"] for row in loc_rows], dtype=float)
            for t in talabat_brand.itertuples():
                dists = haversine(float(t.ld_lat), float(t.ld_lon), lats, lngs)
                idx = np.nanargmin(dists)
                if dists[idx] <= MATCH_THRESHOLD_KM:
                    pid = loc_rows[idx]["place_id"]
                    distance_by_place_id[pid] = round(float(dists[idx]), 4)

        insert_rows = []
        for row in loc_rows:
            insert_rows.append(
                (
                    brand_name,
                    canonical_restaurant_id,
                    row["place_id"],
                    row["name"],
                    row["address"],
                    row["street"],
                    row["neighborhood"],
                    row["emirate"],
                    row["phone"],
                    row["website"],
                    row["google_maps_url"],
                    row["latitude"],
                    row["longitude"],
                    row["category"],
                    row["rating"],
                    row["review_count"],
                    distance_by_place_id.get(row["place_id"]),
                    "Manual MNC Scrape (Google Maps)" if schema == "rich" else "Manual MNC Scrape (Web Directory)",
                )
            )

        cur.executemany(
            """
            INSERT INTO mnc_chain_locations
                (brand_name, restaurant_id, place_id, name, address, street, neighborhood,
                 emirate, phone, website, google_maps_url, latitude, longitude,
                 category, rating, review_count, match_distance_km, data_source)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (place_id) DO UPDATE SET
                brand_name = EXCLUDED.brand_name,
                restaurant_id = EXCLUDED.restaurant_id,
                name = EXCLUDED.name,
                address = EXCLUDED.address,
                street = EXCLUDED.street,
                neighborhood = EXCLUDED.neighborhood,
                emirate = EXCLUDED.emirate,
                phone = EXCLUDED.phone,
                website = EXCLUDED.website,
                google_maps_url = EXCLUDED.google_maps_url,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                category = EXCLUDED.category,
                rating = EXCLUDED.rating,
                review_count = EXCLUDED.review_count,
                match_distance_km = EXCLUDED.match_distance_km,
                data_source = EXCLUDED.data_source,
                discovered_at = NOW()
            """,
            insert_rows,
        )
        specific_matches = sum(1 for r in insert_rows if r[16] is not None)
        logger.info(f"{brand_name}: {len(insert_rows)} locations loaded, {specific_matches} matched to a specific Talabat branch within 100m")
        total_inserted += len(insert_rows)
        total_linked += specific_matches

    conn.commit()
    logger.info(f"Batch 2 total: {total_inserted} rows inserted, {total_linked} with a specific Talabat branch match")
    conn.close()


if __name__ == "__main__":
    main()
