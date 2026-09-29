"""
Export a location-only JSON for the current 15,198 listings in talabat_export.json:
source_id, district_name, area_name, city, restaurant_name.

  district_name = location.area from the export (derived by parsing the
                   resolved address text, e.g. "Al Muraqqabat - Deira")
  area_name     = talabat_restaurants.area_name (Talabat's own delivery-zone
                   label, e.g. "Al Barsha 3" — only 19 distinct values total,
                   a different/coarser field than district_name above)
  city          = location.city from the export

Run:
  python talabat/export/export_locations.py
"""

import csv
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

DB_PARAMS = dict(host="localhost", port=5432, database="RestaurantIntelligence", user="postgres", password=PG_PASSWORD)
EXPORT_PATH = Path(__file__).parent / "talabat_export.json"
JSON_OUT_PATH = Path(__file__).parent / "locations.json"
CSV_OUT_PATH = Path(__file__).parent / "locations.csv"


def main():
    with open(EXPORT_PATH, "rb") as f:
        export_data = orjson.loads(f.read())

    branch_ids = [int(r["source_id"]) for r in export_data]

    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute("SELECT branch_id, area_name FROM talabat_restaurants WHERE branch_id = ANY(%s)", (branch_ids,))
    area_name_map = {str(bid): area for bid, area in cur.fetchall()}
    conn.close()

    records = [
        {
            "source_id": r["source_id"],
            "district_name": r["location"]["area"],
            "area_name": area_name_map.get(r["source_id"]),
            "city": r["location"]["city"],
            "restaurant_name": r["name"],
        }
        for r in export_data
    ]

    with open(JSON_OUT_PATH, "wb") as f:
        f.write(orjson.dumps(records, option=orjson.OPT_INDENT_2))
    print(f"Exported {len(records):,} listings -> {JSON_OUT_PATH} ({JSON_OUT_PATH.stat().st_size/1e6:.2f} MB)")

    with open(CSV_OUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["source_id", "district_name", "area_name", "city", "restaurant_name"])
        writer.writeheader()
        writer.writerows(records)
    print(f"Exported {len(records):,} listings -> {CSV_OUT_PATH} ({CSV_OUT_PATH.stat().st_size/1e6:.2f} MB)")


if __name__ == "__main__":
    main()
