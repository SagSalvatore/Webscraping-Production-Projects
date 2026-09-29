"""
Recover std_term + ingredients for Excel rows that failed the exact
(branch_id, item_name) join against talabat_menu_items.

Root cause: talabat_menu_items.item_name stores bilingual text
("Belgian Hot Chocolate بلجيكية شوكولاتة"), while the ingredients Excel
kept only the English portion, lowercased ("belgian hot chocolate").

Recovery strategy (per branch_id, safety-first):
  1. Unique prefix match  -> DB item_name (normalized) starts with Excel name
  2. Unique substring match (fallback) -> Excel name appears anywhere in DB item_name
  3. Anything matching 2+ DB rows is left ambiguous, never guessed
  4. Fragments < 4 chars are never matched (too risky)

Writes std_term + ingredient rows ONLY for the safely-recovered matches.
Exports ambiguous / still-unmatched rows to CSV for manual review.

Run:
  python talabat/ingredients/load_recovered_unmatched.py
"""

import asyncio
import re
import time
from collections import defaultdict
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
UNMATCHED_CSV = Path("C:/Users/SagarSingh/Downloads/unmatched_excel_rows.csv")
OUT_DIR = Path("C:/Users/SagarSingh/Downloads")

DB_DSN = PG_DSN

FLOWER_IDS = {788728, 788731, 788733, 788749, 788754}


def norm_name(s: str) -> str:
    s = str(s).strip().lower()
    s = re.sub(r"^[\-\|\*\s\d]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_taxonomy(existing_rows, csv_path: Path):
    existing_map = {r[0].strip().lower(): (r[0].strip(), r[1]) for r in existing_rows}
    tax = pd.read_csv(csv_path)
    tax["key"] = tax["Ingredients"].str.strip().str.lower()
    tax = tax.drop_duplicates(subset="key", keep="first")
    csv_map = {
        row["key"]: (row["Ingredients"].strip(), row["Ingredients_Category"].strip())
        for _, row in tax.iterrows()
    }
    canonical_name, category_lookup = {}, {}
    for k, (name, cat) in existing_map.items():
        canonical_name[k] = name
        category_lookup[k] = cat
    for k, (name, cat) in csv_map.items():
        category_lookup[k] = cat
        canonical_name.setdefault(k, name)
    return canonical_name, category_lookup


def build_pattern(canonical_name: dict[str, str]) -> re.Pattern:
    names_sorted = sorted(canonical_name.keys(), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in names_sorted) + r")\b")


def extract_ingredients(text: str, pattern: re.Pattern) -> set[str]:
    found = set()
    for tok in str(text).split(","):
        t = tok.strip().lower().strip("-").strip()
        if t:
            found.update(pattern.findall(t))
    return found


async def main():
    t_start = time.time()
    conn = await asyncpg.connect(DB_DSN)

    # ── 1. Taxonomy ───────────────────────────────────────────────────────────
    existing_rows = await conn.fetch("SELECT ingredient_name, ingredient_category FROM ingredients_taxonomy")
    canonical_name, category_lookup = build_taxonomy(
        [(r["ingredient_name"], r["ingredient_category"]) for r in existing_rows], TAXONOMY_CSV
    )
    pattern = build_pattern(canonical_name)
    logger.info(f"Taxonomy loaded: {len(canonical_name)} terms")

    # ── 2. Load DB menu items, grouped per branch ────────────────────────────
    mi_rows = await conn.fetch("SELECT id, branch_id, item_name, item_key FROM talabat_menu_items")
    await conn.close()

    db_by_branch = defaultdict(list)
    for r in mi_rows:
        db_by_branch[r["branch_id"]].append((norm_name(r["item_name"]), r["id"], r["item_key"]))
    logger.info(f"Loaded {len(mi_rows):,} talabat_menu_items rows, grouped into {len(db_by_branch):,} branches")

    # ── 3. Load unmatched rows + full Excel (need Ingredients / Std_Term for recovered ones) ──
    unmatched = pd.read_csv(UNMATCHED_CSV, encoding="utf-8-sig")
    unmatched = unmatched[(~unmatched["branch_id"].isin(FLOWER_IDS)) & (unmatched["branch_id"] != 0)].copy()
    unmatched["norm"] = unmatched["Item Name"].apply(norm_name)
    logger.info(f"Unmatched rows to attempt recovery on: {len(unmatched):,}")

    # ── 4. Prefix -> substring fallback matching ─────────────────────────────
    recovered_ids, recovered_item_keys = [], []
    ambiguous_rows, no_match_rows = [], []

    for r in unmatched.itertuples():
        candidates = db_by_branch.get(r.branch_id, [])
        if len(r.norm) < 4:
            no_match_rows.append(r.Index)
            recovered_ids.append(None)
            recovered_item_keys.append(None)
            continue

        hits = [c for c in candidates if c[0].startswith(r.norm)]
        if len(hits) == 1:
            recovered_ids.append(hits[0][1])
            recovered_item_keys.append(hits[0][2])
            continue
        if len(hits) > 1:
            ambiguous_rows.append(r.Index)
            recovered_ids.append(None)
            recovered_item_keys.append(None)
            continue

        hits2 = [c for c in candidates if r.norm in c[0]]
        if len(hits2) == 1:
            recovered_ids.append(hits2[0][1])
            recovered_item_keys.append(hits2[0][2])
        elif len(hits2) > 1:
            ambiguous_rows.append(r.Index)
            recovered_ids.append(None)
            recovered_item_keys.append(None)
        else:
            no_match_rows.append(r.Index)
            recovered_ids.append(None)
            recovered_item_keys.append(None)

    unmatched["recovered_id"] = recovered_ids
    unmatched["recovered_item_key"] = recovered_item_keys

    recovered = unmatched[unmatched["recovered_id"].notna()].copy()
    ambiguous = unmatched.loc[ambiguous_rows].copy()
    still_no_match = unmatched.loc[no_match_rows].copy()

    logger.info(
        f"Recovered: {len(recovered):,} | Ambiguous: {len(ambiguous):,} | Still no match: {len(still_no_match):,}"
    )

    # ── 5. Export ambiguous + no-match for manual review ─────────────────────
    export_cols = ["branch_id", "restaurant_id", "restaurant_name", "Item Name", "Category", "Description", "Ingredients", "Std_Term (Reference)"]
    ambiguous[export_cols].to_csv(OUT_DIR / "unmatched_ambiguous_review.csv", index=False, encoding="utf-8-sig")
    still_no_match[export_cols].to_csv(OUT_DIR / "unmatched_still_no_match.csv", index=False, encoding="utf-8-sig")
    logger.info("Exported unmatched_ambiguous_review.csv and unmatched_still_no_match.csv")

    # ── 6. Std_term pairs + ingredient extraction for recovered rows ─────────
    recovered["id"] = recovered["recovered_id"].astype("int64")
    std_term_pairs = list(zip(recovered["id"], recovered["Std_Term (Reference)"]))

    logger.info("Extracting ingredients for recovered rows ...")
    t0 = time.time()
    ingredient_rows = []
    for branch_id, item_key, ing_text in zip(
        recovered["branch_id"], recovered["recovered_item_key"], recovered["Ingredients"]
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

    # ── 7. Async bulk write ───────────────────────────────────────────────────
    conn = await asyncpg.connect(DB_DSN)
    async with conn.transaction():
        await conn.execute("CREATE TEMP TABLE staging_std_term2 (id bigint, std_term text) ON COMMIT DROP")
        await conn.copy_records_to_table("staging_std_term2", records=std_term_pairs, columns=["id", "std_term"])
        result = await conn.execute(
            """
            UPDATE talabat_menu_items t
            SET std_term = s.std_term
            FROM staging_std_term2 s
            WHERE t.id = s.id
            """
        )
        logger.info(f"std_term update: {result}")

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
