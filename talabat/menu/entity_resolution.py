"""
entity_resolution.py — Map TYPE_CHAIN_DATA.xlsx classifications to talabat_restaurants.

Matching hierarchy (stops at first hit):
  1. URL branch_id extraction   — 100% confidence (Both sheet only, covers all 133 rows)
  2. Geo ≤ 30m + fuzzy ≥ 0.85  — 99%  (fallback / verification)
  3. Geo ≤ 100m + fuzzy ≥ 0.90 — 95%
  4. Unmatched                  — flagged in output

Data sources:
  only_type sheet : 197 rows → branch_id + restaurant_type (no outlet/chain data)
  Both sheet      : 133 rows → URL-derived branch_id + restaurant_type + outlet_type + chained_outlet_type

Columns updated in talabat_restaurants:
  restaurant_type     : Full-Service Restaurants | Quick-Service Restaurants | Cafes | Bakery | Cloud Kitchen
  outlet_type         : Independent | Chain   (NULL for only_type rows without Both overlap)
  chained_outlet_type : Local Chain | MNC Chain | NULL

Outputs:
  data/entity_resolution/matched_results_YYYYMMDD_HHMMSS.csv
  data/entity_resolution/matched_results_YYYYMMDD_HHMMSS.json

Usage:
  python entity_resolution.py              # dry run — outputs files, no DB writes
  python entity_resolution.py --write-db   # also update Supabase
"""

import argparse
import json
import math
import os
import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
from dotenv import load_dotenv
from rapidfuzz import fuzz

ROOT     = Path(__file__).resolve().parent.parent
EXCEL_SRC = ROOT / "TYPE_CHAIN_DATA.xlsx"
REF_EXCEL = ROOT / "Data_menus.xlsx"
OUT_DIR   = Path(__file__).resolve().parent / "data" / "entity_resolution"
OUT_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")


# ── Type normalisation ─────────────────────────────────────────────────────────
# Standardise both sheet formats to the user's canonical category names.
TYPE_NORM = {
    "Full-Service Restaurant (FSR)":  "Full-Service Restaurants",
    "Quick-Service Restaurant (QSR)": "Quick-Service Restaurants",
    "Full-Service Restaurants":       "Full-Service Restaurants",
    "Quick-Service Restaurants":      "Quick-Service Restaurants",
    "Cafes":                          "Cafes",
    "Cafe":                           "Cafes",
    "Cloud Kitchen":                  "Cloud Kitchen",
    "Bakery":                         "Bakery",
}


# ── Utilities ──────────────────────────────────────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance in metres between two GPS points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def normalize_name(name: str) -> str:
    """Lowercase, strip diacritics, remove punctuation + common location suffixes."""
    name = str(name).lower()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(
        r"\b(branch|br|uae|dubai|abu dhabi|sharjah|restaurant|rest|cafe|kitchen|kitch)\b",
        "", name,
    )
    return re.sub(r"\s+", " ", name).strip()


def parse_geo(geo_str: str):
    """'25.2326875,55.2646875' → (25.23, 55.26)"""
    try:
        parts = str(geo_str).split(",")
        return float(parts[0].strip()), float(parts[1].strip())
    except Exception:
        return None, None


def extract_branch_id_from_url(url: str):
    """'/restaurant/748535/...' → 748535"""
    m = re.search(r"/restaurant/(\d+)/", str(url))
    return int(m.group(1)) if m else None


# ── Data loaders ───────────────────────────────────────────────────────────────

def load_reference() -> pd.DataFrame:
    """5,275 unique restaurants from Data_menus.xlsx (lat/lon + names)."""
    print(f"  Reading {REF_EXCEL.name} ...")
    df = pd.read_excel(REF_EXCEL, dtype={"branch_id": int})
    df = df.groupby("branch_id").first().reset_index()
    df = df[["branch_id", "restaurant_id", "RESTRO NAME", "ld_name", "ld_lat", "ld_lon"]].copy()
    df["ld_lat"] = pd.to_numeric(df["ld_lat"], errors="coerce")
    df["ld_lon"] = pd.to_numeric(df["ld_lon"], errors="coerce")
    df["name_norm"] = df["ld_name"].fillna(df["RESTRO NAME"]).apply(normalize_name)
    return df


def load_only_type() -> pd.DataFrame:
    df = pd.read_excel(EXCEL_SRC, sheet_name="only_type",
                       dtype={"branch_id": int, "restaurant_id": int})
    df["restaurant_type"] = df["Type of Restaurants"].map(TYPE_NORM).fillna(df["Type of Restaurants"])
    df["outlet_type"] = None
    df["chained_outlet_type"] = None
    return df[["branch_id", "RESTRO NAME", "ld_name", "restaurant_type",
               "outlet_type", "chained_outlet_type"]].copy()


def load_both() -> pd.DataFrame:
    df = pd.read_excel(EXCEL_SRC, sheet_name="Both")
    df["branch_id_url"] = df["URL"].apply(extract_branch_id_from_url)
    df["restaurant_type"] = df["Type of Restaurants"].map(TYPE_NORM).fillna(df["Type of Restaurants"])
    df["outlet_type"] = df["Outlet Type"].where(pd.notna(df["Outlet Type"]), other=None)
    df["chained_outlet_type"] = df["Chained Outlet Type"].where(pd.notna(df["Chained Outlet Type"]), other=None)
    df[["src_lat", "src_lon"]] = df["Geo Code(Lat,Long)"].apply(
        lambda g: pd.Series(parse_geo(g))
    )
    return df


# ── Matching engine ────────────────────────────────────────────────────────────

def match_both_sheet(both_df: pd.DataFrame, ref_df: pd.DataFrame) -> list[dict]:
    """
    Match each Both-sheet row to a branch_id using 3-tier hierarchy.
    Returns list of dicts — one per Both row.
    """
    ref_map = {int(r["branch_id"]): r for _, r in ref_df.iterrows()}
    results = []

    for _, row in both_df.iterrows():
        src_bid       = row["branch_id_url"]
        src_lat       = row["src_lat"]
        src_lon       = row["src_lon"]
        src_name_norm = normalize_name(str(row["RESTRO NAME"]))
        match_method  = "unmatched"
        confidence    = 0.0
        matched_bid   = None
        matched_name  = None
        verification  = ""

        # ── Method 1: URL branch_id ────────────────────────────────────────────
        if src_bid and src_bid in ref_map:
            ref_row      = ref_map[src_bid]
            matched_bid  = src_bid
            match_method = "url_branch_id"
            confidence   = 1.0
            matched_name = ref_row["ld_name"] or ref_row["RESTRO NAME"]

            # Geo + name verification of URL match
            ref_lat = ref_row["ld_lat"] if pd.notna(ref_row["ld_lat"]) else None
            ref_lon = ref_row["ld_lon"] if pd.notna(ref_row["ld_lon"]) else None
            if ref_lat and src_lat and ref_lon and src_lon:
                dist     = haversine_m(src_lat, src_lon, float(ref_lat), float(ref_lon))
                name_sim = fuzz.token_sort_ratio(src_name_norm, ref_row["name_norm"]) / 100
                if dist > 500:
                    verification = f"GEO_MISMATCH:{dist:.0f}m — review"
                elif name_sim < 0.5:
                    verification = f"NAME_MISMATCH:sim={name_sim:.2f} — review"
                else:
                    verification = f"geo={dist:.0f}m,name_sim={name_sim:.2f}"
            else:
                verification = "no_ref_geo_available"

        # ── Method 1b: URL branch_id exists but branch not in our reference ────
        elif src_bid and src_bid not in ref_map:
            matched_bid  = src_bid
            match_method = "url_branch_id_not_in_ref"
            confidence   = 0.95
            matched_name = str(row["RESTRO NAME"])
            verification = "branch_id not in Data_menus.xlsx — may need scraping"

        # ── Methods 2–3: Geo + fuzzy (fallback when no URL branch_id) ─────────
        elif src_lat and src_lon:
            candidates = []
            for _, rr in ref_df.iterrows():
                if pd.isna(rr["ld_lat"]) or pd.isna(rr["ld_lon"]):
                    continue
                dist = haversine_m(src_lat, src_lon, float(rr["ld_lat"]), float(rr["ld_lon"]))
                if dist > 300:
                    continue
                sim = fuzz.token_sort_ratio(src_name_norm, rr["name_norm"]) / 100
                candidates.append((dist, sim, rr))

            candidates.sort(key=lambda x: (x[0], -x[1]))

            for dist, sim, rr in candidates:
                if dist <= 30 and sim >= 0.85:
                    matched_bid  = int(rr["branch_id"])
                    match_method = "geo_30m_fuzzy_0.85"
                    confidence   = 0.99
                    matched_name = rr["ld_name"]
                    verification = f"geo={dist:.1f}m,name_sim={sim:.2f}"
                    break
                elif dist <= 100 and sim >= 0.90:
                    matched_bid  = int(rr["branch_id"])
                    match_method = "geo_100m_fuzzy_0.90"
                    confidence   = 0.95
                    matched_name = rr["ld_name"]
                    verification = f"geo={dist:.1f}m,name_sim={sim:.2f}"
                    break
                elif dist <= 200 and sim >= 0.95:
                    matched_bid  = int(rr["branch_id"])
                    match_method = "geo_200m_fuzzy_0.95_REVIEW"
                    confidence   = 0.80
                    matched_name = rr["ld_name"]
                    verification = f"geo={dist:.1f}m,name_sim={sim:.2f} — NEEDS_REVIEW"
                    break

        results.append({
            "branch_id":           matched_bid,
            "src_name":            str(row["RESTRO NAME"]),
            "matched_name":        matched_name,
            "restaurant_type":     row["restaurant_type"],
            "outlet_type":         row["outlet_type"],
            "chained_outlet_type": row["chained_outlet_type"],
            "match_method":        match_method,
            "confidence":          confidence,
            "verification":        verification,
        })

    return results


# ── Merge both sources ─────────────────────────────────────────────────────────

def merge_all(only_type_df: pd.DataFrame, both_results: list[dict]) -> pd.DataFrame:
    """
    Both sheet takes priority when the same branch_id appears in both sources.
    only_type-only rows get outlet_type=NULL (clearing any wrong values from previous seeding).
    """
    both_bids = {r["branch_id"] for r in both_results if r["branch_id"]}
    rows = []

    # Both-sheet results (all of them, matched or not)
    for r in both_results:
        if r["branch_id"]:
            rows.append(r)

    # only_type rows NOT already covered by Both
    for _, r in only_type_df.iterrows():
        bid = int(r["branch_id"])
        if bid not in both_bids:
            rows.append({
                "branch_id":           bid,
                "src_name":            r["RESTRO NAME"],
                "matched_name":        r["RESTRO NAME"],
                "restaurant_type":     r["restaurant_type"],
                "outlet_type":         None,   # explicitly NULL — no data available
                "chained_outlet_type": None,
                "match_method":        "direct_branch_id",
                "confidence":          1.0,
                "verification":        "only_type sheet — outlet/chain data unavailable",
            })

    return pd.DataFrame(rows).sort_values("branch_id").reset_index(drop=True)


# ── Supabase writer ────────────────────────────────────────────────────────────

def write_to_db(merged_df: pd.DataFrame):
    from supabase import create_client

    thread_local = threading.local()

    def get_client():
        if not hasattr(thread_local, "c"):
            thread_local.c = create_client(SUPABASE_URL, SUPABASE_KEY)
        return thread_local.c

    def update_row(row_dict):
        bid = row_dict["branch_id"]
        rt  = row_dict.get("restaurant_type")
        ot  = row_dict.get("outlet_type")
        cot = row_dict.get("chained_outlet_type")

        payload = {
            "restaurant_type":     rt if rt and str(rt) not in ("nan", "None") else None,
            "outlet_type":         ot if ot and str(ot) not in ("nan", "None") else None,
            "chained_outlet_type": cot if cot and str(cot) not in ("nan", "None") else None,
        }
        try:
            get_client().table("talabat_restaurants").update(payload).eq("branch_id", int(bid)).execute()
            return True, bid
        except Exception as exc:
            return False, f"{bid}: {exc}"

    print(f"\nWriting {len(merged_df):,} rows to Supabase (20 threads)...")
    t0 = time.perf_counter()
    ok = err = 0
    rows = merged_df.to_dict("records")

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(update_row, r) for r in rows]
        for i, f in enumerate(as_completed(futures), 1):
            success, info = f.result()
            if success:
                ok += 1
            else:
                err += 1
                print(f"\n  WARN {info}")
            if i % 50 == 0 or i == len(rows):
                print(f"  [{i}/{len(rows)}] {i/len(rows)*100:.0f}%", end="\r")

    print(f"\nDone. {ok} updated, {err} errors  ({time.perf_counter()-t0:.0f}s)")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Entity resolution: TYPE_CHAIN_DATA → talabat_restaurants")
    parser.add_argument("--write-db", action="store_true", help="Push results to Supabase")
    args = parser.parse_args()

    print("=" * 60)
    print("Talabat Entity Resolution")
    print("=" * 60)

    # Load data
    print("\n[1] Loading reference data...")
    ref_df = load_reference()
    print(f"    {len(ref_df):,} reference restaurants")

    print("\n[2] Loading TYPE_CHAIN_DATA.xlsx...")
    only_type_df = load_only_type()
    both_df      = load_both()
    print(f"    only_type sheet : {len(only_type_df):,} rows")
    print(f"    Both sheet      : {len(both_df):,} rows")
    print(f"    Both — URLs with branch_id: {both_df['branch_id_url'].notna().sum()}/{len(both_df)}")

    # Match
    print("\n[3] Matching Both sheet...")
    both_results = match_both_sheet(both_df, ref_df)

    # Report
    in_ref     = sum(1 for r in both_results if r["match_method"] == "url_branch_id")
    not_in_ref = sum(1 for r in both_results if r["match_method"] == "url_branch_id_not_in_ref")
    geo_match  = sum(1 for r in both_results if r["match_method"].startswith("geo_"))
    unmatched  = sum(1 for r in both_results if r["match_method"] == "unmatched")

    print(f"    url_branch_id (in ref)    : {in_ref}")
    print(f"    url_branch_id (not in ref): {not_in_ref}")
    print(f"    geo+fuzzy fallback        : {geo_match}")
    print(f"    unmatched                 : {unmatched}")

    # Flag verification issues
    issues = [r for r in both_results if "MISMATCH" in r.get("verification", "") or "REVIEW" in r.get("verification", "")]
    if issues:
        print(f"\n    FLAGGED ({len(issues)} rows):")
        for r in issues:
            print(f"      branch_id={r['branch_id']} | {r['src_name'][:40]} | {r['verification']}")

    # Merge
    print("\n[4] Merging both sources...")
    merged_df = merge_all(only_type_df, both_results)

    only_type_bids = set(only_type_df["branch_id"].astype(int))
    both_bids      = {r["branch_id"] for r in both_results if r["branch_id"]}
    overlap        = only_type_bids & both_bids

    print(f"    Total unique branch_ids : {len(merged_df):,}")
    print(f"    Overlap (Both wins)     : {len(overlap)}")
    print(f"    only_type-only          : {len(only_type_bids - both_bids)}")
    print(f"    Both-only               : {len(both_bids - only_type_bids)}")

    # Distributions
    print("\n[5] Distributions:")
    print("\n  restaurant_type:")
    for t, c in merged_df["restaurant_type"].value_counts().items():
        print(f"    {t}: {c}")

    print("\n  outlet_type:")
    for t, c in merged_df["outlet_type"].value_counts(dropna=False).items():
        print(f"    {t if pd.notna(t) else 'NULL'}: {c}")

    print("\n  chained_outlet_type:")
    for t, c in merged_df["chained_outlet_type"].value_counts(dropna=False).items():
        print(f"    {t if pd.notna(t) else 'NULL'}: {c}")

    # Save outputs
    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path  = OUT_DIR / f"matched_results_{ts}.csv"
    json_path = OUT_DIR / f"matched_results_{ts}.json"

    merged_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    records = merged_df.copy()
    for col in records.select_dtypes(include=["float64"]).columns:
        records[col] = records[col].where(pd.notna(records[col]), other=None)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(records.to_dict("records"), fh, indent=2, ensure_ascii=False, default=str)

    print(f"\n[6] Output files:")
    print(f"    CSV  → {csv_path}")
    print(f"    JSON → {json_path}")

    # DB write
    if args.write_db:
        if not SUPABASE_URL or not SUPABASE_KEY:
            print("\nERROR: SUPABASE_URL + SUPABASE_ANON_KEY must be in talabat/.env")
        else:
            write_to_db(merged_df)
    else:
        print("\nDry run complete — add --write-db to push to Supabase.")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
