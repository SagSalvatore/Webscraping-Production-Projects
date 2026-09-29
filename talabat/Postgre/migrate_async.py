#!/usr/bin/env python3
"""
Supabase -> Local PostgreSQL async migration.

Strategy for large tables (>100K rows):
  - ID-range fetching: WHERE pk >= X AND pk <= Y (uses index, no deep scans)
  - 8 concurrent range-fetches at once → no Supabase 500 errors
  - Resume: reads max(pk) from local DB, continues from there

Strategy for small tables (<100K rows):
  - TRUNCATE + offset-based reload (always fresh, very fast)

Run:   python talabat/Postgre/migrate_async.py
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import asyncpg
import httpx
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
load_dotenv(Path(__file__).parent.parent / ".env")

SUPABASE_URL      = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

LOCAL_PG = dict(
    host="localhost", port=5432,
    database="RestaurantIntelligence",
    user="postgres", password=PG_PASSWORD,
)

FETCH_CONCURRENCY = 8     # concurrent Supabase range-fetches
PAGE_SIZE         = 1000  # rows per fetch (also ID chunk size in range mode)
COPY_BATCH        = 5_000 # rows per asyncpg COPY flush
QUEUE_MAX         = 40    # max buffered pages in memory

LOG_FILE = Path(__file__).parent / "migration_async.log"

SB_HEADERS = {
    "apikey": SUPABASE_ANON_KEY,
    "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a"),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type converters
# ---------------------------------------------------------------------------
_TS_COLS = frozenset({
    "started_at", "completed_at", "created_at", "updated_at",
    "first_scraped_at", "scraped_at", "detected_at", "extracted_at",
    "first_seen_at", "last_updated_at", "refreshed_at",
})
_DEC_COLS = frozenset({
    "ld_lat", "ld_lon", "lat", "lon",
    "price_aed", "old_price_aed", "new_price_aed", "price_change_pct",
    "geo_lat", "geo_lng", "rating", "confidence",
})


def _coerce(col: str, v):
    if v is None:
        return None
    if col in _TS_COLS:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    if col in _DEC_COLS:
        return Decimal(str(v))
    return v


def row_to_tuple(row: dict, cols: list[str]) -> tuple:
    return tuple(_coerce(col, row.get(col)) for col in cols)


# ---------------------------------------------------------------------------
# Table configs
# large_table=True  → ID-range concurrent fetch + resume from local max(pk)
# large_table=False → TRUNCATE + offset-based reload (small, fast)
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
        "pk": "run_id", "is_seq": False, "large_table": False,
    },
    {
        "name": "ingredients_taxonomy",
        "cols": ["id", "ingredient_name", "ingredient_category", "source", "created_at"],
        "pk": "id", "is_seq": True, "seq_name": "ingredients_taxonomy_id_seq",
        "large_table": False,
    },
    {
        "name": "chain_brands",
        "cols": [
            "chain_id", "chain_name", "chain_type", "country_of_origin",
            "total_locations_uae", "cuisine_category", "website", "created_at", "updated_at",
        ],
        "pk": "chain_id", "is_seq": True, "seq_name": "chain_brands_chain_id_seq",
        "large_table": False,
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
        "pk": "branch_id", "is_seq": False, "large_table": False,
    },
    {
        "name": "fact_restaurants",
        "cols": [
            "id", "platform", "platform_restaurant_id", "platform_branch_id",
            "restaurant_name", "lat", "lon", "area_name", "country_code",
            "cuisine_types", "outlet_type", "entity_group_id",
            "first_seen_at", "last_updated_at", "is_active",
        ],
        "pk": "id", "is_seq": False, "large_table": False,
    },
    # Large tables — use ID-range fetching + resume
    {
        "name": "talabat_menu_items",
        "cols": [
            "id", "branch_id", "run_id", "item_id", "item_name", "item_key",
            "menu_category", "price_aed", "description", "image_url", "scraped_at",
        ],
        "pk": "id", "is_seq": True, "seq_name": "talabat_menu_items_id_seq",
        "large_table": True,
    },
    {
        "name": "talabat_menu_deltas",
        "cols": [
            "id", "branch_id", "run_id", "change_type", "item_name", "item_key",
            "menu_category", "field_changed", "old_value", "new_value",
            "old_price_aed", "new_price_aed", "price_change_pct",
            "detected_at", "baseline_run_id", "restaurant_id",
        ],
        "pk": "id", "is_seq": True, "seq_name": "talabat_menu_deltas_id_seq",
        "large_table": True,
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
        "pk": "id", "is_seq": True, "seq_name": "chain_locations_id_seq",
        "jsonb_cols": {"rating_distribution"}, "large_table": False,
    },
    {
        "name": "menu_item_ingredients",
        "cols": [
            "id", "platform", "branch_id", "item_key", "ingredient_name",
            "ingredient_category", "extraction_method", "confidence", "extracted_at",
        ],
        "pk": "id", "is_seq": True, "seq_name": "menu_item_ingredients_id_seq",
        "large_table": False,
    },
    {
        "name": "talabat_summary_cache",
        "cols": ["id", "payload", "refreshed_at"],
        "pk": "id", "is_seq": False,
        "jsonb_cols": {"payload"}, "rls_protected": True, "large_table": False,
    },
]

# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------
async def sb_get_max_id(client: httpx.AsyncClient, table: str, pk: str) -> int:
    """Fetch the maximum PK value from Supabase (one request)."""
    resp = await client.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        params=[("select", pk), ("order", f"{pk}.desc"), ("limit", 1)],
        headers=SB_HEADERS, timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data[0][pk] if data else 0


async def sb_fetch_range(
    client: httpx.AsyncClient,
    table: str, cols: list[str], pk: str,
    min_id: int, max_id: int,
    sem: asyncio.Semaphore,
) -> list[dict]:
    """Fetch rows where pk >= min_id AND pk <= max_id. Uses index, no deep scan."""
    params = [
        ("select", ",".join(cols)),
        ("order", pk),
        (pk, f"gte.{min_id}"),
        (pk, f"lte.{max_id}"),
        ("limit", PAGE_SIZE),
    ]
    async with sem:
        for attempt in range(4):
            try:
                resp = await client.get(
                    f"{SUPABASE_URL}/rest/v1/{table}",
                    params=params, headers=SB_HEADERS, timeout=90,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt == 3:
                    raise
                wait = 2 ** attempt
                log.warning("[%s] Range fetch error (attempt %d/%d): %s — retry in %ds",
                            table, attempt + 1, 4, e, wait)
                await asyncio.sleep(wait)
    return []


async def sb_fetch_page_offset(
    client: httpx.AsyncClient,
    table: str, cols: list[str], pk: str,
    offset: int, sem: asyncio.Semaphore,
) -> list[dict]:
    """Offset-based fetch for small tables."""
    params = [("select", ",".join(cols)), ("order", pk), ("offset", offset), ("limit", PAGE_SIZE)]
    async with sem:
        for attempt in range(3):
            try:
                resp = await client.get(
                    f"{SUPABASE_URL}/rest/v1/{table}",
                    params=params, headers=SB_HEADERS, timeout=60,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
    return []


# ---------------------------------------------------------------------------
# Producers
# ---------------------------------------------------------------------------
async def producer_idrange(
    client: httpx.AsyncClient,
    table_cfg: dict,
    queue: asyncio.Queue,
    start_id: int,        # max local pk (0 if table empty)
    max_supabase_id: int,
) -> None:
    """ID-range concurrent producer — safe for large tables."""
    name = table_cfg["name"]
    cols = table_cfg["cols"]
    pk   = table_cfg["pk"]
    sem  = asyncio.Semaphore(FETCH_CONCURRENCY)

    # Build all ID ranges: [start_id+1, start_id+1000], [start_id+1001, start_id+2000], ...
    ranges = []
    lo = start_id + 1
    while lo <= max_supabase_id:
        hi = min(lo + PAGE_SIZE - 1, max_supabase_id)
        ranges.append((lo, hi))
        lo = hi + 1

    log.info("[%s] ID-range fetch: %d chunks | id %d -> %d", name, len(ranges), start_id + 1, max_supabase_id)
    total_fetched = 0

    # Process in batches of FETCH_CONCURRENCY
    for batch_start in range(0, len(ranges), FETCH_CONCURRENCY):
        batch = ranges[batch_start : batch_start + FETCH_CONCURRENCY]
        tasks = [
            asyncio.create_task(sb_fetch_range(client, name, cols, pk, lo, hi, sem))
            for lo, hi in batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                raise result
            if result:
                await queue.put(result)
                total_fetched += len(result)

    log.info("[%s] Producer done — %d rows fetched", name, total_fetched)
    await queue.put(None)


async def producer_offset(
    client: httpx.AsyncClient,
    table_cfg: dict,
    queue: asyncio.Queue,
) -> None:
    """Offset-based concurrent producer for small tables."""
    name = table_cfg["name"]
    cols = table_cfg["cols"]
    pk   = table_cfg["pk"]
    sem  = asyncio.Semaphore(FETCH_CONCURRENCY)
    offset = 0
    total_fetched = 0

    while True:
        page_offsets = [offset + i * PAGE_SIZE for i in range(FETCH_CONCURRENCY)]
        tasks = [
            asyncio.create_task(sb_fetch_page_offset(client, name, cols, pk, po, sem))
            for po in page_offsets
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        any_data = False
        last_was_partial = False
        for result in results:
            if isinstance(result, Exception):
                raise result
            if result:
                any_data = True
                await queue.put(result)
                total_fetched += len(result)
                if len(result) < PAGE_SIZE:
                    last_was_partial = True

        offset += FETCH_CONCURRENCY * PAGE_SIZE
        if not any_data or last_was_partial:
            break

    log.info("[%s] Producer done — %d rows fetched", name, total_fetched)
    await queue.put(None)


# ---------------------------------------------------------------------------
# Consumer (asyncpg COPY)
# ---------------------------------------------------------------------------
async def consumer(pool: asyncpg.Pool, table_cfg: dict, queue: asyncio.Queue) -> int:
    name  = table_cfg["name"]
    cols  = table_cfg["cols"]
    total = 0
    buf: list[tuple] = []
    t0 = time.time()

    async def flush(rows: list[tuple]) -> None:
        nonlocal total
        async with pool.acquire() as conn:
            await conn.copy_records_to_table(
                name, records=rows, columns=cols, schema_name="public",
            )
        total += len(rows)
        rate = total / (time.time() - t0) if time.time() - t0 > 0 else 0
        log.info("[%s] %d rows | %.0f rows/s", name, total, rate)

    while True:
        page = await queue.get()
        if page is None:
            break
        for row in page:
            buf.append(row_to_tuple(row, cols))
        if len(buf) >= COPY_BATCH:
            await flush(buf)
            buf = []

    if buf:
        await flush(buf)

    return total


# ---------------------------------------------------------------------------
# Per-table migration
# ---------------------------------------------------------------------------
async def migrate_table(
    client: httpx.AsyncClient,
    pool: asyncpg.Pool,
    table_cfg: dict,
) -> int:
    name         = table_cfg["name"]
    pk           = table_cfg["pk"]
    is_large     = table_cfg.get("large_table", False)
    rls_protected = table_cfg.get("rls_protected", False)

    if rls_protected:
        log.info("[%s] Skipping — RLS protected", name)
        return 0

    # --- Check local state ---
    async with pool.acquire() as conn:
        local_count   = await conn.fetchval(f"SELECT COUNT(*) FROM {name}")
        local_max_id  = await conn.fetchval(f"SELECT COALESCE(MAX({pk}::bigint), 0) FROM {name}") \
            if is_large else 0

    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    t0 = time.time()

    if is_large:
        # --- Large table: ID-range fetch, resume from local max id ---
        log.info("[%s] Resume from local max %s=%s (already %d rows)", name, pk, local_max_id, local_count)
        max_sb_id = await sb_get_max_id(client, name, pk)
        if local_max_id >= max_sb_id:
            log.info("[%s] Already fully migrated (%d rows). Skipping.", name, local_count)
            return int(local_count)

        prod_task = asyncio.create_task(
            producer_idrange(client, table_cfg, queue, int(local_max_id), int(max_sb_id))
        )
    else:
        # --- Small table: truncate + full reload ---
        async with pool.acquire() as conn:
            await conn.execute(f"TRUNCATE TABLE {name} RESTART IDENTITY CASCADE")
        log.info("[%s] Truncated, reloading from scratch", name)

        prod_task = asyncio.create_task(producer_offset(client, table_cfg, queue))

    new_rows = await consumer(pool, table_cfg, queue)
    await prod_task

    total = int(local_count) + new_rows if is_large else new_rows

    # Reset BIGSERIAL sequence
    if table_cfg.get("is_seq") and total > 0:
        seq = table_cfg.get("seq_name", f"{name}_{pk}_seq")
        async with pool.acquire() as conn:
            await conn.execute(
                f"SELECT setval('{seq}', (SELECT COALESCE(MAX({pk}), 1) FROM {name}))"
            )
        log.info("[%s] Sequence %s reset", name, seq)

    log.info("[%s] DONE  %d total rows  %.1fs", name, total, time.time() - t0)
    return total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> None:
    log.info("=" * 60)
    log.info("Async Supabase -> Local PostgreSQL Migration (ID-range mode)")
    log.info("=" * 60)

    async def init_conn(conn):
        await conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog",
        )

    pool = await asyncpg.create_pool(
        **LOCAL_PG, min_size=2, max_size=6, init=init_conn,
    )
    log.info("PostgreSQL pool ready")

    t_start = time.time()
    grand_total = 0

    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=FETCH_CONCURRENCY + 4, max_keepalive_connections=FETCH_CONCURRENCY),
        timeout=httpx.Timeout(90.0, connect=10.0),
    ) as client:
        for tbl in TABLES:
            try:
                n = await migrate_table(client, pool, tbl)
                grand_total += n
            except Exception as e:
                log.error("[%s] Migration failed: %s", tbl["name"], e, exc_info=True)
                await pool.close()
                sys.exit(1)

    await pool.close()
    elapsed = time.time() - t_start
    log.info("=" * 60)
    log.info("Done! %d rows in %.1fs (%.0f rows/s overall)", grand_total, elapsed, grand_total / elapsed if elapsed > 0 else 0)
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
