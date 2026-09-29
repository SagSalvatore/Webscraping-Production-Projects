#!/usr/bin/env python3
"""
Supabase -> Local PostgreSQL full migration.
Migrates all Talabat UAE tables from Supabase to local RestaurantIntelligence DB.

Run: python talabat/Postgre/migrate_supabase_to_local.py
Resume: safe to re-run — uses checkpoint file + ON CONFLICT DO NOTHING
"""

import os
import sys
import json
import time
import logging
from pathlib import Path

import psycopg2
import psycopg2.extras
from supabase import create_client, Client
from dotenv import load_dotenv

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_PASSWORD  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(ENV_FILE)

SUPABASE_URL      = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
LOCAL_PG = dict(
    host="localhost", port=5432,
    dbname="RestaurantIntelligence",
    user="postgres", password=PG_PASSWORD,
)
PAGE_SIZE   = 1000
CHECKPOINT  = Path(__file__).parent / "migration_checkpoint.json"
LOG_FILE    = Path(__file__).parent / "migration.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Table configs (FK-dependency order)
# cols = columns to SELECT from Supabase AND INSERT into local PG
#        (generated columns excluded — PG computes them automatically)
# ---------------------------------------------------------------------------
TABLES = [
    {
        "name": "scrape_runs",
        "cols": [
            "run_id", "platform", "mode",
            "restaurants_scraped", "restaurants_changed", "restaurants_no_change",
            "restaurants_failed", "items_total", "items_added", "items_removed",
            "items_price_changed", "items_desc_changed", "items_cat_changed",
            "started_at", "completed_at",
        ],
        "pk": "run_id",
        "order_col": "run_id",
        "is_seq_pk": False,
        "jsonb_cols": [],
    },
    {
        "name": "ingredients_taxonomy",
        # normalized_name is GENERATED ALWAYS — excluded
        "cols": ["id", "ingredient_name", "ingredient_category", "source", "created_at"],
        "pk": "id",
        "order_col": "id",
        "is_seq_pk": True,
        "seq_name": "ingredients_taxonomy_id_seq",
        "jsonb_cols": [],
    },
    {
        "name": "chain_brands",
        # chain_name_normalized is GENERATED ALWAYS — excluded
        "cols": [
            "chain_id", "chain_name", "chain_type", "country_of_origin",
            "total_locations_uae", "cuisine_category", "website", "created_at", "updated_at",
        ],
        "pk": "chain_id",
        "order_col": "chain_id",
        "is_seq_pk": True,
        "seq_name": "chain_brands_chain_id_seq",
        "jsonb_cols": [],
    },
    {
        "name": "talabat_restaurants",
        "cols": [
            "branch_id", "restaurant_id", "restaurant_name", "ld_name",
            "restaurant_type", "outlet_type", "chained_outlet_type", "std_terms",
            "contact", "map_url", "serves_cuisine", "key_cuisines",
            "ld_lat", "ld_lon", "area_id", "area_name",
            "first_scraped_at", "updated_at",
        ],
        "pk": "branch_id",
        "order_col": "branch_id",
        "is_seq_pk": False,
        "jsonb_cols": [],
    },
    {
        "name": "fact_restaurants",
        # normalized_name is GENERATED ALWAYS — excluded
        "cols": [
            "id", "platform", "platform_restaurant_id", "platform_branch_id",
            "restaurant_name", "lat", "lon", "area_name", "country_code",
            "cuisine_types", "outlet_type", "entity_group_id",
            "first_seen_at", "last_updated_at", "is_active",
        ],
        "pk": "id",
        "order_col": "id",
        "is_seq_pk": False,
        "jsonb_cols": [],
    },
    {
        "name": "talabat_menu_items",
        "cols": [
            "id", "branch_id", "run_id", "item_id", "item_name", "item_key",
            "menu_category", "price_aed", "description", "image_url", "scraped_at",
        ],
        "pk": "id",
        "order_col": "id",
        "is_seq_pk": True,
        "seq_name": "talabat_menu_items_id_seq",
        "jsonb_cols": [],
    },
    {
        "name": "talabat_menu_deltas",
        "cols": [
            "id", "branch_id", "run_id", "change_type", "item_name", "item_key",
            "menu_category", "field_changed", "old_value", "new_value",
            "old_price_aed", "new_price_aed", "price_change_pct",
            "detected_at", "baseline_run_id", "restaurant_id",
        ],
        "pk": "id",
        "order_col": "id",
        "is_seq_pk": True,
        "seq_name": "talabat_menu_deltas_id_seq",
        "jsonb_cols": [],
    },
    {
        "name": "chain_locations",
        "cols": [
            "id", "chain_id", "place_id", "google_maps_url", "location_name",
            "phone", "address", "city", "emirate", "geo_lat", "geo_lng",
            "category", "all_categories", "category_type", "price_range",
            "rating", "review_count", "rating_distribution", "is_permanently_closed",
            "entity_group_id", "apify_run_id", "scraped_at", "updated_at",
        ],
        "pk": "id",
        "order_col": "id",
        "is_seq_pk": True,
        "seq_name": "chain_locations_id_seq",
        "jsonb_cols": ["rating_distribution"],
    },
    {
        "name": "menu_item_ingredients",
        "cols": [
            "id", "platform", "branch_id", "item_key", "ingredient_name",
            "ingredient_category", "extraction_method", "confidence", "extracted_at",
        ],
        "pk": "id",
        "order_col": "id",
        "is_seq_pk": True,
        "seq_name": "menu_item_ingredients_id_seq",
        "jsonb_cols": [],
    },
    # talabat_summary_cache has RLS enabled — migrated last and skipped if no access
    {
        "name": "talabat_summary_cache",
        "cols": ["id", "payload", "refreshed_at"],
        "pk": "id",
        "order_col": "id",
        "is_seq_pk": False,
        "jsonb_cols": ["payload"],
        "skip_if_empty": True,
    },
]

# ---------------------------------------------------------------------------
# DDL — create all tables in local PG
# ---------------------------------------------------------------------------
DDL = """
-- ---------------------------------------------------------------
-- Core tables (no FK deps)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scrape_runs (
    run_id                  TEXT PRIMARY KEY,
    platform                TEXT NOT NULL,
    mode                    TEXT,
    restaurants_scraped     INTEGER DEFAULT 0,
    restaurants_changed     INTEGER DEFAULT 0,
    restaurants_no_change   INTEGER DEFAULT 0,
    restaurants_failed      INTEGER DEFAULT 0,
    items_total             INTEGER DEFAULT 0,
    items_added             INTEGER DEFAULT 0,
    items_removed           INTEGER DEFAULT 0,
    items_price_changed     INTEGER DEFAULT 0,
    items_desc_changed      INTEGER DEFAULT 0,
    items_cat_changed       INTEGER DEFAULT 0,
    started_at              TIMESTAMPTZ NOT NULL,
    completed_at            TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ingredients_taxonomy (
    id                  BIGSERIAL PRIMARY KEY,
    ingredient_name     TEXT NOT NULL UNIQUE,
    ingredient_category TEXT,
    normalized_name     TEXT GENERATED ALWAYS AS (lower(trim(ingredient_name))) STORED,
    source              TEXT DEFAULT 'FoodAnalytics',
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chain_brands (
    chain_id                BIGSERIAL PRIMARY KEY,
    chain_name              TEXT NOT NULL UNIQUE,
    chain_name_normalized   TEXT GENERATED ALWAYS AS (lower(trim(chain_name))) STORED,
    chain_type              TEXT,
    country_of_origin       TEXT,
    total_locations_uae     INTEGER DEFAULT 0,
    cuisine_category        TEXT,
    website                 TEXT,
    created_at              TIMESTAMPTZ DEFAULT now(),
    updated_at              TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS talabat_restaurants (
    branch_id           BIGINT PRIMARY KEY,
    restaurant_id       BIGINT,
    restaurant_name     TEXT NOT NULL,
    ld_name             TEXT,
    restaurant_type     TEXT,
    outlet_type         TEXT,
    chained_outlet_type TEXT,
    std_terms           TEXT,
    contact             TEXT,
    map_url             TEXT,
    serves_cuisine      TEXT[],
    key_cuisines        TEXT[],
    ld_lat              NUMERIC,
    ld_lon              NUMERIC,
    area_id             INTEGER,
    area_name           TEXT,
    first_scraped_at    TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fact_restaurants (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform                TEXT NOT NULL,
    platform_restaurant_id  TEXT,
    platform_branch_id      TEXT NOT NULL,
    restaurant_name         TEXT NOT NULL,
    normalized_name         TEXT GENERATED ALWAYS AS (lower(trim(restaurant_name))) STORED,
    lat                     NUMERIC,
    lon                     NUMERIC,
    area_name               TEXT,
    country_code            TEXT DEFAULT 'AE',
    cuisine_types           TEXT[],
    outlet_type             TEXT,
    entity_group_id         UUID,
    first_seen_at           TIMESTAMPTZ DEFAULT now(),
    last_updated_at         TIMESTAMPTZ DEFAULT now(),
    is_active               BOOLEAN DEFAULT true
);

-- ---------------------------------------------------------------
-- Menu tables (depend on scrape_runs + talabat_restaurants)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS talabat_menu_items (
    id              BIGSERIAL PRIMARY KEY,
    branch_id       BIGINT NOT NULL REFERENCES talabat_restaurants(branch_id),
    run_id          TEXT   NOT NULL REFERENCES scrape_runs(run_id),
    item_id         TEXT,
    item_name       TEXT NOT NULL,
    item_key        TEXT NOT NULL,
    menu_category   TEXT,
    price_aed       NUMERIC,
    description     TEXT,
    image_url       TEXT,
    scraped_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS talabat_menu_deltas (
    id              BIGSERIAL PRIMARY KEY,
    branch_id       BIGINT NOT NULL REFERENCES talabat_restaurants(branch_id),
    run_id          TEXT   NOT NULL REFERENCES scrape_runs(run_id),
    change_type     TEXT NOT NULL CHECK (change_type = ANY (ARRAY['ADDED','REMOVED','CHANGED'])),
    item_name       TEXT,
    item_key        TEXT,
    menu_category   TEXT,
    field_changed   TEXT,
    old_value       TEXT,
    new_value       TEXT,
    old_price_aed   NUMERIC,
    new_price_aed   NUMERIC,
    price_change_pct NUMERIC,
    detected_at     TIMESTAMPTZ DEFAULT now(),
    baseline_run_id TEXT,
    restaurant_id   BIGINT
);

-- ---------------------------------------------------------------
-- Chain tables (depend on chain_brands)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chain_locations (
    id                      BIGSERIAL PRIMARY KEY,
    chain_id                BIGINT NOT NULL REFERENCES chain_brands(chain_id),
    place_id                TEXT NOT NULL UNIQUE,
    google_maps_url         TEXT,
    location_name           TEXT,
    phone                   TEXT,
    address                 TEXT,
    city                    TEXT DEFAULT 'Dubai',
    emirate                 TEXT,
    geo_lat                 NUMERIC,
    geo_lng                 NUMERIC,
    category                TEXT,
    all_categories          TEXT[],
    category_type           TEXT,
    price_range             TEXT,
    rating                  NUMERIC,
    review_count            INTEGER DEFAULT 0,
    rating_distribution     JSONB,
    is_permanently_closed   BOOLEAN DEFAULT false,
    entity_group_id         UUID,
    apify_run_id            TEXT,
    scraped_at              TIMESTAMPTZ DEFAULT now(),
    updated_at              TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------
-- Ingredient linkage (depends on ingredients_taxonomy)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS menu_item_ingredients (
    id                  BIGSERIAL PRIMARY KEY,
    platform            TEXT NOT NULL,
    branch_id           TEXT NOT NULL,
    item_key            TEXT NOT NULL,
    ingredient_name     TEXT NOT NULL REFERENCES ingredients_taxonomy(ingredient_name),
    ingredient_category TEXT,
    extraction_method   TEXT CHECK (extraction_method = ANY (ARRAY['ai','keyword','manual'])),
    confidence          NUMERIC CHECK (confidence >= 0 AND confidence <= 1),
    extracted_at        TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------
-- Summary cache
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS talabat_summary_cache (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    payload     JSONB NOT NULL,
    refreshed_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------
-- Indexes (applied after DDL creation, before or after data load)
-- ---------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_menu_items_branch_id   ON talabat_menu_items(branch_id);
CREATE INDEX IF NOT EXISTS idx_menu_items_run_id      ON talabat_menu_items(run_id);
CREATE INDEX IF NOT EXISTS idx_menu_items_item_key    ON talabat_menu_items(item_key);
CREATE INDEX IF NOT EXISTS idx_menu_deltas_branch_id  ON talabat_menu_deltas(branch_id);
CREATE INDEX IF NOT EXISTS idx_menu_deltas_run_id     ON talabat_menu_deltas(run_id);
CREATE INDEX IF NOT EXISTS idx_menu_deltas_chtype     ON talabat_menu_deltas(change_type);
CREATE INDEX IF NOT EXISTS idx_restaurants_area       ON talabat_restaurants(area_name);
CREATE INDEX IF NOT EXISTS idx_fact_platform_branch   ON fact_restaurants(platform, platform_branch_id);
CREATE INDEX IF NOT EXISTS idx_fact_entity_group      ON fact_restaurants(entity_group_id);
"""


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------
def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        with open(CHECKPOINT, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_checkpoint(cp: dict) -> None:
    with open(CHECKPOINT, "w", encoding="utf-8") as f:
        json.dump(cp, f, indent=2)


# ---------------------------------------------------------------------------
# Core migration function
# ---------------------------------------------------------------------------
def migrate_table(sb: Client, pg_conn, table_cfg: dict, cp: dict) -> None:
    name      = table_cfg["name"]
    cols      = table_cfg["cols"]
    pk        = table_cfg["pk"]
    order_col = table_cfg["order_col"]
    jsonb_cols = table_cfg.get("jsonb_cols", [])

    if cp.get(name, {}).get("done"):
        log.info("[%s] Already done, skipping", name)
        return

    start_offset = cp.get(name, {}).get("offset", 0)
    log.info("[%s] Starting at offset %d", name, start_offset)

    col_names = ", ".join(cols)
    insert_sql = (
        f"INSERT INTO {name} ({col_names}) VALUES %s "
        f"ON CONFLICT ({pk}) DO NOTHING"
    )

    cur    = pg_conn.cursor()
    offset = start_offset
    total  = start_offset  # count includes rows already migrated in prior run
    t0     = time.time()

    while True:
        # --- Fetch page from Supabase ---
        try:
            resp  = (
                sb.table(name)
                .select(col_names)
                .order(order_col)
                .range(offset, offset + PAGE_SIZE - 1)
                .execute()
            )
            batch = resp.data or []
        except Exception as e:
            log.error("[%s] Supabase fetch error at offset %d: %s", name, offset, e)
            save_checkpoint(cp)
            raise

        if not batch:
            break

        # --- Build row tuples, wrapping JSONB ---
        rows = []
        for row in batch:
            tup = []
            for col in cols:
                val = row.get(col)
                if col in jsonb_cols and val is not None:
                    val = psycopg2.extras.Json(val)
                tup.append(val)
            rows.append(tuple(tup))

        # --- Bulk insert ---
        try:
            psycopg2.extras.execute_values(cur, insert_sql, rows, page_size=500)
            pg_conn.commit()
        except Exception as e:
            pg_conn.rollback()
            log.error("[%s] Insert error at offset %d: %s", name, offset, e)
            save_checkpoint(cp)
            raise

        offset += len(batch)
        total   = offset
        elapsed = time.time() - t0
        rate    = (offset - start_offset) / elapsed if elapsed > 0 else 0
        log.info("[%s] %d rows | %.0f rows/s", name, total, rate)

        # Update checkpoint after every page
        cp[name] = {"done": False, "offset": offset}
        save_checkpoint(cp)

        if len(batch) < PAGE_SIZE:
            break

    # --- Reset BIGSERIAL sequence ---
    if table_cfg.get("is_seq_pk") and total > 0:
        seq = table_cfg.get("seq_name", f"{name}_{pk}_seq")
        cur.execute(f"SELECT setval('{seq}', (SELECT COALESCE(MAX({pk}), 1) FROM {name}))")
        pg_conn.commit()
        log.info("[%s] Sequence %s reset", name, seq)

    cur.close()
    cp[name] = {"done": True, "rows": total}
    save_checkpoint(cp)
    log.info("[%s] DONE  %d rows  %.1fs", name, total, time.time() - t0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info("=" * 60)
    log.info("Supabase -> Local PostgreSQL Migration")
    log.info("=" * 60)

    # --- Supabase client ---
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        log.error("SUPABASE_URL / SUPABASE_ANON_KEY missing in .env")
        sys.exit(1)
    sb = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    log.info("Supabase client connected: %s", SUPABASE_URL)

    # --- Local PostgreSQL ---
    try:
        pg = psycopg2.connect(**LOCAL_PG)
        pg.autocommit = False
        log.info("Local PostgreSQL connected: %s:%s/%s", LOCAL_PG["host"], LOCAL_PG["port"], LOCAL_PG["dbname"])
    except Exception as e:
        log.error("Cannot connect to local PostgreSQL: %s", e)
        sys.exit(1)

    # --- Create schema ---
    log.info("Creating schema (IF NOT EXISTS)...")
    with pg.cursor() as cur:
        cur.execute(DDL)
    pg.commit()
    log.info("Schema ready")

    # --- Load checkpoint ---
    cp = load_checkpoint()

    # --- Migrate each table ---
    t_start = time.time()
    for tbl in TABLES:
        try:
            migrate_table(sb, pg, tbl, cp)
        except Exception as e:
            log.error("Migration failed on table [%s]: %s", tbl["name"], e)
            log.error("Checkpoint saved. Re-run to resume.")
            pg.close()
            sys.exit(1)

    pg.close()
    total_min = (time.time() - t_start) / 60
    log.info("=" * 60)
    log.info("Migration complete in %.1f minutes", total_min)
    log.info("Checkpoint: %s", CHECKPOINT)
    log.info("=" * 60)

    # Clean up checkpoint on success
    if CHECKPOINT.exists():
        CHECKPOINT.unlink()
    log.info("Checkpoint file removed (clean run)")


if __name__ == "__main__":
    main()
