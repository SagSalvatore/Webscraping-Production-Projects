"""
Load Google Maps / web-search enrichment data into Postgres via async bulk COPY.

Sources:
  output/Final_Master_Mapping.csv        (16,053 rows = 15,749 unique branches;
                                           151 branches have 2-11 rows each = confirmed
                                           hidden-chain locations)
  New_Restro/New_Discovered_Listings.csv (18,806 non-Talabat businesses)

Writes:
  talabat_restaurants   <- one representative row per branch_id (address, website,
                            rating, review_count, google_maps_url, google_lat/lon,
                            verification_status, data_source, brand_type, notes).
                            For hidden chains, the representative is the location
                            with the highest review_count (most-reviewed = most
                            credible listing).
  talabat_chain_locations <- ALL individual locations for the 151 hidden-chain
                            branches (including the one already summarized above),
                            so the "N real locations" signal isn't lost.
  discovered_uae_food_businesses <- straight bulk load, no join (no branch_id exists
                            for these businesses).

Run:
  python talabat/map_version2/load_enrichment_data.py
"""

import asyncio
import re
import time
from pathlib import Path

import asyncpg
import numpy as np
import pandas as pd
from loguru import logger

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_DSN  # noqa: E402

HERE = Path(__file__).parent
MASTER_CSV = HERE / "output" / "Final_Master_Mapping.csv"
DISCOVERED_CSV = HERE / "New_Restro" / "New_Discovered_Listings.csv"

DB_DSN = PG_DSN

COORD_PATTERN = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")


def none_if_nan(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def extract_coords(url):
    if not isinstance(url, str):
        return None, None
    m = COORD_PATTERN.search(url)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


async def main():
    t_start = time.time()

    # ── 1. Load + prep Master Mapping ────────────────────────────────────────
    mm = pd.read_csv(MASTER_CSV)
    mm["Review_Count"] = mm["Review_Count"].fillna(0)

    dup_counts = mm.groupby("Branch_ID").size()
    hidden_chain_ids = set(dup_counts[dup_counts > 1].index)
    logger.info(f"Master rows: {len(mm):,} | unique branches: {mm['Branch_ID'].nunique():,} | hidden chains: {len(hidden_chain_ids):,}")

    # ── 2. Representative row per branch (highest review_count wins ties -> first) ──
    mm_sorted = mm.sort_values("Review_Count", ascending=False)
    representative = mm_sorted.drop_duplicates(subset="Branch_ID", keep="first").copy()
    logger.info(f"Representative rows for talabat_restaurants update: {len(representative):,}")

    restaurant_rows = []
    for r in representative.itertuples():
        lat, lon = extract_coords(r.Google_Maps_URL)
        restaurant_rows.append(
            (
                none_if_nan(r.Address),
                none_if_nan(r.Website),
                none_if_nan(r.Rating),
                int(r.Review_Count) if pd.notna(r.Review_Count) else None,
                none_if_nan(r.Google_Maps_URL),
                lat,
                lon,
                none_if_nan(r.Final_Status),
                none_if_nan(r.Data_Source),
                none_if_nan(r.Brand_Type),
                none_if_nan(r.Notes),
                none_if_nan(r.Phone),
                int(r.Branch_ID),
            )
        )

    # ── 3. Child-table rows: every location for hidden-chain branches ───────
    chain_df = mm[mm["Branch_ID"].isin(hidden_chain_ids)].sort_values(["Branch_ID", "Review_Count"], ascending=[True, False])
    chain_rows = []
    seq_counter = {}
    for r in chain_df.itertuples():
        bid = int(r.Branch_ID)
        seq_counter[bid] = seq_counter.get(bid, 0) + 1
        chain_rows.append(
            (
                bid,
                int(r.Restaurant_ID) if pd.notna(r.Restaurant_ID) else None,
                seq_counter[bid],
                none_if_nan(r.Address),
                none_if_nan(r.Phone),
                none_if_nan(r.Website),
                none_if_nan(r.Google_Maps_URL),
                none_if_nan(r.Rating),
                int(r.Review_Count) if pd.notna(r.Review_Count) else None,
                none_if_nan(r.Data_Source),
                none_if_nan(r.Notes),
            )
        )
    logger.info(f"Chain-location rows to insert: {len(chain_rows):,} across {len(hidden_chain_ids):,} branches")

    # ── 4. Discovered (non-Talabat) businesses ───────────────────────────────
    disc = pd.read_csv(DISCOVERED_CSV)
    discovered_rows = []
    for r in disc.itertuples():
        discovered_rows.append(
            (
                none_if_nan(r.Name),
                none_if_nan(r.Address),
                none_if_nan(r.Phone),
                none_if_nan(r.Website),
                none_if_nan(r.Google_Maps_URL),
                none_if_nan(r.Category),
                none_if_nan(r.Latitude),
                none_if_nan(r.Longitude),
                none_if_nan(r.Source),
            )
        )
    logger.info(f"Discovered-business rows to insert: {len(discovered_rows):,}")

    # ── 5. Async bulk write ───────────────────────────────────────────────────
    conn = await asyncpg.connect(DB_DSN)
    async with conn.transaction():
        await conn.execute(
            """
            CREATE TEMP TABLE staging_restaurant_enrich (
                address TEXT, website TEXT, rating NUMERIC, review_count INTEGER,
                google_maps_url TEXT, google_lat NUMERIC, google_lon NUMERIC,
                verification_status TEXT, data_source TEXT, brand_type TEXT,
                enrichment_notes TEXT, contact TEXT, branch_id BIGINT
            ) ON COMMIT DROP
            """
        )
        await conn.copy_records_to_table(
            "staging_restaurant_enrich",
            records=restaurant_rows,
            columns=["address", "website", "rating", "review_count", "google_maps_url",
                     "google_lat", "google_lon", "verification_status", "data_source",
                     "brand_type", "enrichment_notes", "contact", "branch_id"],
        )
        result = await conn.execute(
            """
            UPDATE talabat_restaurants t
            SET address = s.address,
                website = s.website,
                rating = s.rating,
                review_count = s.review_count,
                google_maps_url = s.google_maps_url,
                google_lat = s.google_lat,
                google_lon = s.google_lon,
                verification_status = s.verification_status,
                data_source = s.data_source,
                brand_type = s.brand_type,
                enrichment_notes = s.enrichment_notes,
                contact = s.contact,
                updated_at = NOW()
            FROM staging_restaurant_enrich s
            WHERE t.branch_id = s.branch_id
            """
        )
        logger.info(f"talabat_restaurants update: {result}")

        await conn.copy_records_to_table(
            "talabat_chain_locations",
            records=chain_rows,
            columns=["branch_id", "restaurant_id", "location_seq", "address", "phone",
                     "website", "google_maps_url", "rating", "review_count",
                     "data_source", "notes"],
        )
        logger.info(f"talabat_chain_locations insert: {len(chain_rows):,} rows")

        await conn.copy_records_to_table(
            "discovered_uae_food_businesses",
            records=discovered_rows,
            columns=["name", "address", "phone", "website", "google_maps_url",
                     "category", "latitude", "longitude", "source"],
        )
        logger.info(f"discovered_uae_food_businesses insert: {len(discovered_rows):,} rows")

    await conn.close()
    logger.info(f"Done in {time.time()-t_start:.1f}s total")


if __name__ == "__main__":
    asyncio.run(main())
