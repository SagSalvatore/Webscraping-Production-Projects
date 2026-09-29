"""
seed_from_excel.py — Backfill talabat_restaurants with enrichment data from Data_menus.xlsx.

Columns seeded:
  Type of Restaurants  → restaurant_type  (FSR / QSR / Cafe / Cloud Kitchen)
                       → outlet_type      (derived short form)
  KEY CUISINES         → key_cuisines     (array of cuisine tags)

Strategy:
  - Batch upsert on branch_id (ON CONFLICT branch_id DO UPDATE) — 11 API calls for 5,275 rows
  - Only touches the 3 seeding columns; restaurant_name / map_url / scraped data untouched
  - restaurant_type from Excel overwrites the generic "Restaurant" placeholder from scraper

Usage:
  python seed_from_excel.py              # seed all rows
  python seed_from_excel.py --dry-run    # preview counts only
"""

import argparse
import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
EXCEL_SRC = ROOT / "Data_menus.xlsx"

load_dotenv(ROOT / ".env")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")

BATCH_SIZE = 500

# Maps Excel "Type of Restaurants" → short outlet_type code
OUTLET_TYPE_MAP = {
    "Full-Service Restaurant (FSR)":  "FSR",
    "Quick-Service Restaurant (QSR)": "QSR",
    "Cafes":                          "Cafe",
    "Cloud Kitchen":                  "Cloud Kitchen",
}


def arr_from_str(val) -> list:
    """'Asian, Chinese, Thai' -> ['Asian', 'Chinese', 'Thai']"""
    if pd.isna(val) or not str(val).strip():
        return []
    return [x.strip() for x in str(val).split(",") if x.strip()]


def load_excel_seed() -> list[dict]:
    """Read Excel and return one enrichment dict per unique branch_id."""
    print(f"Reading {EXCEL_SRC.name} ...")
    df = pd.read_excel(EXCEL_SRC, dtype={"branch_id": int, "restaurant_id": int})

    # One row per branch_id (menu-level file — many rows per restaurant)
    rest = df.groupby("branch_id").first().reset_index()
    print(f"  {len(rest):,} unique branch_ids found")

    rows = []
    for _, r in rest.iterrows():
        raw_type     = r.get("Type of Restaurants")
        rest_type    = str(raw_type).strip() if pd.notna(raw_type) else None
        outlet_type  = OUTLET_TYPE_MAP.get(rest_type) if rest_type else None
        key_cuisines = arr_from_str(r.get("KEY CUISINES"))

        row = {"branch_id": int(r["branch_id"])}

        # Only include restaurant_type when we have a meaningful classification
        # (avoids overwriting scraped data for non-classified rows)
        if rest_type and rest_type not in ("nan", ""):
            row["restaurant_type"] = rest_type
        if outlet_type:
            row["outlet_type"] = outlet_type
        if key_cuisines:
            row["key_cuisines"] = key_cuisines

        rows.append(row)

    return rows


def main():
    parser = argparse.ArgumentParser(description="Seed talabat_restaurants from Data_menus.xlsx")
    parser.add_argument("--dry-run", action="store_true", help="Preview only — no DB writes")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL + SUPABASE_ANON_KEY must be set in talabat/.env")
        sys.exit(1)

    try:
        from supabase import create_client
    except ImportError:
        print("ERROR: pip install supabase")
        sys.exit(1)

    rows = load_excel_seed()

    # Stats preview
    has_type    = sum(1 for r in rows if r.get("restaurant_type"))
    has_outlet  = sum(1 for r in rows if r.get("outlet_type"))
    has_cuisine = sum(1 for r in rows if r.get("key_cuisines"))
    print(f"\n  restaurant_type  to seed : {has_type:,} / {len(rows):,}")
    print(f"  outlet_type      to seed : {has_outlet:,} / {len(rows):,}")
    print(f"  key_cuisines     to seed : {has_cuisine:,} / {len(rows):,}")

    if args.dry_run:
        print("\n[DRY RUN] No writes. Remove --dry-run to seed.")
        print("\nSample rows:")
        for r in rows[:5]:
            print(f"  {r}")
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    thread_local = threading.local()

    def get_client():
        if not hasattr(thread_local, "client"):
            thread_local.client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return thread_local.client

    def update_row(row):
        bid = row["branch_id"]
        payload = {k: v for k, v in row.items() if k != "branch_id"}
        if not payload:
            return True, bid
        try:
            get_client().table("talabat_restaurants").update(payload).eq("branch_id", bid).execute()
            return True, bid
        except Exception as exc:
            return False, f"branch_id={bid}: {exc}"

    print(f"\nUpdating {len(rows):,} rows concurrently (20 threads) ...")
    t0 = time.perf_counter()
    success_count = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(update_row, row) for row in rows]
        done = 0
        for f in as_completed(futures):
            ok, info = f.result()
            done += 1
            if ok:
                success_count += 1
            else:
                errors += 1
                print(f"\n  WARN {info}")
            if done % 200 == 0 or done == len(rows):
                pct = done / len(rows) * 100
                elapsed = time.perf_counter() - t0
                print(f"  [{done:,}/{len(rows):,}] {pct:.0f}%  ({elapsed:.0f}s)", end="\r")

    elapsed = time.perf_counter() - t0
    print(f"\n\nDone. {success_count:,} rows updated in {elapsed:.0f}s  (errors: {errors})")
    if errors == 0:
        print("Next step: python export_to_csv.py --format both")


if __name__ == "__main__":
    main()
