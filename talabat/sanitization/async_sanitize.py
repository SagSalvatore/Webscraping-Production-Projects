"""
async_sanitize.py — Fast sanitization via asyncpg (direct PostgreSQL connection).

Why asyncpg vs apply_to_supabase.py (REST API):
  REST approach:  250 HTTP GET pages + 3,000 individual PATCH calls = ~15 min
  asyncpg:        1 SELECT (all rows in one shot) + 1 executemany() = ~5–10 sec

How:
  - asyncpg talks directly to PostgreSQL over TCP (no HTTP/PostgREST overhead)
  - executemany() sends ALL dirty updates in a single protocol message
  - asyncio + connection pool lets reads and writes overlap

Setup:
  1. pip install asyncpg
  2. Add to talabat/.env:
       SUPABASE_DB_URL=postgresql://postgres.[password]@db.cbagujraolgpcjmnpplg.supabase.co:5432/postgres
     Get from: Supabase Dashboard → Settings → Database → Connection string (URI)

Usage:
  cd talabat/sanitization
  python async_sanitize.py               # sanitize both tables
  python async_sanitize.py --dry-run     # preview counts, no writes
  python async_sanitize.py --table items    # menu items only
  python async_sanitize.py --table deltas   # deltas only
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sanitization"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from text_cleaner import clean_category, clean_item_name

DB_URL = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL", "")

# How many rows to fetch per chunk — tune down if RAM is tight
FETCH_CHUNK = 50_000


async def sanitize_table(
    pool,
    table: str,
    cat_col: str,
    name_col: str,
    dry_run: bool,
):
    t0 = time.perf_counter()
    print(f"\n{'─'*58}")
    print(f"Table: {table}")

    # ── 1. Fetch all rows (chunked SELECT with keyset pagination for safety) ──
    all_rows = []
    last_id  = 0
    while True:
        async with pool.acquire() as conn:
            chunk = await conn.fetch(
                f"SELECT id, {cat_col}, {name_col} FROM {table} "
                f"WHERE id > $1 ORDER BY id LIMIT $2",
                last_id, FETCH_CHUNK,
            )
        if not chunk:
            break
        all_rows.extend(chunk)
        last_id = chunk[-1]["id"]
        print(f"  Fetched {len(all_rows):,} rows ...", end="\r")

    print(f"  Fetched {len(all_rows):,} total rows in {time.perf_counter()-t0:.1f}s")

    # ── 2. Identify dirty rows (pure Python, no I/O) ──────────────────────────
    dirty: list[tuple] = []
    for r in all_rows:
        orig_cat  = r[cat_col]  or ""
        orig_name = r[name_col] or ""
        new_cat   = clean_category(orig_cat)
        new_name  = clean_item_name(orig_name)
        if new_cat != orig_cat or new_name != orig_name:
            dirty.append((new_cat, new_name, r["id"]))

    pct = len(dirty) / len(all_rows) * 100 if all_rows else 0
    print(f"  Dirty rows: {len(dirty):,} / {len(all_rows):,} ({pct:.1f}% need cleaning)")

    if not dirty:
        print("  Nothing to update — all rows already clean.")
        return
    if dry_run:
        print("  [DRY RUN] Would update the above rows. No writes performed.")
        return

    # ── 3. Batch UPDATE via executemany (single round-trip for ALL dirty rows) ─
    t1 = time.perf_counter()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                f"UPDATE {table} SET {cat_col}=$1, {name_col}=$2 WHERE id=$3",
                dirty,
            )
    elapsed = time.perf_counter() - t1
    print(f"  Updated {len(dirty):,} rows via executemany in {elapsed:.2f}s")
    print(f"  Total for {table}: {time.perf_counter()-t0:.1f}s")


async def run(args):
    try:
        import asyncpg
    except ImportError:
        print("ERROR: asyncpg not installed. Run: pip install asyncpg")
        sys.exit(1)

    if not DB_URL:
        print("ERROR: SUPABASE_DB_URL not set in talabat/.env")
        print()
        print("Steps to get it:")
        print("  1. Supabase Dashboard → your project → Settings → Database")
        print("  2. Copy 'Connection string (URI)' (Session mode, port 5432)")
        print("  3. Add to talabat/.env:")
        print("     SUPABASE_DB_URL=postgresql://postgres:[PASSWORD]@db.cbagujraolgpcjmnpplg.supabase.co:5432/postgres")
        sys.exit(1)

    print(f"Connecting (asyncpg pool) ...")
    pool = await asyncpg.create_pool(DB_URL, min_size=2, max_size=8, command_timeout=120)
    print(f"Connected.")

    if args.dry_run:
        print("[DRY RUN — no writes will happen]")

    tables = []
    if args.table in ("items", "all"):
        tables.append(("talabat_menu_items",  "menu_category", "item_name"))
    if args.table in ("deltas", "all"):
        tables.append(("talabat_menu_deltas", "menu_category", "item_name"))

    # Run tables sequentially (each table already bulk-fetches + bulk-updates)
    for table, cat_col, name_col in tables:
        await sanitize_table(pool, table, cat_col, name_col, dry_run=args.dry_run)

    await pool.close()
    print(f"\n{'═'*58}")
    print("Sanitization complete.")


def main():
    parser = argparse.ArgumentParser(
        description="Fast async text sanitization via asyncpg (direct PostgreSQL)"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan and report without writing")
    parser.add_argument("--table", choices=["items", "deltas", "all"], default="all",
                        help="Which table(s) to sanitize (default: all)")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
