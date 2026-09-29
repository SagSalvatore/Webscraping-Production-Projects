"""
enrich_export.py — Enrich talabat_full_*.xlsx with restaurant classifications.

Matching tiers (applied in order, stops at first hit per restaurant):
  Tier 1 : Direct branch_id match from matched_results CSV     — confidence 1.0
  Tier 2 : Geo ≤ 30m + fuzzy name ≥ 0.85 vs TYPE_CHAIN_DATA  — confidence 0.99
  Tier 3 : Geo ≤ 100m + fuzzy name ≥ 0.90 vs TYPE_CHAIN_DATA — confidence 0.95
  Tier 4 : Geo ≤ 50m + exact normalised name                  — confidence 0.95

Columns added / updated in both sheets:
  Type of Restaurants   (standardised: Full-Service Restaurants / QSR / Cafes / Bakery / Cloud Kitchen)
  Outlet Type           (Independent / Chain)
  Chained Outlet Type   (Local Chain / MNC Chain / NULL)
  match_method          (new — tier used)
  confidence            (new — 0.0–1.0)

Usage:
  python enrich_export.py --input data/exports/talabat_full_20260618_182042.xlsx
  python enrich_export.py --input data/exports/talabat_full_20260618_182042.xlsx --format both
"""

import argparse
import math
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
from rapidfuzz import fuzz

ROOT       = Path(__file__).resolve().parent.parent
MR_DIR     = Path(__file__).resolve().parent / "data" / "entity_resolution"
EXPORT_DIR = Path(__file__).resolve().parent / "data" / "exports"


# ── Utilities ──────────────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    name = str(name).lower()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(
        r"\b(branch|br|uae|dubai|abu dhabi|sharjah|restaurant|rest|cafe|kitchen|kitch)\b",
        "", name,
    )
    return re.sub(r"\s+", " ", name).strip()


def haversine_vec(lat1: float, lon1: float, lats2: np.ndarray, lons2: np.ndarray) -> np.ndarray:
    """Vectorised haversine: distance in metres from one point to an array of points."""
    R = 6_371_000.0
    phi1  = math.radians(lat1)
    phi2  = np.radians(lats2)
    dphi  = np.radians(lats2 - lat1)
    dlam  = np.radians(lons2 - lon1)
    a     = np.sin(dphi / 2) ** 2 + math.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


# ── Load classification sources ────────────────────────────────────────────────

def load_matched_results() -> pd.DataFrame:
    """Latest matched_results CSV from entity_resolution run."""
    files = sorted(MR_DIR.glob("matched_results_*.csv"), reverse=True)
    if not files:
        raise FileNotFoundError(f"No matched_results CSV in {MR_DIR}")
    path = files[0]
    print(f"  matched_results: {path.name}")
    df = pd.read_csv(path, dtype={"branch_id": "Int64"})
    return df


def load_type_chain_geo() -> pd.DataFrame:
    """
    Build a geo-lookup table from TYPE_CHAIN_DATA.xlsx for tier-2/3 matching.
    Combines both sheets, deduplicates by (lat_r3, lon_r3) + name_norm,
    keeps the richest record (Both sheet wins over only_type).
    """
    excel = ROOT / "TYPE_CHAIN_DATA.xlsx"

    # ── Both sheet ──
    both = pd.read_excel(excel, sheet_name="Both")
    both["src_lat"] = both["Geo Code(Lat,Long)"].apply(
        lambda g: float(str(g).split(",")[0]) if pd.notna(g) else None
    )
    both["src_lon"] = both["Geo Code(Lat,Long)"].apply(
        lambda g: float(str(g).split(",")[1]) if pd.notna(g) and "," in str(g) else None
    )
    both_norm = pd.DataFrame({
        "name":               both["RESTRO NAME"],
        "restaurant_type":    both["Type of Restaurants"].map({
            "Full-Service Restaurants":  "Full-Service Restaurants",
            "Quick-Service Restaurants": "Quick-Service Restaurants",
            "Cafes": "Cafes", "Bakery": "Bakery",
        }).fillna(both["Type of Restaurants"]),
        "outlet_type":        both["Outlet Type"].where(pd.notna(both["Outlet Type"]), other=None),
        "chained_outlet_type": both["Chained Outlet Type"].where(pd.notna(both["Chained Outlet Type"]), other=None),
        "lat": both["src_lat"], "lon": both["src_lon"],
        "source": "Both",
    })

    # ── only_type sheet ──
    ot = pd.read_excel(excel, sheet_name="only_type",
                       dtype={"branch_id": int, "restaurant_id": int})
    TYPE_NORM = {
        "Full-Service Restaurant (FSR)":  "Full-Service Restaurants",
        "Quick-Service Restaurant (QSR)": "Quick-Service Restaurants",
        "Cafes": "Cafes", "Cloud Kitchen": "Cloud Kitchen",
    }
    ot_norm = pd.DataFrame({
        "name":               ot["RESTRO NAME"],
        "restaurant_type":    ot["Type of Restaurants"].map(TYPE_NORM).fillna(ot["Type of Restaurants"]),
        "outlet_type":        None,
        "chained_outlet_type": None,
        "lat": pd.to_numeric(ot["ld_lat"], errors="coerce"),
        "lon": pd.to_numeric(ot["ld_lon"], errors="coerce"),
        "source": "only_type",
    })

    combined = pd.concat([both_norm, ot_norm], ignore_index=True)
    combined = combined.dropna(subset=["lat", "lon"])
    combined["name_norm"]  = combined["name"].apply(normalize_name)
    combined["lat_r4"]     = combined["lat"].round(4).astype(str)

    # Both-sheet rows take priority (they have outlet_type data)
    combined = combined.sort_values("source", ascending=True)   # "Both" < "only_type" alphabetically → Both first
    combined = combined.drop_duplicates(subset=["name_norm", "lat_r4"], keep="first")
    print(f"  TYPE_CHAIN_DATA geo lookup: {len(combined)} unique entries (lat/lon present)")
    return combined.reset_index(drop=True)


# ── Geo+fuzzy matching for unclassified restaurants ────────────────────────────

def geo_fuzzy_match(unclassified: pd.DataFrame, lookup: pd.DataFrame) -> pd.DataFrame:
    """
    For each unclassified restaurant (has ld_lat, ld_lon, name),
    find the best match in lookup using:
      Tier 2: geo ≤ 30m  + name_sim ≥ 0.85  → confidence 0.99
      Tier 3: geo ≤ 100m + name_sim ≥ 0.90  → confidence 0.95
      Tier 4: geo ≤ 50m  + exact norm name   → confidence 0.95
    """
    lk_lats = lookup["lat"].values
    lk_lons = lookup["lon"].values
    lk_names = lookup["name_norm"].values

    results = []
    t0 = time.perf_counter()
    total = len(unclassified)

    for idx, (_, row) in enumerate(unclassified.iterrows()):
        if idx % 500 == 0:
            elapsed = time.perf_counter() - t0
            print(f"  geo matching: [{idx:,}/{total:,}]  ({elapsed:.0f}s)", end="\r")

        lat = row["ld_lat"]
        lon = row["ld_lon"]
        if pd.isna(lat) or pd.isna(lon):
            results.append(None)
            continue

        # Vectorised distance to all lookup entries
        dists = haversine_vec(float(lat), float(lon), lk_lats, lk_lons)

        # Fast reject: only consider entries within 300m
        near_mask = dists <= 300
        if not near_mask.any():
            results.append(None)
            continue

        name_norm = normalize_name(str(row.get("ld_name") or row.get("RESTRO NAME", "")))
        near_idx  = np.where(near_mask)[0]
        best = None

        for ni in near_idx:
            dist = dists[ni]
            sim  = fuzz.token_sort_ratio(name_norm, lk_names[ni]) / 100

            if dist <= 30 and sim >= 0.85:
                best = (ni, "geo_30m_fuzzy_0.85", 0.99, dist, sim)
                break
            elif dist <= 50 and name_norm == lk_names[ni]:
                best = (ni, "geo_50m_exact_name", 0.95, dist, sim)
                break
            elif dist <= 100 and sim >= 0.90:
                if best is None or dist < best[3]:
                    best = (ni, "geo_100m_fuzzy_0.90", 0.95, dist, sim)

        if best:
            ni, method, conf, dist, sim = best
            lk_row = lookup.iloc[ni]
            results.append({
                "branch_id":           row["branch_id"],
                "restaurant_type":     lk_row["restaurant_type"],
                "outlet_type":         lk_row["outlet_type"],
                "chained_outlet_type": lk_row["chained_outlet_type"],
                "match_method":        f"{method}(geo={dist:.0f}m,sim={sim:.2f})",
                "confidence":          conf,
                "matched_to":          lk_row["name"],
            })
        else:
            results.append(None)

    elapsed = time.perf_counter() - t0
    print(f"  geo matching done: {total:,} restaurants in {elapsed:.0f}s{' ' * 30}")

    rows = [r for r in results if r]
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["branch_id","restaurant_type","outlet_type","chained_outlet_type",
                 "match_method","confidence","matched_to"]
    )


# ── Build per-restaurant classification table ──────────────────────────────────

def build_classification_map(xlsx_restaurants: pd.DataFrame,
                              matched_results: pd.DataFrame,
                              lookup: pd.DataFrame) -> pd.DataFrame:
    """
    Returns a DataFrame indexed by branch_id with classification columns + meta.
    Covers all 3 tiers.
    """
    mr = matched_results[["branch_id","restaurant_type","outlet_type",
                           "chained_outlet_type","match_method","confidence"]].copy()
    mr["match_method"] = "direct_branch_id"
    mr["matched_to"]   = ""
    tier1_bids = set(mr["branch_id"].dropna().astype(int))

    # Tier 2/3/4 — run on restaurants NOT already classified
    unclassified = xlsx_restaurants[
        ~xlsx_restaurants["branch_id"].isin(tier1_bids) &
        pd.notna(xlsx_restaurants["ld_lat"]) &
        pd.notna(xlsx_restaurants["ld_lon"])
    ].copy()

    print(f"  Tier 2/3 geo+fuzzy: {len(unclassified):,} unclassified restaurants")
    geo_matches = geo_fuzzy_match(unclassified, lookup)

    # Combine
    all_class = pd.concat([mr, geo_matches], ignore_index=True)
    all_class["branch_id"] = all_class["branch_id"].astype("Int64")

    # Drop any duplicate branch_ids (Tier 1 wins)
    all_class = all_class.drop_duplicates(subset=["branch_id"], keep="first")

    print(f"\n  Total classified restaurants: {len(all_class):,}")
    print(f"  Tier 1 (direct branch_id) : {len(mr):,}")
    print(f"  Tier 2/3 (geo+fuzzy)       : {len(geo_matches):,}")
    return all_class


# ── Merge classification into dataframe ───────────────────────────────────────

CLASS_COLS = ["restaurant_type", "outlet_type", "chained_outlet_type",
              "match_method", "confidence", "matched_to"]

RENAME_MAP = {
    "restaurant_type":    "Type of Restaurants",
    "outlet_type":        "Outlet Type",
    "chained_outlet_type": "Chained Outlet Type",
}

def apply_classification(df: pd.DataFrame, class_map: pd.DataFrame) -> pd.DataFrame:
    """Left-join classification onto df on branch_id, overwrite existing cols."""
    df = df.copy()
    df["branch_id"] = pd.to_numeric(df["branch_id"], errors="coerce").astype("Int64")

    cm = class_map[["branch_id"] + CLASS_COLS].copy()
    cm["branch_id"] = cm["branch_id"].astype("Int64")

    merged = df.merge(cm, on="branch_id", how="left", suffixes=("_old", ""))

    # Overwrite display columns
    for src, dst in RENAME_MAP.items():
        if src in merged.columns:
            # Fill display col where we have new data; leave existing where we don't
            new_vals = merged[src]
            if dst in merged.columns:
                merged[dst] = new_vals.where(new_vals.notna(), merged[dst])
            else:
                merged[dst] = new_vals
            merged.drop(columns=[src], inplace=True)

    # Drop _old suffix columns
    old_cols = [c for c in merged.columns if c.endswith("_old")]
    merged.drop(columns=old_cols, inplace=True)

    return merged


# ── Excel writer ───────────────────────────────────────────────────────────────

def write_excel(path: Path, menu_df: pd.DataFrame, rest_df: pd.DataFrame):
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    ROW_LIMIT = 1_048_575

    def style_header(ws, ncols, bg, fg="FFFFFF"):
        for col in range(1, ncols + 1):
            cell = ws.cell(row=1, column=col)
            cell.font      = Font(name="Arial", bold=True, color=fg, size=9)
            cell.fill      = PatternFill("solid", start_color=bg)
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 18

    def autofit(ws, df, max_w=50):
        for i, col in enumerate(df.columns, 1):
            sample  = df[col].astype(str).head(300)
            max_len = max(len(str(col)), sample.str.len().max() if not sample.empty else 0)
            ws.column_dimensions[get_column_letter(i)].width = min(max_len + 2, max_w)

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        # Full Menu
        to_write = menu_df.head(ROW_LIMIT)
        to_write.to_excel(writer, sheet_name="Full Menu", index=False)
        ws = writer.sheets["Full Menu"]
        style_header(ws, len(to_write.columns), "1F3864")
        autofit(ws, to_write)

        if len(menu_df) > ROW_LIMIT:
            overflow = menu_df.iloc[ROW_LIMIT:]
            overflow.to_excel(writer, sheet_name="Full Menu (cont)", index=False)
            ws2 = writer.sheets["Full Menu (cont)"]
            style_header(ws2, len(overflow.columns), "1F3864")
            autofit(ws2, overflow)

        # Restaurants
        rest_df.to_excel(writer, sheet_name="Restaurants", index=False)
        ws3 = writer.sheets["Restaurants"]
        style_header(ws3, len(rest_df.columns), "2E4057")
        autofit(ws3, rest_df)

    print(f"  Excel → {path.name}  ({path.stat().st_size/1_048_576:.1f} MB)")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to talabat_full_*.xlsx")
    parser.add_argument("--format", choices=["csv","excel","both"], default="both")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.is_absolute():
        src = Path(__file__).resolve().parent / args.input
    if not src.exists():
        print(f"ERROR: {src} not found")
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("=" * 60)
    print(f"Talabat Export Enrichment — {ts}")
    print(f"Source : {src.name}")
    print("=" * 60)

    # ── Load sources ───────────────────────────────────────────────────────────
    print("\n[1] Loading classification sources...")
    matched_results = load_matched_results()
    print(f"  matched_results : {len(matched_results)} rows")
    geo_lookup      = load_type_chain_geo()

    # ── Load xlsx ──────────────────────────────────────────────────────────────
    print("\n[2] Loading xlsx export (this takes ~60s for 386K rows)...")
    t = time.perf_counter()
    menu_df = pd.read_excel(src, sheet_name="Full Menu")
    rest_df = pd.read_excel(src, sheet_name="Restaurants")
    print(f"  Full Menu  : {len(menu_df):,} rows, {menu_df['branch_id'].nunique():,} unique restaurants")
    print(f"  Restaurants: {len(rest_df):,} rows  ({time.perf_counter()-t:.0f}s)")

    # ── Build classification map ───────────────────────────────────────────────
    print("\n[3] Building classification map...")
    class_map = build_classification_map(rest_df, matched_results, geo_lookup)

    # Distribution
    print("\n  Type of Restaurants distribution (classified rows):")
    for t_val, cnt in class_map["restaurant_type"].value_counts(dropna=False).items():
        print(f"    {t_val if pd.notna(t_val) else 'NULL'}: {cnt}")

    print("\n  Outlet Type distribution:")
    for t_val, cnt in class_map["outlet_type"].value_counts(dropna=False).items():
        print(f"    {t_val if pd.notna(t_val) else 'NULL'}: {cnt}")

    # ── Enrich sheets ──────────────────────────────────────────────────────────
    print("\n[4] Applying classifications to Full Menu sheet...")
    t = time.perf_counter()
    menu_enriched = apply_classification(menu_df, class_map)

    # Re-order: put match_method + confidence + matched_to after Chained Outlet Type
    insert_after = "Chained Outlet Type"
    cols = list(menu_enriched.columns)
    for mc in ["matched_to", "confidence", "match_method"]:
        if mc in cols:
            cols.remove(mc)
            pos = cols.index(insert_after) + 1 if insert_after in cols else len(cols)
            cols.insert(pos, mc)
    menu_enriched = menu_enriched[cols]

    print(f"  Done ({time.perf_counter()-t:.1f}s)")
    print(f"  Classified menu item rows : {menu_enriched['match_method'].notna().sum():,} / {len(menu_enriched):,}")

    print("\n[5] Applying classifications to Restaurants sheet...")
    rest_enriched = apply_classification(rest_df, class_map)

    for mc in ["matched_to", "confidence", "match_method"]:
        if mc in rest_enriched.columns:
            cols_r = list(rest_enriched.columns)
            cols_r.remove(mc)
            cols_r.append(mc)
            rest_enriched = rest_enriched[cols_r]

    classified_restaurants = rest_enriched["match_method"].notna().sum()
    print(f"  Classified restaurants     : {classified_restaurants:,} / {len(rest_enriched):,}")

    # ── Save ───────────────────────────────────────────────────────────────────
    print("\n[6] Saving output files...")

    if args.format in ("csv", "both"):
        csv_path = EXPORT_DIR / f"talabat_enriched_{ts}.csv"
        menu_enriched.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"  CSV  → {csv_path.name}  ({csv_path.stat().st_size/1_048_576:.1f} MB)")

    if args.format in ("excel", "both"):
        xlsx_path = EXPORT_DIR / f"talabat_enriched_{ts}.xlsx"
        print("  Writing Excel (may take ~2 min)...")
        write_excel(xlsx_path, menu_enriched, rest_enriched)

    print("\n" + "=" * 60)
    print("Summary:")
    print(f"  Restaurants classified : {classified_restaurants:,} / {len(rest_enriched):,}")
    print(f"    Tier 1 (branch_id)   : {len(matched_results):,}")
    tier2 = classified_restaurants - len(matched_results)
    if tier2 > 0:
        print(f"    Tier 2/3 (geo+fuzzy) : {tier2}")
    print(f"  Output : {EXPORT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
