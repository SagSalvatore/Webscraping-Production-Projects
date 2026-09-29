"""
seed_data.py — One-time bulk loader for Supabase tables.

Seeds:
  --ingredients   ingredients_taxonomy from FoodAnalytics JSON (~1k rows)
  --restaurants   talabat_restaurants + fact_restaurants from restaurants_confirmed.jsonl (~15k rows)
  --all           both of the above

Usage:
  cd talabat/supabase
  pip install supabase python-dotenv
  python seed_data.py --all
  python seed_data.py --ingredients
  python seed_data.py --restaurants
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parent.parent   # talabat/
load_dotenv(ROOT / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")

# Source files
INGREDIENTS_JSON  = Path(r"C:\Users\SagarSingh\Downloads\FoodAnalytics.Ingredient_category_taxonomy.json")
RESTAURANTS_JSONL = ROOT / "Restaurant Identifier" / "data" / "restaurants_confirmed.jsonl"

BATCH_SIZE = 500


def get_client():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY in talabat/.env")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ── Ingredients taxonomy ───────────────────────────────────────────────────────

def seed_ingredients(client):
    if not INGREDIENTS_JSON.exists():
        print(f"ERROR: {INGREDIENTS_JSON} not found")
        sys.exit(1)

    print(f"Loading {INGREDIENTS_JSON.name} ...")
    with open(INGREDIENTS_JSON, "r", encoding="utf-8") as f:
        raw = json.load(f)

    rows, seen = [], set()
    for entry in raw:
        name = (entry.get("Ingredients") or "").strip()
        cat  = (entry.get("Ingredients_Category") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append({
            "ingredient_name":     name,
            "ingredient_category": cat,
            "source":              "FoodAnalytics",
            # normalized_name is a GENERATED column — PostgreSQL computes it automatically
        })

    print(f"  {len(rows)} unique ingredients to upsert ...")
    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        client.table("ingredients_taxonomy").upsert(
            batch, on_conflict="ingredient_name"
        ).execute()
        total += len(batch)
        print(f"  [{total}/{len(rows)}] done")

    print(f"Ingredients seeded: {total} rows.\n")


# ── Talabat restaurants ────────────────────────────────────────────────────────

def seed_restaurants(client):
    if not RESTAURANTS_JSONL.exists():
        print(f"ERROR: {RESTAURANTS_JSONL} not found")
        sys.exit(1)

    print(f"Loading {RESTAURANTS_JSONL.name} ...")
    lines = RESTAURANTS_JSONL.read_text(encoding="utf-8").strip().splitlines()
    print(f"  {len(lines)} records found")

    now_ts        = datetime.now(timezone.utc).isoformat()
    talabat_rows  = []
    fact_rows     = []

    for line in lines:
        r = json.loads(line)

        branch_id     = int(r["branch_id"])
        restaurant_id = int(r["restaurant_id"])
        name          = r.get("name", "")
        ld_name       = r.get("ld_name", "")
        url           = r.get("url", "")
        cuisines      = r.get("matched_cuisines") or []
        area_name     = r.get("area_name", "")
        area_id       = int(r["area_id"]) if r.get("area_id") else None
        ld_type       = r.get("ld_type", "Restaurant")
        filtered_at   = r.get("filtered_at") or now_ts

        ld_lat = float(r["ld_lat"]) if r.get("ld_lat") else None
        ld_lon = float(r["ld_lon"]) if r.get("ld_lon") else None

        talabat_rows.append({
            "branch_id":        branch_id,
            "restaurant_id":    restaurant_id,
            "restaurant_name":  name,
            "ld_name":          ld_name,
            "restaurant_type":  ld_type,
            "map_url":          url,
            "serves_cuisine":   cuisines,
            "ld_lat":           ld_lat,
            "ld_lon":           ld_lon,
            "area_id":          area_id,
            "area_name":        area_name,
            "first_scraped_at": filtered_at,
            "updated_at":       now_ts,
        })

        fact_rows.append({
            "platform":               "talabat",
            "platform_restaurant_id": str(restaurant_id),
            "platform_branch_id":     str(branch_id),
            "restaurant_name":        name,
            "lat":                    ld_lat,
            "lon":                    ld_lon,
            "area_name":              area_name,
            "cuisine_types":          cuisines,
            "first_seen_at":          filtered_at,
            "last_updated_at":        now_ts,
        })

    # Bulk upsert talabat_restaurants
    print(f"Upserting talabat_restaurants ({len(talabat_rows)} rows) ...")
    total = 0
    for i in range(0, len(talabat_rows), BATCH_SIZE):
        batch = talabat_rows[i:i + BATCH_SIZE]
        client.table("talabat_restaurants").upsert(
            batch, on_conflict="branch_id"
        ).execute()
        total += len(batch)
        print(f"  talabat_restaurants [{total}/{len(talabat_rows)}]")

    # Bulk upsert fact_restaurants
    print(f"Upserting fact_restaurants ({len(fact_rows)} rows) ...")
    total = 0
    for i in range(0, len(fact_rows), BATCH_SIZE):
        batch = fact_rows[i:i + BATCH_SIZE]
        client.table("fact_restaurants").upsert(
            batch, on_conflict="platform,platform_branch_id"
        ).execute()
        total += len(batch)
        print(f"  fact_restaurants [{total}/{len(fact_rows)}]")

    print(f"Restaurants seeded: {len(talabat_rows)} rows in each table.\n")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Supabase UAE food pipeline tables")
    parser.add_argument("--ingredients",  action="store_true", help="Seed ingredients_taxonomy")
    parser.add_argument("--restaurants",  action="store_true", help="Seed talabat_restaurants + fact_restaurants")
    parser.add_argument("--all",          action="store_true", help="Seed all tables")
    args = parser.parse_args()

    if not any([args.ingredients, args.restaurants, args.all]):
        parser.print_help()
        sys.exit(0)

    client = get_client()
    print(f"Connected to {SUPABASE_URL[:50]}...\n")

    if args.all or args.ingredients:
        seed_ingredients(client)

    if args.all or args.restaurants:
        seed_restaurants(client)

    print("All seeding complete.")
