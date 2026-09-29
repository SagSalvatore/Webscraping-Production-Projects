"""
Load big multi-national chain brands' complete real-world UAE footprint into
mnc_chain_locations — a brand-agnostic table designed to grow as more chain
CSVs are scraped (McDonald's, Starbucks, Subway, etc. from the 46-brand
allowlist in workdone.md).

Why a separate table from talabat_chain_locations:
  talabat_chain_locations = ambiguous candidates FOR one specific Talabat
                             branch_id (we don't know which of N results is
                             this branch).
  mnc_chain_locations      = the definitive, independent census of a brand's
                             full UAE footprint. Some rows link back to a
                             Talabat restaurant_id (a Talabat listing exists
                             for that exact physical location); most don't,
                             which is itself the "hidden chain" signal for
                             brands this large (e.g. Talabat lists 12 of
                             KFC's 247 real UAE locations).

Linked via restaurant_id rather than branch_id: restaurant_id is Talabat's
own brand-level identifier — every branch_id/area_id for a given brand in
the UAE shares the same restaurant_id (confirmed: 11/12 KFC branches share
restaurant_id=1968, 6/7 Domino's branches share 8516; the lone outlier in
each is a data-quality glitch in Talabat's own scrape, not a second valid
brand entity). So restaurant_id is assigned to EVERY row of a brand's
locations in this table — the canonical value taken as the mode across all
of that brand's Talabat branches — regardless of whether that specific
real-world location also has a precise geo-match to one of Talabat's
listings. restaurant_id is NOT unique in talabat_restaurants (one brand
spans several branch_ids), so this column carries no FK constraint — it's
a plain join key, not enforced referential integrity.

match_distance_km is the separate, per-row signal for "does this specific
real-world location also have its own Talabat delivery listing" — NULL
means the brand is confirmed (restaurant_id is set) but no specific
Talabat branch was found within 100m of this exact location.

Sources (each must have columns: place_id, Name, Address, Street,
Neighborhood, Contact_No, Google_Maps_URL, Geo_Lat, Geo_Lng, Category,
Rating, Review_Count, Emirate):
  KFC_UAE.csv
  DOMINOS_UAE.csv

Matching: nearest Talabat branch_id (by name + Haversine distance) is linked
only when the closest match is within a tight threshold (100m) — the same
precision that produced 19/19 clean, collision-free matches earlier.

Re-runnable: upserts on place_id (ON CONFLICT DO UPDATE), so re-running this
after a refreshed brand CSV (updated ratings/review counts, newly opened
locations) safely overwrites existing rows instead of skipping them.

Run:
  python talabat/map_version2/load_mnc_chain_locations.py
"""

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

BRAND_FILES = {
    "KFC": "KFC_UAE.csv",
    "Domino's Pizza": "DOMINOS_UAE.csv",
}

MATCH_THRESHOLD_KM = 0.1  # 100 meters


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


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()

    talabat = pd.read_sql(
        """
        SELECT branch_id, restaurant_id, restaurant_name, ld_lat, ld_lon
        FROM talabat_restaurants
        WHERE restaurant_name IN ('KFC', 'Domino''s Pizza') OR restaurant_name LIKE 'KFCin%'
        """,
        conn,
    )
    talabat["brand"] = talabat["restaurant_name"].apply(lambda x: "KFC" if x.startswith("KFC") else "Domino's Pizza")

    total_inserted = 0
    total_linked = 0

    for brand_name, csv_file in BRAND_FILES.items():
        google_df = pd.read_csv(csv_file)
        talabat_brand = talabat[talabat["brand"] == brand_name]

        if talabat_brand.empty:
            logger.warning(f"{brand_name}: no Talabat branches found under this name — skipping brand-id assignment (all rows will have restaurant_id=NULL)")
            canonical_restaurant_id = None
        else:
            # canonical brand-level id = mode across ALL Talabat branches for this brand in UAE
            canonical_restaurant_id = int(talabat_brand["restaurant_id"].mode().iloc[0])
            dissenters = talabat_brand[talabat_brand["restaurant_id"] != canonical_restaurant_id]
            if not dissenters.empty:
                logger.warning(
                    f"{brand_name}: {len(dissenters)} Talabat branch(es) carry a different restaurant_id "
                    f"than the canonical {canonical_restaurant_id} (data-quality outliers, not used): "
                    f"{dissenters[['branch_id','restaurant_id']].values.tolist()}"
                )

        # separately: find the nearest Talabat branch for each Google location, to flag
        # which specific real-world locations also have their own Talabat delivery listing
        distance_by_place_id = {}
        for t in talabat_brand.itertuples():
            dists = haversine(float(t.ld_lat), float(t.ld_lon), google_df["Geo_Lat"].values, google_df["Geo_Lng"].values)
            idx = np.argmin(dists)
            if dists[idx] <= MATCH_THRESHOLD_KM:
                pid = google_df.iloc[idx]["place_id"]
                distance_by_place_id[pid] = round(float(dists[idx]), 4)

        rows = []
        for r in google_df.itertuples():
            restaurant_id = canonical_restaurant_id
            dist = distance_by_place_id.get(r.place_id)
            rows.append(
                (
                    brand_name,
                    restaurant_id,
                    r.place_id,
                    clean(r.Name),
                    clean(r.Address),
                    clean(r.Street),
                    clean(r.Neighborhood),
                    clean(r.Emirate),
                    clean(r.Contact_No),
                    None,  # website not present in these CSVs
                    clean(r.Google_Maps_URL),
                    float(r.Geo_Lat),
                    float(r.Geo_Lng),
                    clean(r.Category),
                    clean(r.Rating),
                    int(r.Review_Count) if pd.notna(r.Review_Count) else None,
                    dist,
                    "Manual MNC Scrape (Google Maps)",
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
            rows,
        )
        specific_matches = sum(1 for r in rows if r[16] is not None)  # match_distance_km column
        logger.info(
            f"{brand_name}: {len(rows)} locations tagged restaurant_id={canonical_restaurant_id}, "
            f"{specific_matches} also matched to a specific Talabat branch within {int(MATCH_THRESHOLD_KM*1000)}m"
        )
        total_inserted += len(rows)
        total_linked += specific_matches

    conn.commit()
    logger.info(f"Total: {total_inserted} rows inserted, {total_linked} with a specific Talabat branch match")
    conn.close()


if __name__ == "__main__":
    main()
