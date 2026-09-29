#!/usr/bin/env python3
"""
apply_manual_corrections.py
Push human-reviewed std_term corrections to talabat_menu_items and update
master_mapping.parquet so future runs don't overwrite them.

Method is set to 'manual', confidence=1.0, review_required=False for all.
"""

import asyncio
import logging
import sys
from pathlib import Path

import asyncpg
import polars as pl

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_PASSWORD  # noqa: E402

LOCAL_PG = dict(
    host="localhost", port=5432,
    database="RestaurantIntelligence",
    user="postgres", password=PG_PASSWORD,
)

MASTER_PARQUET = Path(__file__).parent / "master_mapping.parquet"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Human-reviewed corrections
# (item_key_clean, std_term, taxonomy)
# ---------------------------------------------------------------------------
CORRECTIONS = [
    ("veg_dim_sum(fried)",              "dim sum and dumplings",     "core_food"),
    ("sri_lankan_ribbon_cake_1kg",       "sri lankan breakfast",      "breakfast"),
    ("mongolian_stir-fry",              "stir-fry vegetables",       "core_food"),
    ("bondas_ghee_roast_(squid)",       "seafood starters",          "core_food"),
    ("veg_dim_sum",                     "dim sum and dumplings",     "core_food"),
    ("mangolian_stir_fry",              "stir-fry vegetables",       "core_food"),
    ("cashewnut_stir_fry",              "stir-fry vegetables",       "core_food"),
    ("mix_seafood_dish",                "continental seafood dish",  "core_food"),
    ("chicken_dim_sum(_fried)",         "dim sum and dumplings",     "core_food"),
    ("jengi_ghee_roast_(crab)",         "meat/seafood bites",        "core_food"),
    ("yeti_-_ghee_roast_(prawns)",      "meat/seafood bites",        "core_food"),
    ("chicken_dim_sum",                 "dim sum and dumplings",     "core_food"),
    ("sri_lankan_coffee_cake_1kg",      "sri lankan breakfast",      "breakfast"),
    ("pain_au_chocola",                 "pain au chocolat",          "core_food"),
    ("schezwan_stir_fry",               "stir-fry vegetables",       "core_food"),
    ("sri_lankan_rice",                 "sri lankan breakfast",      "breakfast"),
    ("pain_au_lait",                    "pain au chocolat",          "core_food"),
    ("cashewnut_stir-fry",              "stir-fry vegetables",       "core_food"),
    ("pain_au_raisin",                  "pain au chocolat",          "core_food"),
    ("mix_seafood_ghee_roast",          "mangalorean ghee roast",    "core_food"),
    ("the_heart_s_desire_luxury_set",   "western breakfast set",     "breakfast"),
]


async def apply_db(pool: asyncpg.Pool) -> int:
    """Bulk update via staging table. Returns rows updated."""
    records = [
        (key, std, tax, 1.0, "manual", False)
        for key, std, tax in CORRECTIONS
    ]

    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS public._manual_corr_staging;")
        await conn.execute("""
            CREATE TABLE public._manual_corr_staging (
                item_key_clean  TEXT PRIMARY KEY,
                std_term        TEXT,
                taxonomy        TEXT,
                confidence      FLOAT,
                method          TEXT,
                review_required BOOLEAN
            );
        """)
        await conn.copy_records_to_table(
            "_manual_corr_staging",
            records=records,
            columns=["item_key_clean", "std_term", "taxonomy",
                     "confidence", "method", "review_required"],
            schema_name="public",
        )
        result = await conn.execute("""
            UPDATE talabat_menu_items t
            SET
                std_term        = s.std_term,
                taxonomy        = s.taxonomy,
                confidence      = s.confidence,
                method          = s.method,
                review_required = s.review_required
            FROM public._manual_corr_staging s
            WHERE t.item_key_clean = s.item_key_clean;
        """)
        await conn.execute("DROP TABLE IF EXISTS public._manual_corr_staging;")

    updated = int(result.split()[-1])
    return updated


def update_parquet() -> None:
    """Patch master_mapping.parquet in-place."""
    if not MASTER_PARQUET.exists():
        log.warning("master_mapping.parquet not found — skipping parquet update")
        return

    df = pl.read_parquet(MASTER_PARQUET)

    corr_df = pl.DataFrame({
        "item_key_clean":  [c[0] for c in CORRECTIONS],
        "std_term":        [c[1] for c in CORRECTIONS],
        "taxonomy":        [c[2] for c in CORRECTIONS],
        "confidence":      [1.0]  * len(CORRECTIONS),
        "method":          ["manual"] * len(CORRECTIONS),
        "review_required": [False] * len(CORRECTIONS),
    })

    # Remove old entries for these keys, then append corrected ones
    keys_to_patch = set(c[0] for c in CORRECTIONS)
    df_patched = df.filter(~pl.col("item_key_clean").is_in(list(keys_to_patch)))
    df_patched = pl.concat([df_patched, corr_df])
    df_patched.write_parquet(MASTER_PARQUET)
    log.info("Parquet updated: %d total entries", len(df_patched))


async def main() -> None:
    log.info("Applying %d manual corrections...", len(CORRECTIONS))

    pool = await asyncpg.create_pool(**LOCAL_PG, min_size=1, max_size=3)
    db_rows = await apply_db(pool)
    await pool.close()
    log.info("DB: UPDATE affected %d rows in talabat_menu_items", db_rows)

    update_parquet()

    # Verify
    pool2 = await asyncpg.create_pool(**LOCAL_PG, min_size=1, max_size=2)
    async with pool2.acquire() as conn:
        rows = await conn.fetch("""
            SELECT item_key_clean, std_term, taxonomy, confidence, method
            FROM talabat_menu_items
            WHERE method = 'manual'
            ORDER BY item_key_clean
            LIMIT 25;
        """)
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM talabat_menu_items WHERE method = 'manual'"
        )
    await pool2.close()

    log.info("Verification — method='manual' rows in DB: %d", count)
    for r in rows:
        log.info("  %-40s -> %-30s [%s]", r["item_key_clean"], r["std_term"], r["taxonomy"])


if __name__ == "__main__":
    asyncio.run(main())
