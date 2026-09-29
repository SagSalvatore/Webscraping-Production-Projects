"""
export_to_csv.py — Export full Talabat menu data from Supabase to CSV + Excel.

Output columns (per management spec):
  branch_id | restaurant_id | RESTRO NAME | ld_name | Type of Restaurants |
  map_url | serves_cuisine | ld_lat | ld_lon | area_id | area_name |
  KEY CUISINES | Menu category | Menu item(name) | Price | DESCRIPTION |
  Outlet Type | Chained Outlet Type | Std terms | INGREDIENTS | contact |
  scraped_at

Two output files per run:
  data/exports/talabat_full_YYYYMMDD_HHMMSS.csv    ← all ~386K item rows
  data/exports/talabat_full_YYYYMMDD_HHMMSS.xlsx   ← 2 sheets: Full Menu + Restaurants

Usage:
  python export_to_csv.py                   # full export (CSV + Excel)
  python export_to_csv.py --format csv      # CSV only (faster)
  python export_to_csv.py --format excel    # Excel only
  python export_to_csv.py --restaurants     # restaurant-level summary only (no menu items)
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Force UTF-8 stdout on Windows — prevents UnicodeEncodeError on → ✓ etc.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")

EXPORT_DIR = Path(__file__).resolve().parent / "data" / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

PAGE_SIZE = 1000   # safe default — Supabase free tier caps at 1000/request


# ── Column mapping: user-facing name → final output ───────────────────────────

FULL_COLUMNS = [
    "branch_id",
    "restaurant_id",
    "RESTRO NAME",
    "ld_name",
    "Type of Restaurants",
    "map_url",
    "serves_cuisine",
    "ld_lat",
    "ld_lon",
    "area_id",
    "area_name",
    "KEY CUISINES",
    "Menu category",
    "Menu item(name)",
    "Price",
    "DESCRIPTION",
    "Outlet Type",
    "Chained Outlet Type",
    "Std terms",
    "INGREDIENTS",
    "contact",
    "scraped_at",
]

REST_COLUMNS = [
    "branch_id",
    "restaurant_id",
    "RESTRO NAME",
    "ld_name",
    "Type of Restaurants",
    "map_url",
    "serves_cuisine",
    "ld_lat",
    "ld_lon",
    "area_id",
    "area_name",
    "KEY CUISINES",
    "Outlet Type",
    "Chained Outlet Type",
    "Std terms",
    "contact",
    "first_scraped_at",
    "updated_at",
]


# ── Supabase paginated fetch ───────────────────────────────────────────────────

def fetch_all(client, table: str, select: str = "*", order_col: str = "branch_id") -> list[dict]:
    """Fetch every row from a table using offset pagination."""
    rows = []
    offset = 0
    t0 = time.perf_counter()
    while True:
        resp = (
            client.table(table)
            .select(select)
            .order(order_col)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = resp.data or []
        if not batch:
            break
        rows.extend(batch)
        offset += PAGE_SIZE
        elapsed = time.perf_counter() - t0
        print(f"  {table}: {len(rows):,} rows  ({elapsed:.0f}s) ...", end="\r")
        if len(batch) < PAGE_SIZE:
            break
    elapsed = time.perf_counter() - t0
    print(f"  {table}: {len(rows):,} rows fetched in {elapsed:.1f}s{' ' * 20}")
    return rows


# ── Array → comma-separated string ────────────────────────────────────────────

def arr_to_str(v) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x)
    return str(v) if v else ""


# ── Build the merged flat DataFrame ───────────────────────────────────────────

def build_flat_df(restaurants: list[dict], menu_items: list[dict]) -> pd.DataFrame:
    df_r = pd.DataFrame(restaurants)
    df_m = pd.DataFrame(menu_items) if menu_items else pd.DataFrame()

    # Convert PostgreSQL array columns to readable strings
    for col in ("serves_cuisine", "key_cuisines"):
        if col in df_r.columns:
            df_r[col] = df_r[col].apply(arr_to_str)

    # Ensure numeric lat/lon (stored as text in DB)
    for col in ("ld_lat", "ld_lon"):
        if col in df_r.columns:
            df_r[col] = pd.to_numeric(df_r[col], errors="coerce")

    if df_m.empty:
        # Restaurants-only export — still produce one row per restaurant
        df = df_r.copy()
        df["item_name"]      = ""
        df["menu_category"]  = ""
        df["price_aed"]      = None
        df["description"]    = ""
        df["scraped_at"]     = df_r.get("first_scraped_at", "")
    else:
        df_m["branch_id"] = pd.to_numeric(df_m["branch_id"], errors="coerce")
        df_r["branch_id"] = pd.to_numeric(df_r["branch_id"], errors="coerce")
        df = df_m.merge(df_r, on="branch_id", how="left", suffixes=("_item", "_rest"))

    # Helper: pick column, falling back gracefully
    def col(name_in_df: str, fallback=""):
        if name_in_df in df.columns:
            return df[name_in_df].fillna(fallback)
        return fallback

    final = pd.DataFrame({
        "branch_id":            col("branch_id"),
        "restaurant_id":        col("restaurant_id"),
        "RESTRO NAME":          col("restaurant_name"),
        "ld_name":              col("ld_name"),
        "Type of Restaurants":  col("restaurant_type"),
        "map_url":              col("map_url"),
        "serves_cuisine":       col("serves_cuisine"),
        "ld_lat":               col("ld_lat"),
        "ld_lon":               col("ld_lon"),
        "area_id":              col("area_id"),
        "area_name":            col("area_name"),
        "KEY CUISINES":         col("key_cuisines"),
        "Menu category":        col("menu_category"),
        "Menu item(name)":      col("item_name"),
        "Price":                col("price_aed"),
        "DESCRIPTION":          col("description"),
        "Outlet Type":          col("outlet_type"),
        "Chained Outlet Type":  col("chained_outlet_type"),
        "Std terms":            col("std_terms"),
        "INGREDIENTS":          "",          # future GPT enrichment field
        "contact":              col("contact"),
        "scraped_at":           col("scraped_at"),
    })

    return final


def build_restaurants_df(restaurants: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(restaurants)
    for c in ("serves_cuisine", "key_cuisines"):
        if c in df.columns:
            df[c] = df[c].apply(arr_to_str)
    for c in ("ld_lat", "ld_lon"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    out = pd.DataFrame({
        "branch_id":            df.get("branch_id", ""),
        "restaurant_id":        df.get("restaurant_id", ""),
        "RESTRO NAME":          df.get("restaurant_name", ""),
        "ld_name":              df.get("ld_name", ""),
        "Type of Restaurants":  df.get("restaurant_type", ""),
        "map_url":              df.get("map_url", ""),
        "serves_cuisine":       df.get("serves_cuisine", ""),
        "ld_lat":               df.get("ld_lat"),
        "ld_lon":               df.get("ld_lon"),
        "area_id":              df.get("area_id"),
        "area_name":            df.get("area_name", ""),
        "KEY CUISINES":         df.get("key_cuisines", ""),
        "Outlet Type":          df.get("outlet_type", ""),
        "Chained Outlet Type":  df.get("chained_outlet_type", ""),
        "Std terms":            df.get("std_terms", ""),
        "contact":              df.get("contact", ""),
        "first_scraped_at":     df.get("first_scraped_at", ""),
        "updated_at":           df.get("updated_at", ""),
    })
    return out


# ── Excel writer with auto column-width ───────────────────────────────────────

_ILLEGAL_CHARS_RE = __import__("re").compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")

def _strip_illegal(val):
    """Remove openpyxl-illegal control characters from string values."""
    return _ILLEGAL_CHARS_RE.sub("", val) if isinstance(val, str) else val

def _sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    obj_cols = df.select_dtypes(include="object").columns
    df = df.copy()
    for c in obj_cols:
        df[c] = df[c].map(_strip_illegal)
    return df

def write_excel(path: Path, full_df: pd.DataFrame, rest_df: pd.DataFrame):
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    full_df = _sanitize_df(full_df)
    rest_df = _sanitize_df(rest_df)

    HDR_BG  = "1F3864"
    HDR_FG  = "FFFFFF"
    ROW_LIMIT = 1_048_575   # Excel max rows per sheet

    with pd.ExcelWriter(path, engine="openpyxl") as writer:

        # ── Sheet 1: Full Menu (capped at Excel row limit) ────────────────────
        rows_to_write = full_df.head(ROW_LIMIT)
        rows_to_write.to_excel(writer, sheet_name="Full Menu", index=False, startrow=0)
        ws = writer.sheets["Full Menu"]
        _style_header(ws, len(rows_to_write.columns), HDR_BG, HDR_FG)
        _autofit(ws, rows_to_write)

        if len(full_df) > ROW_LIMIT:
            # Write overflow rows to a continuation sheet
            overflow = full_df.iloc[ROW_LIMIT:]
            overflow.to_excel(writer, sheet_name="Full Menu (cont)", index=False, startrow=0)
            ws2 = writer.sheets["Full Menu (cont)"]
            _style_header(ws2, len(overflow.columns), HDR_BG, HDR_FG)
            _autofit(ws2, overflow)
            print(f"  Excel: overflow → 'Full Menu (cont)' sheet ({len(overflow):,} rows)")

        # ── Sheet 2: Restaurants ──────────────────────────────────────────────
        rest_df.to_excel(writer, sheet_name="Restaurants", index=False, startrow=0)
        ws3 = writer.sheets["Restaurants"]
        _style_header(ws3, len(rest_df.columns), "2E4057", HDR_FG)
        _autofit(ws3, rest_df)

    print(f"  Excel saved → {path.name}")


def _style_header(ws, ncols: int, bg: str, fg: str):
    from openpyxl.styles import Font, PatternFill, Alignment
    for col in range(1, ncols + 1):
        cell = ws.cell(row=1, column=col)
        cell.font      = Font(name="Arial", bold=True, color=fg, size=9)
        cell.fill      = PatternFill("solid", start_color=bg)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=False)
    ws.row_dimensions[1].height = 18


def _autofit(ws, df: pd.DataFrame, max_width: int = 50):
    from openpyxl.utils import get_column_letter
    for i, col in enumerate(df.columns, 1):
        # Max of header length and longest cell value (sampled)
        sample = df[col].astype(str).head(500)
        max_len = max(len(str(col)), sample.str.len().max() if not sample.empty else 0)
        ws.column_dimensions[get_column_letter(i)].width = min(max_len + 2, max_width)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Export Talabat data to CSV + Excel")
    parser.add_argument("--format", choices=["csv", "excel", "both"], default="both",
                        help="Output format (default: both)")
    parser.add_argument("--restaurants", action="store_true",
                        help="Export restaurant-level summary only (no menu items)")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_ANON_KEY must be set in talabat/.env")
        sys.exit(1)

    try:
        from supabase import create_client
    except ImportError:
        print("ERROR: supabase not installed — run: pip install supabase")
        sys.exit(1)

    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 60)
    print(f"Talabat Export — {run_ts}")
    print(f"Format  : {args.format}")
    print(f"Mode    : {'Restaurants only' if args.restaurants else 'Full (restaurants + menu)'}")
    print(f"Output  : {EXPORT_DIR}")
    print("=" * 60)

    # 1. Fetch restaurants (always needed)
    print("\nFetching restaurants ...")
    restaurants = fetch_all(client, "talabat_restaurants", "*", "branch_id")
    rest_df = build_restaurants_df(restaurants)

    # 2. Fetch menu items (unless restaurants-only mode)
    if args.restaurants:
        full_df = rest_df.copy()
        suffix  = "restaurants"
    else:
        print("Fetching menu items ...")
        menu_items = fetch_all(client, "talabat_menu_items", "*", "branch_id")
        full_df = build_flat_df(restaurants, menu_items)
        suffix  = "full"

    print(f"\nRows to export : {len(full_df):,}")
    print(f"Restaurants    : {len(rest_df):,}")

    # 3. CSV
    if args.format in ("csv", "both"):
        csv_path = EXPORT_DIR / f"talabat_{suffix}_{run_ts}.csv"
        t = time.perf_counter()
        # utf-8-sig so Excel opens Arabic/emoji columns correctly without BOM issues
        full_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        size_mb = csv_path.stat().st_size / 1_048_576
        print(f"\nCSV  → {csv_path.name}  ({size_mb:.1f} MB, {time.perf_counter()-t:.1f}s)")

    # 4. Excel
    if args.format in ("excel", "both"):
        xlsx_path = EXPORT_DIR / f"talabat_{suffix}_{run_ts}.xlsx"
        t = time.perf_counter()
        print("Writing Excel ... (this may take ~30-60s for large datasets)")
        write_excel(xlsx_path, full_df, rest_df)
        size_mb = xlsx_path.stat().st_size / 1_048_576
        print(f"Excel → {xlsx_path.name}  ({size_mb:.1f} MB, {time.perf_counter()-t:.1f}s)")

    print("\n" + "=" * 60)
    print("Export complete.")
    print(f"Files saved to: {EXPORT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
