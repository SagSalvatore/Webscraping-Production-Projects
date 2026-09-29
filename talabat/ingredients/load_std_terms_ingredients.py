"""
Load standard terms + ingredient mappings into Postgres via async bulk COPY.

Sources:
  talabat/ingredients/Restaurant_Menu_With_Ingredients_Final_v1.xlsx
      columns: branch_id, restaurant_id, Item Name, Category, Description,
               Ingredients, Std_Term (Reference)
  talabat/ingredients/ING_TAXONOMY.csv
      columns: _id, Ingredients, Ingredients_Category

Writes:
  talabat_menu_items.std_term       <- Std_Term (Reference), joined on
                                        (branch_id, lower(trim(item_name)))
  ingredients_taxonomy               <- new ingredient names found only in the CSV
  menu_item_ingredients               <- one row per (branch_id, item_key, ingredient_name)
                                        extracted from the free-text Ingredients column

Run:
  python talabat/ingredients/load_std_terms_ingredients.py
"""

import asyncio
import re
import time
from pathlib import Path

import asyncpg
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
EXCEL_PATH = HERE / "Restaurant_Menu_With_Ingredients_Final_v1.xlsx"
TAXONOMY_CSV = HERE / "ING_TAXONOMY.csv"

DB_DSN = PG_DSN


def normalize(s: str) -> str:
    return str(s).strip().lower()


def build_taxonomy(conn_rows: list[tuple[str, str]], csv_path: Path):
    """Merge DB taxonomy + CSV taxonomy. CSV wins for ingredient_category
    (per instruction); DB casing wins for canonical ingredient_name when
    the ingredient already exists there."""
    existing_map = {normalize(name): (name.strip(), cat) for name, cat in conn_rows}

    tax = pd.read_csv(csv_path)
    tax["key"] = tax["Ingredients"].str.strip().str.lower()
    tax = tax.drop_duplicates(subset="key", keep="first")
    csv_map = {
        row["key"]: (row["Ingredients"].strip(), row["Ingredients_Category"].strip())
        for _, row in tax.iterrows()
    }

    canonical_name = {}
    category_lookup = {}
    for k, (name, cat) in existing_map.items():
        canonical_name[k] = name
        category_lookup[k] = cat
    for k, (name, cat) in csv_map.items():
        category_lookup[k] = cat  # CSV is authoritative for category
        canonical_name.setdefault(k, name)  # only fills in casing for brand-new names

    new_entries = [
        (canonical_name[k], category_lookup[k], "ING_TAXONOMY_CSV")
        for k in csv_map
        if k not in existing_map
    ]
    return canonical_name, category_lookup, new_entries


def build_pattern(canonical_name: dict[str, str]) -> re.Pattern:
    names_sorted = sorted(canonical_name.keys(), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in names_sorted) + r")\b")


def extract_ingredients(text: str, pattern: re.Pattern) -> set[str]:
    found = set()
    for tok in str(text).split(","):
        t = tok.strip().lower().strip("-").strip()
        if not t:
            continue
        found.update(pattern.findall(t))
    return found


async def main():
    t_start = time.time()

    # ── 1. Load taxonomy (DB + CSV) ──────────────────────────────────────────
    conn = await asyncpg.connect(DB_DSN)
    existing_rows = await conn.fetch(
        "SELECT ingredient_name, ingredient_category FROM ingredients_taxonomy"
    )
    canonical_name, category_lookup, new_entries = build_taxonomy(
        [(r["ingredient_name"], r["ingredient_category"]) for r in existing_rows],
        TAXONOMY_CSV,
    )
    logger.info(f"Taxonomy: {len(existing_rows)} existing + {len(new_entries)} new from CSV")

    if new_entries:
        await conn.executemany(
            """
            INSERT INTO ingredients_taxonomy (ingredient_name, ingredient_category, source)
            VALUES ($1, $2, $3)
            ON CONFLICT (ingredient_name) DO NOTHING
            """,
            new_entries,
        )
        logger.info(f"Inserted {len(new_entries)} new ingredient_taxonomy rows")

    pattern = build_pattern(canonical_name)

    # ── 2. Load talabat_menu_items for join ──────────────────────────────────
    mi_rows = await conn.fetch("SELECT id, branch_id, item_name, item_key FROM talabat_menu_items")
    mi = pd.DataFrame(mi_rows, columns=["id", "branch_id", "item_name", "item_key"])
    mi["norm"] = mi["item_name"].str.strip().str.lower()
    logger.info(f"Loaded {len(mi):,} talabat_menu_items rows for matching")
    await conn.close()

    # ── 3. Load Excel + join ─────────────────────────────────────────────────
    df = pd.read_excel(EXCEL_PATH)
    df["norm"] = df["Item Name"].astype(str).str.strip().str.lower()
    merged = df.merge(mi[["id", "branch_id", "norm", "item_key"]], on=["branch_id", "norm"], how="left")

    matched = merged[merged["id"].notna()].copy()
    unmatched_count = merged["id"].isna().sum()
    logger.info(f"Excel rows: {len(df):,} | matched: {len(matched):,} | unmatched: {unmatched_count:,}")

    matched["id"] = matched["id"].astype("int64")

    # ── 4. Prepare std_term update pairs ─────────────────────────────────────
    std_term_pairs = list(zip(matched["id"], matched["Std_Term (Reference)"]))

    # ── 5. Extract ingredients per matched row ───────────────────────────────
    logger.info("Extracting ingredients from free-text column ...")
    t0 = time.time()
    ingredient_rows = []
    for branch_id, item_key, ing_text in zip(
        matched["branch_id"], matched["item_key"], matched["Ingredients"]
    ):
        if pd.isna(ing_text):
            continue
        for ing in extract_ingredients(ing_text, pattern):
            ingredient_rows.append(
                (
                    "talabat",
                    str(branch_id),
                    item_key,
                    canonical_name[ing],
                    category_lookup.get(ing),
                    "keyword",
                    1.0,
                )
            )
    logger.info(f"Extracted {len(ingredient_rows):,} ingredient rows in {time.time()-t0:.1f}s")

    # ── 6. Async bulk write ──────────────────────────────────────────────────
    conn = await asyncpg.connect(DB_DSN)
    async with conn.transaction():
        # 6a. std_term via staging table + set-based UPDATE
        await conn.execute("CREATE TEMP TABLE staging_std_term (id bigint, std_term text) ON COMMIT DROP")
        await conn.copy_records_to_table("staging_std_term", records=std_term_pairs, columns=["id", "std_term"])
        result = await conn.execute(
            """
            UPDATE talabat_menu_items t
            SET std_term = s.std_term
            FROM staging_std_term s
            WHERE t.id = s.id
            """
        )
        logger.info(f"std_term update: {result}")

        # 6b. menu_item_ingredients bulk insert
        await conn.copy_records_to_table(
            "menu_item_ingredients",
            records=ingredient_rows,
            columns=["platform", "branch_id", "item_key", "ingredient_name", "ingredient_category", "extraction_method", "confidence"],
        )
        logger.info(f"menu_item_ingredients insert: {len(ingredient_rows):,} rows")

    await conn.close()

    logger.info(f"Done in {time.time()-t_start:.1f}s total")


if __name__ == "__main__":
    asyncio.run(main())
