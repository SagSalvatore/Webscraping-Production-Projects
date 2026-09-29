"""
Fetch Restaurant URLs — image/logo scraping input builder
============================================================
Step 1 of the menu-image + logo scraping goal: build the input list the
scraper will actually read from.

Source of identity fields (source_id, chain_id, restaurant_name):
  talabat/export/talabat_export.json (the production IT-schema export)

Source of the scrape target (map_url — Talabat's OWN restaurant page, NOT
google_maps_url):
  Postgres talabat_restaurants.map_url, joined on branch_id = source_id

Dedup: talabat_export.json has 17,187 entries but only 15,198 UNIQUE
source_ids — the 1,989 "verified_location" entries (added for the 28 MNC
brands) reuse one representative branch's source_id per brand, since they
don't have their own Talabat page. Scraping those source_ids again for every
location would just re-fetch the SAME page redundantly (up to 247x for KFC),
so this join is done on unique source_id only — one row per real Talabat
page. (Verified: 0 source_ids map to more than one (chain_id, name) pair.)

Output (in this folder's data/ dir, ready for the scraper to consume):
  data/restaurant_urls_for_scraping.jsonl
  data/restaurant_urls_for_scraping.csv

Run:
  python "talabat/Restaurant Identifier/fetch_restaurant_urls.py"
"""

import csv
import json
from pathlib import Path

import orjson
import psycopg2

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_PASSWORD  # noqa: E402

EXPORT_PATH = Path(__file__).resolve().parent.parent / "export" / "talabat_export.json"
OUT_DIR = Path(__file__).resolve().parent / "data"
OUT_JSONL = OUT_DIR / "restaurant_urls_for_scraping.jsonl"
OUT_CSV = OUT_DIR / "restaurant_urls_for_scraping.csv"

DB_PARAMS = dict(host="localhost", port=5432, database="RestaurantIntelligence", user="postgres", password=PG_PASSWORD)


def main():
    OUT_DIR.mkdir(exist_ok=True)

    with open(EXPORT_PATH, "rb") as f:
        export_data = orjson.loads(f.read())
    print(f"Loaded {len(export_data):,} entries from {EXPORT_PATH.name}")

    # dedupe to one row per unique source_id (real Talabat page) — see
    # module docstring for why verified-location entries are skipped here
    identity_by_sid = {}
    for r in export_data:
        sid = r["source_id"]
        if sid not in identity_by_sid:
            identity_by_sid[sid] = {"source_id": sid, "chain_id": r["chain_id"], "restaurant_name": r["name"]}
    print(f"Unique source_ids (real Talabat pages) after dedup: {len(identity_by_sid):,}")

    branch_ids = [int(sid) for sid in identity_by_sid]
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute("SELECT branch_id, map_url FROM talabat_restaurants WHERE branch_id = ANY(%s)", (branch_ids,))
    map_url_by_bid = {str(bid): url for bid, url in cur.fetchall()}
    conn.close()

    rows = []
    missing_url = 0
    for sid, identity in identity_by_sid.items():
        map_url = map_url_by_bid.get(sid)
        if not map_url:
            missing_url += 1
        rows.append({**identity, "map_url": map_url})

    print(f"Joined against talabat_restaurants.map_url — {len(rows) - missing_url:,} with a URL, {missing_url:,} missing")

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Saved -> {OUT_JSONL} ({len(rows):,} rows)")

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["source_id", "chain_id", "restaurant_name", "map_url"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved -> {OUT_CSV} ({len(rows):,} rows)")


if __name__ == "__main__":
    main()
