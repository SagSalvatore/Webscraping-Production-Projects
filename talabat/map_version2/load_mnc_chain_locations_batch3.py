"""
Batch 3 of mnc_chain_locations loading: New_brands.csv (26 rows, 2 brands).

Findings from inspection (see conversation) before writing this loader:

  "Shak Shak" (15 rows, source_id 774480) — every row is an exact duplicate
  (same address, phone, and lat/lng recoverable from the google_map_urls
  @lat,lng) of the 15 Shake Shack rows already in mnc_chain_locations
  (id 1639-1653). Nothing new to insert. The real defect was that branch
  774480's raw Talabat name "Shak Shak" (missing letters vs "Shake Shack")
  never matched MNC_BRAND_CONFIG's old strict regex — fixed directly in
  export_to_json.py (pattern now also matches "shak shak"). This script does
  NOT touch Shake Shack at all.

  "Haagen Dazs Delights" (11 rows, source_id 733885) — genuinely new brand,
  0 prior rows in mnc_chain_locations. 8 Talabat branches already exist under
  two raw name variants ("Haagen Dazs" x5, "Haagen Dazs Delights" x3), both
  matched by the new MNC_BRAND_CONFIG entry ("Häagen-Dazs", r"\\bhaagen dazs\\b").
  google_map_urls are all /maps/search/?... (no embeddable place_id or
  lat/lng, same limitation as Peet's Coffee) -> latitude/longitude stored as
  NULL, place_id synthesized as a stable hash of brand+address (same pattern
  as load_mnc_chain_locations_batch2.py's "simple" schema), so re-running
  this script upserts cleanly instead of duplicating.

  5 of the 11 contact numbers are stored in the source CSV itself as lossy
  scientific notation (e.g. "9.71568E+11" -- confirmed present in the raw
  file bytes, not a pandas artifact) with the trailing digits unrecoverable.
  Stored as NULL rather than guessed, per "never silently impute a
  high-trust factual field."

Run:
  python talabat/map_version2/load_mnc_chain_locations_batch3.py
"""

import hashlib
import re

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
CSV_PATH = "talabat/map_version2/New_brands.csv"

BRAND_NAME = "Häagen-Dazs"
TALABAT_REGEX = r"\bhaagen dazs\b"


def normalize_name(s):
    s = str(s).lower()
    s = re.sub(r"[''`]", "", s)
    s = re.sub(r"[^\w\s%]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_address(raw):
    parts = [p.strip() for p in str(raw).split("\n") if p.strip()]
    return ", ".join(parts)


def clean_phone(raw):
    s = str(raw).strip()
    return s if re.fullmatch(r"\d{6,15}", s) else None


def derive_emirate(address):
    low = address.lower()
    if "abu dhabi" in low:
        return "Abu Dhabi"
    if "dubai" in low:
        return "Dubai"
    return None


def main():
    df = pd.read_csv(CSV_PATH, dtype=str)
    df.columns = ["source_id", "name", "website", "contact", "address", "google_map_url"]

    hd = df[df["name"] == "Haagen Dazs Delights"].reset_index(drop=True)
    logger.info(f"Loaded {len(hd)} Häagen-Dazs rows from {CSV_PATH}")

    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()

    all_talabat = pd.read_sql("SELECT branch_id, restaurant_id, restaurant_name FROM talabat_restaurants", conn)
    all_talabat["norm_name"] = all_talabat["restaurant_name"].apply(normalize_name)
    talabat_brand = all_talabat[all_talabat["norm_name"].str.contains(TALABAT_REGEX, regex=True, na=False)]

    if talabat_brand.empty:
        logger.warning(f"{BRAND_NAME}: no Talabat branches found — restaurant_id will be NULL for all rows")
        canonical_restaurant_id = None
    else:
        canonical_restaurant_id = int(talabat_brand["restaurant_id"].mode().iloc[0])
        dominant_share = (talabat_brand["restaurant_id"] == canonical_restaurant_id).mean()
        logger.info(
            f"{BRAND_NAME}: {len(talabat_brand)} Talabat branches found, canonical restaurant_id={canonical_restaurant_id} "
            f"({dominant_share*100:.1f}% concentration)"
        )

    insert_rows = []
    for r in hd.itertuples():
        address = clean_address(r.address)
        place_id = "manual-" + hashlib.md5(f"{BRAND_NAME}|{address}".encode()).hexdigest()
        insert_rows.append(
            (
                BRAND_NAME,
                canonical_restaurant_id,
                place_id,
                BRAND_NAME,
                address,
                None,  # street
                None,  # neighborhood
                derive_emirate(address),
                clean_phone(r.contact),
                r.website,
                r.google_map_url,
                None,  # latitude
                None,  # longitude
                None,  # category
                None,  # rating
                None,  # review_count
                None,  # match_distance_km
                "Manual MNC Scrape (Web Directory)",
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
    null_phones = sum(1 for r in insert_rows if r[8] is None)
    logger.info(f"{BRAND_NAME}: {len(insert_rows)} locations loaded, {null_phones} with unrecoverable phone numbers (stored NULL)")

    conn.commit()
    conn.close()
    logger.info("Batch 3 done.")


if __name__ == "__main__":
    main()
