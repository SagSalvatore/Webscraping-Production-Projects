#!/usr/bin/env python3
"""
export_excel_review.py
Exports talabat data from local PostgreSQL to Excel for std_term review.

Output: talabat_menu_analysis.xlsx  (4 sheets)
  Sheet 1 — Summary        : coverage stats
  Sheet 2 — Unique_Items   : 355K unique item_key_clean + one restaurant context
  Sheet 3 — Needs_Review   : ~55K rows where review_required=True, worst first
  Sheet 4 — Restaurants    : 15K restaurant reference

Also writes: talabat_queries.sql  (copy-paste into pgAdmin4)

Run:
    python talabat/menu/export_excel_review.py
"""

import asyncio, logging, sys, time
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

LOCAL_PG = dict(host="localhost", port=5432, database="RestaurantIntelligence",
                user="postgres", password=PG_PASSWORD)

OUT_EXCEL = Path(__file__).parent / "talabat_menu_analysis.xlsx"
OUT_SQL   = Path(__file__).parent / "talabat_queries.sql"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

Q_UNIQUE = """
SELECT DISTINCT ON (mi.item_key_clean)
    r.branch_id,
    r.restaurant_id,
    r.restaurant_name,
    r.ld_name,
    r.map_url,
    array_to_string(r.serves_cuisine, ', ')    AS serves_cuisines,
    r.ld_lat,
    r.ld_lon,
    r.area_id,
    r.area_name,
    mi.item_key_clean,
    replace(mi.item_key_clean, '_', ' ')       AS item_name_readable,
    mi.menu_category_clean,
    replace(COALESCE(mi.menu_category_clean,''), '_', ' ') AS category_readable,
    mi.description_clean,
    mi.std_term,
    mi.taxonomy,
    ROUND(mi.confidence::numeric, 4)           AS confidence,
    mi.method,
    mi.review_required
FROM talabat_menu_items mi
INNER JOIN talabat_restaurants r ON mi.branch_id = r.branch_id
WHERE mi.item_key_clean IS NOT NULL
ORDER BY mi.item_key_clean, r.restaurant_name
"""

Q_RESTAURANTS = """
SELECT
    branch_id, restaurant_id, restaurant_name, ld_name, map_url,
    array_to_string(serves_cuisine, ', ')  AS serves_cuisines,
    array_to_string(key_cuisines,   ', ')  AS key_cuisines,
    restaurant_type, outlet_type,
    ld_lat, ld_lon, area_id, area_name,
    first_scraped_at, updated_at
FROM talabat_restaurants
ORDER BY area_name, restaurant_name
"""

Q_COVERAGE = """
SELECT
    COUNT(*)                                           AS total_items,
    COUNT(std_term)                                    AS matched,
    COUNT(DISTINCT item_key_clean)                     AS unique_keys,
    COUNT(*) FILTER (WHERE method='rule')              AS by_rule,
    COUNT(*) FILTER (WHERE method='exact')             AS by_exact,
    COUNT(*) FILTER (WHERE method='fuzzy')             AS by_fuzzy,
    COUNT(*) FILTER (WHERE method='tfidf')             AS by_tfidf,
    COUNT(*) FILTER (WHERE method='embedding')         AS by_embedding,
    COUNT(*) FILTER (WHERE method='manual')            AS by_manual,
    COUNT(*) FILTER (WHERE method IS NULL
                     OR method='unknown')              AS unknown,
    COUNT(*) FILTER (WHERE review_required=TRUE)       AS needs_review,
    COUNT(*) FILTER (WHERE confidence >= 0.95)         AS high_conf,
    COUNT(*) FILTER (WHERE confidence >= 0.70
                     AND   confidence <  0.95)         AS mid_conf,
    COUNT(*) FILTER (WHERE confidence <  0.70
                     AND   confidence IS NOT NULL)     AS low_conf
FROM talabat_menu_items
"""

Q_METHOD_TAX = """
SELECT method, taxonomy,
    COUNT(*)                                    AS item_rows,
    COUNT(DISTINCT branch_id)                   AS restaurants,
    ROUND(AVG(confidence)::numeric, 3)          AS avg_confidence
FROM talabat_menu_items
WHERE method IS NOT NULL
GROUP BY method, taxonomy
ORDER BY method, item_rows DESC
"""

# ---------------------------------------------------------------------------
# Fetch helper
# ---------------------------------------------------------------------------

async def fetch(pool, sql):
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    if not rows:
        return pl.DataFrame()
    cols = list(rows[0].keys())
    data = {}
    for c in cols:
        vals = [r[c] for r in rows]
        # asyncpg returns lists for ARRAY columns — already handled by array_to_string
        data[c] = vals
    return pl.DataFrame(data)

# ---------------------------------------------------------------------------
# Excel writer
# ---------------------------------------------------------------------------

COL_WIDTHS = {
    "branch_id": 12, "restaurant_id": 14, "restaurant_name": 28,
    "ld_name": 22, "map_url": 14, "serves_cuisines": 28, "key_cuisines": 22,
    "ld_lat": 12, "ld_lon": 12, "area_id": 8, "area_name": 18,
    "item_key_clean": 36, "item_name_readable": 36,
    "menu_category_clean": 22, "category_readable": 22,
    "description_clean": 45, "std_term": 28, "taxonomy": 18,
    "confidence": 10, "method": 11, "review_required": 14,
    "restaurant_type": 16, "outlet_type": 14,
    "first_scraped_at": 22, "updated_at": 22,
}


def write_sheet(ws, df, hdr_fmt, cell_fmt, flag_fmt, good_fmt, flag_col=None):
    cols = df.columns
    for ci, c in enumerate(cols):
        ws.set_column(ci, ci, COL_WIDTHS.get(c, 15))
        ws.write(0, ci, c, hdr_fmt)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, 0, len(cols) - 1)

    for ri, row in enumerate(df.to_dicts(), start=1):
        if flag_col:
            rev  = row.get("review_required", False)
            conf = row.get("confidence") or 0
            fmt  = flag_fmt if rev else (good_fmt if conf >= 0.95 else cell_fmt)
        else:
            fmt = cell_fmt

        for ci, c in enumerate(cols):
            val = row[c]
            if val is None:
                ws.write(ri, ci, "", fmt)
            elif isinstance(val, bool):
                ws.write(ri, ci, val, fmt)
            elif isinstance(val, float):
                ws.write(ri, ci, round(val, 4), fmt)
            elif hasattr(val, "isoformat"):
                ws.write(ri, ci, str(val), fmt)
            else:
                ws.write(ri, ci, val, fmt)


def build_excel(unique_df, rest_df, cov, method_df):
    try:
        import xlsxwriter
    except ImportError:
        log.error("pip install xlsxwriter")
        return

    t0 = time.time()
    with xlsxwriter.Workbook(str(OUT_EXCEL), {"constant_memory": True}) as wb:

        H = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "white",
                            "border": 1, "text_wrap": True, "valign": "vcenter"})
        C = wb.add_format({"border": 1})
        F = wb.add_format({"border": 1, "bg_color": "#FFE699"})   # needs review
        G = wb.add_format({"border": 1, "bg_color": "#E2EFDA"})   # high confidence
        T = wb.add_format({"bold": True, "font_size": 13, "font_color": "#1F4E79"})
        S = wb.add_format({"bold": True, "bg_color": "#D6E4F0"})
        N = wb.add_format({"border": 1, "num_format": "#,##0"})

        # ── Summary sheet ──────────────────────────────────────────────
        ws0 = wb.add_worksheet("Summary")
        ws0.set_column("A:A", 38); ws0.set_column("B:B", 20)
        ws0.write("A1", "Talabat UAE — Menu Classification Summary", T)
        stats = [
            ("Total menu item rows",         cov["total_items"]),
            ("Matched (have std_term)",       cov["matched"]),
            ("Unique item_key_clean",         cov["unique_keys"]),
            ("Needs human review",            cov["needs_review"]),
            ("", ""),
            ("By method", ""),
            ("  rule",                        cov["by_rule"]),
            ("  exact",                       cov["by_exact"]),
            ("  fuzzy",                       cov["by_fuzzy"]),
            ("  tfidf",                       cov["by_tfidf"]),
            ("  embedding",                   cov["by_embedding"]),
            ("  manual (human corrections)",  cov["by_manual"]),
            ("  unknown / unclassified",      cov["unknown"]),
            ("", ""),
            ("By confidence", ""),
            ("  high  >= 0.95",               cov["high_conf"]),
            ("  mid   0.70 – 0.94",           cov["mid_conf"]),
            ("  low   < 0.70",                cov["low_conf"]),
        ]
        for i, (label, val) in enumerate(stats, start=2):
            fmt = S if (label and not label.startswith(" ") and val == "") else C
            ws0.write(i, 0, label, fmt)
            ws0.write(i, 1, val, N if isinstance(val, int) else C)

        ws0.write(23, 0, "Method × Taxonomy breakdown", S)
        for ci, c in enumerate(method_df.columns):
            ws0.write(24, ci, c, H)
            ws0.set_column(ci, ci, 22)
        for ri, row in enumerate(method_df.to_dicts(), start=25):
            for ci, c in enumerate(method_df.columns):
                ws0.write(ri, ci, row[c], C)

        # ── Unique Items sheet ─────────────────────────────────────────
        ws1 = wb.add_worksheet("Unique_Items")
        log.info("Writing Unique_Items sheet (%d rows)...", len(unique_df))
        write_sheet(ws1, unique_df, H, C, F, G, flag_col="review_required")

        # ── Needs Review sheet ─────────────────────────────────────────
        rev_df = unique_df.filter(pl.col("review_required") == True).sort("confidence")
        ws2 = wb.add_worksheet("Needs_Review")
        log.info("Writing Needs_Review sheet (%d rows)...", len(rev_df))
        write_sheet(ws2, rev_df, H, F, F, F, flag_col=None)

        # ── Restaurants sheet ──────────────────────────────────────────
        ws3 = wb.add_worksheet("Restaurants")
        log.info("Writing Restaurants sheet (%d rows)...", len(rest_df))
        write_sheet(ws3, rest_df, H, C, F, G, flag_col=None)

    sz = OUT_EXCEL.stat().st_size / 1e6
    log.info("Excel written in %.1fs → %s  (%.1f MB)", time.time() - t0, OUT_EXCEL.name, sz)

# ---------------------------------------------------------------------------
# SQL file
# ---------------------------------------------------------------------------

def write_sql():
    sql = """-- ================================================================
-- Talabat UAE — pgAdmin4 Query Reference
-- Generated by export_excel_review.py
-- ================================================================


-- Q1: Unique menu items with one representative restaurant
--     355,727 rows — use this for std_term review in Excel
-- ----------------------------------------------------------------
SELECT DISTINCT ON (mi.item_key_clean)
    r.branch_id, r.restaurant_id, r.restaurant_name, r.ld_name, r.map_url,
    array_to_string(r.serves_cuisine, ', ')    AS serves_cuisines,
    r.ld_lat, r.ld_lon, r.area_id, r.area_name,
    mi.item_key_clean,
    replace(mi.item_key_clean, '_', ' ')       AS item_name_readable,
    mi.menu_category_clean,
    mi.description_clean,
    mi.std_term, mi.taxonomy,
    ROUND(mi.confidence::numeric, 4)           AS confidence,
    mi.method, mi.review_required
FROM talabat_menu_items mi
INNER JOIN talabat_restaurants r ON mi.branch_id = r.branch_id
WHERE mi.item_key_clean IS NOT NULL
ORDER BY mi.item_key_clean, r.restaurant_name;


-- Q2: Full 1.1M join (too large for Excel — use for CSV/data tools)
--     Run with COPY for CSV: COPY (SELECT ...) TO '/path/output.csv' CSV HEADER;
-- ----------------------------------------------------------------
SELECT
    r.branch_id, r.restaurant_id, r.restaurant_name,
    array_to_string(r.serves_cuisine, ', ')  AS serves_cuisines,
    r.area_name,
    mi.item_key_clean,
    replace(mi.item_key_clean, '_', ' ')     AS item_name_readable,
    mi.menu_category_clean, mi.description_clean,
    mi.std_term, mi.taxonomy,
    ROUND(mi.confidence::numeric, 4)         AS confidence,
    mi.method, mi.review_required
FROM talabat_menu_items mi
INNER JOIN talabat_restaurants r ON mi.branch_id = r.branch_id
ORDER BY r.area_name, r.restaurant_name, mi.menu_category_clean;


-- Q3: Full join filtered by AREA (manageable Excel slices ~50K-350K rows)
--     Change the area_name value as needed. List of areas below:
--     'Al Barsha 3', 'Al Dhagaya', 'Zayed Sports City', 'Al Jaddaf',
--     'Al Barsha 1', 'Al Barsha South', 'Al Garhoud', 'Al Furjan'
-- ----------------------------------------------------------------
SELECT
    r.branch_id, r.restaurant_id, r.restaurant_name, r.area_name,
    mi.item_key_clean,
    replace(mi.item_key_clean, '_', ' ')  AS item_name_readable,
    mi.menu_category_clean,
    mi.std_term, mi.taxonomy,
    ROUND(mi.confidence::numeric, 4)      AS confidence,
    mi.method, mi.review_required
FROM talabat_menu_items mi
INNER JOIN talabat_restaurants r ON mi.branch_id = r.branch_id
WHERE r.area_name = 'Al Barsha 3'          -- << change area here
ORDER BY r.restaurant_name, mi.menu_category_clean, mi.item_key_clean;


-- Q4: Only review_required items — unique, worst confidence first
-- ----------------------------------------------------------------
SELECT DISTINCT ON (mi.item_key_clean)
    r.restaurant_name, r.area_name,
    mi.item_key_clean,
    replace(mi.item_key_clean, '_', ' ')  AS item_name_readable,
    mi.menu_category_clean,
    mi.std_term, mi.taxonomy,
    ROUND(mi.confidence::numeric, 4)      AS confidence,
    mi.method
FROM talabat_menu_items mi
INNER JOIN talabat_restaurants r ON mi.branch_id = r.branch_id
WHERE mi.review_required = TRUE
  AND mi.item_key_clean IS NOT NULL
ORDER BY mi.item_key_clean, mi.confidence;


-- Q5: Coverage summary (single row)
-- ----------------------------------------------------------------
SELECT
    COUNT(*)                                           AS total_items,
    COUNT(std_term)                                    AS matched,
    COUNT(DISTINCT item_key_clean)                     AS unique_keys,
    COUNT(*) FILTER (WHERE method='rule')              AS by_rule,
    COUNT(*) FILTER (WHERE method='exact')             AS by_exact,
    COUNT(*) FILTER (WHERE method='fuzzy')             AS by_fuzzy,
    COUNT(*) FILTER (WHERE method='tfidf')             AS by_tfidf,
    COUNT(*) FILTER (WHERE method='embedding')         AS by_embedding,
    COUNT(*) FILTER (WHERE method='manual')            AS by_manual,
    COUNT(*) FILTER (WHERE review_required=TRUE)       AS needs_review
FROM talabat_menu_items;


-- Q6: Per-restaurant classification coverage
-- ----------------------------------------------------------------
SELECT
    r.branch_id, r.restaurant_name, r.area_name,
    COUNT(mi.id)                                       AS total_items,
    COUNT(mi.std_term)                                 AS classified,
    COUNT(*) FILTER (WHERE mi.review_required=TRUE)    AS needs_review,
    ROUND(100.0 * COUNT(mi.std_term)
          / NULLIF(COUNT(mi.id), 0), 1)                AS coverage_pct
FROM talabat_restaurants r
LEFT JOIN talabat_menu_items mi ON mi.branch_id = r.branch_id
GROUP BY r.branch_id, r.restaurant_name, r.area_name
ORDER BY total_items DESC;


-- Q7: All restaurants reference
-- ----------------------------------------------------------------
SELECT
    branch_id, restaurant_id, restaurant_name, ld_name, map_url,
    array_to_string(serves_cuisine, ', ')  AS serves_cuisines,
    array_to_string(key_cuisines,   ', ')  AS key_cuisines,
    restaurant_type, outlet_type,
    ld_lat, ld_lon, area_id, area_name,
    first_scraped_at, updated_at
FROM talabat_restaurants
ORDER BY area_name, restaurant_name;
"""
    OUT_SQL.write_text(sql, encoding="utf-8")
    log.info("SQL queries written → %s", OUT_SQL.name)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    log.info("Connecting to PostgreSQL...")
    pool = await asyncpg.create_pool(**LOCAL_PG, min_size=2, max_size=6)

    log.info("Fetching unique items (~355K rows)...")
    t0 = time.time()
    unique_df  = await fetch(pool, Q_UNIQUE)
    log.info("  %d rows in %.1fs", len(unique_df), time.time() - t0)

    log.info("Fetching restaurants...")
    rest_df    = await fetch(pool, Q_RESTAURANTS)
    log.info("  %d rows", len(rest_df))

    log.info("Fetching coverage stats...")
    cov_df     = await fetch(pool, Q_COVERAGE)
    cov        = cov_df.to_dicts()[0]
    method_df  = await fetch(pool, Q_METHOD_TAX)

    await pool.close()

    log.info("Coverage: %s/%s rows matched (%.1f%%)",
             cov["matched"], cov["total_items"],
             100 * cov["matched"] / cov["total_items"])
    log.info("Needs review (unique items): %d",
             len(unique_df.filter(pl.col("review_required") == True)))

    build_excel(unique_df, rest_df, cov, method_df)
    write_sql()

    log.info("=" * 55)
    log.info("Done.")
    log.info("  Excel : %s  (%.1f MB)", OUT_EXCEL.name, OUT_EXCEL.stat().st_size / 1e6)
    log.info("  SQL   : %s", OUT_SQL.name)
    log.info("=" * 55)


if __name__ == "__main__":
    asyncio.run(main())
