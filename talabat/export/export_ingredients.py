"""
Export the distinct ingredient_name -> ingredient_category list from
menu_item_ingredients as a standalone reference JSON + CSV.

Only 816 distinct ingredient names exist across all 2,176,348 rows (each with
exactly one consistent category, no conflicts, zero nulls) — so this is a
small lookup table, not a per-row dump of the full table.

Run:
  python talabat/export/export_ingredients.py
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
JSON_OUT_PATH = Path(__file__).parent / "ingredients_export.json"
CSV_OUT_PATH = Path(__file__).parent / "ingredients_export.csv"


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT ingredient_name, ingredient_category
        FROM menu_item_ingredients
        ORDER BY ingredient_category, ingredient_name
        """
    )
    rows = cur.fetchall()
    conn.close()

    rows = [(name.lower(), category.lower()) for name, category in rows]
    records = [{"ingredient_name": name, "ingredient_category": category} for name, category in rows]

    with open(JSON_OUT_PATH, "wb") as f:
        f.write(orjson.dumps(records, option=orjson.OPT_INDENT_2))
    print(f"Exported {len(records)} distinct ingredients -> {JSON_OUT_PATH} ({JSON_OUT_PATH.stat().st_size/1e3:.1f} KB)")

    with open(CSV_OUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["ingredient_name", "ingredient_category"])
        writer.writerows(rows)
    print(f"Exported {len(rows)} distinct ingredients -> {CSV_OUT_PATH} ({CSV_OUT_PATH.stat().st_size/1e3:.1f} KB)")


if __name__ == "__main__":
    main()
