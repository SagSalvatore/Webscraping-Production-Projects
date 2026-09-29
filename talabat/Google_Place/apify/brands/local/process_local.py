"""
process_local.py
----------------
Processes raw Apify data for local UAE restaurant brands.

Key difference from process_brand.py:
  - Each search query is a DIFFERENT brand (not one brand searched everywhere)
  - Results are grouped by brand name (from query_metadata)
  - ONLY brands with 2+ unique locations in UAE are kept in output
  - Single-location restaurants are filtered out entirely

Output columns:
  Brand_Name, place_id, Name, Emirate, City, Address,
  Contact_No, Google_Maps_URL, Geo_Lat, Geo_Lng,
  Category, All_Categories, Rating, Review_Count

Output files:
  brands/local/output/LOCAL_UAE.csv
  brands/local/output/LOCAL_UAE.json
  brands/local/output/LOCAL_UAE_SUMMARY.csv   (one row per brand: name + location count)

Usage:
    python brands/local/process_local.py                        # latest raw file
    python brands/local/process_local.py --input path/to.json   # specific raw
    python brands/local/process_local.py --min-locations 3      # keep 3+ only
"""

import re
import sys
import json
import argparse
import logging
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlunparse
from collections import defaultdict

import pandas as pd

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

BRANDS_DIR = Path(__file__).parent.parent   # apify/brands/
LOCAL_DIR  = Path(__file__).parent          # apify/brands/local/
OUTPUT_DIR = LOCAL_DIR / "output"

# ── Brand name matching ───────────────────────────────────────────────────────
# Words that carry no discriminating power on their own — not enough to confirm
# an outlet belongs to a specific brand.
_STOP = {
    "n", "and", "or", "the", "of", "in", "&", "a", "by", "for", "to", "at",
    "on", "an", "w", "de", "le", "la", "el", "llc", "ltd", "uae", "co",
    "corp", "inc", "est",
}


def _norm(s: str) -> str:
    """
    Normalise a name for comparison:
      - strip Arabic / non-Latin script
      - lowercase
      - replace all non-alphanumeric characters with spaces
      - collapse whitespace
    """
    # Remove Arabic and common non-Latin Unicode blocks
    s = re.sub(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]+", " ", s)
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)          # keep alphanumeric + spaces
    return re.sub(r"\s+", " ", s).strip()


def _core(brand: str) -> list[str]:
    """Return the significant word tokens of a brand name (stop-words removed)."""
    tokens = [t for t in _norm(brand).split() if t not in _STOP and len(t) > 1]
    # Deduplicate while preserving order (e.g. "ZAM ZAM" → ["zam"])
    seen, result = set(), []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def name_matches_brand(outlet_name: str, brand_name: str) -> bool:
    """
    Return True only when the outlet name is a genuine match for the brand.

    Two-rule strategy (both case-insensitive, Arabic stripped):

    Rule 1 — Substring:
        The full normalised brand name must appear inside the normalised outlet
        name (or vice versa for very short brand names).
        Catches:  "Bakery Bites" → "Bakery Bites Cafe"   ✓
                  "Bakery Bites" → "Bakery bites JVC"     ✓
        Rejects:  "Bakery Bites" → "Quick Bite Bakery"    ✗ (no substring)

    Rule 2 — Core token match:
        ALL significant tokens (stop-words removed) of the brand must appear
        individually in the outlet name tokens.
        For 4+ core tokens, one miss is allowed.
        Catches:  "Bake N More Factory" → "Bake N More Café & Factory"  ✓
                  (core tokens: bake, more, factory — all present)
        Rejects:  "Bake N More Factory" → "Bake Home pastries sweets"   ✗
                  (only "bake" matches; "more" and "factory" absent)
    """
    brand_n  = _norm(brand_name)
    outlet_n = _norm(outlet_name)

    # If the outlet has no readable (Latin) text after normalisation, skip it
    if not outlet_n or len(outlet_n) < 2:
        return False

    # Rule 1 — direct substring
    if brand_n in outlet_n:
        return True

    # For very short brand names (≤ 5 chars) also check the reverse
    if len(brand_n) <= 5 and outlet_n in brand_n:
        return True

    # Rule 2 — core token matching
    core = _core(brand_name)
    if not core:
        # No significant tokens — fall back to first 6 chars of brand substring
        return brand_n[:6] in outlet_n

    outlet_tokens = set(outlet_n.split())
    matched = sum(1 for t in core if t in outlet_tokens)
    n       = len(core)

    if n <= 3:
        return matched == n          # every core token must appear
    else:
        return matched >= n - 1      # allow 1 miss for 4+ core tokens


# -- Emirates detection -------------------------------------------------------
_EMIRATE_PATTERNS = [
    ("Al Ain",         "Al Ain"),
    ("Khor Fakkan",    "Khor Fakkan"),
    ("Kalba",          "Kalba"),
    ("Dubai",          "Dubai"),
    ("Abu Dhabi",      "Abu Dhabi"),
    ("Sharjah",        "Sharjah"),
    ("Ajman",          "Ajman"),
    ("Ras Al Khaimah", "Ras Al Khaimah"),
    ("Fujairah",       "Fujairah"),
    ("Umm Al Quwain",  "Umm Al Quwain"),
]
_UAE_SUFFIX = re.compile(r",?\s*(united arab emirates|u\.a\.e\.?|uae)\s*$", re.IGNORECASE)
_ROAD_NOISE = re.compile(r"\bal ain\s+(?:rd|road|hwy|highway)\b", re.IGNORECASE)


def emirate_from_address(address: str) -> str:
    stripped = _UAE_SUFFIX.sub("", address).strip(", -")
    parts    = [p.strip() for p in re.split(r"\s*[-,]\s*", stripped) if p.strip()]
    tail     = parts[-6:] if len(parts) >= 6 else parts

    for i in range(len(tail) - 1, -1, -1):
        for keyword, label in _EMIRATE_PATTERNS:
            if keyword.lower() == tail[i].lower():
                if label in ("Abu Dhabi", "Sharjah") and i > 0:
                    before   = " ".join(tail[:i])
                    scrubbed = _ROAD_NOISE.sub("", before)
                    if label == "Abu Dhabi" and re.search(r"\bal ain\b", scrubbed, re.IGNORECASE):
                        return "Al Ain"
                    if label == "Sharjah":
                        if re.search(r"\bkhor\s*fakkan\b", scrubbed, re.IGNORECASE):
                            return "Khor Fakkan"
                        if re.search(r"\bkalba\b", scrubbed, re.IGNORECASE):
                            return "Kalba"
                return label

    scrubbed = _ROAD_NOISE.sub(" ", address)
    for keyword, label in _EMIRATE_PATTERNS:
        if re.search(r"\b" + re.escape(keyword.lower()) + r"\b", scrubbed.lower()):
            return label
    return "Unknown"


def clean_maps_url(url: str) -> str:
    if not url:
        return ""
    p      = urlparse(url)
    params = parse_qs(p.query)
    cid    = params.get("cid", [""])[0]
    if cid:
        return urlunparse((p.scheme, p.netloc, p.path, "", f"cid={cid}", ""))
    qpid = params.get("query_place_id", [""])[0]
    if qpid:
        return f"https://www.google.com/maps/place/?q=place_id:{qpid}"
    return url


# -- Core pipeline ------------------------------------------------------------

def load_query_brand_map(local_dir: Path = LOCAL_DIR) -> dict:
    """Return {query_lower: brand_name} from input.json query_metadata."""
    input_file = local_dir / "input.json"
    if not input_file.exists():
        logger.warning("input.json not found — brand name lookup unavailable")
        return {}
    with open(input_file, encoding="utf-8") as f:
        cfg = json.load(f)
    result = {}
    for m in cfg.get("query_metadata", []):
        key = m.get("query", "").strip().lower().strip('"\'')
        if key:
            result[key] = m.get("brand_name", "")
    logger.info(f"Query->brand map  : {len(result)} entries")
    return result


def find_latest_raw() -> Path:
    raw_dir = LOCAL_DIR / "output" / "raw"
    files   = sorted(raw_dir.glob("dataset_*.json"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No raw files in {raw_dir}")
    return files[0]


def process(input_path: Path, query_brand_map: dict,
            min_locations: int = 2) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (detail_df, summary_df).
    detail_df  — one row per outlet, only for brands with >= min_locations
    summary_df — one row per brand, with location count
    """
    logger.info(f"Loading raw data from: {input_path}")
    with open(input_path, encoding="utf-8") as f:
        raw_items = json.load(f)
    logger.info(f"Raw items             : {len(raw_items):,}")

    records               = []
    skipped_closed        = 0
    skipped_no_name       = 0
    skipped_no_query      = 0
    skipped_name_mismatch = 0   # outlet name doesn't match the brand we searched

    for item in raw_items:
        if item.get("permanentlyClosed") or item.get("temporarilyClosed"):
            skipped_closed += 1
            continue

        name = (item.get("title") or "").strip()
        if not name:
            skipped_no_name += 1
            continue

        # Extract search query
        sq = item.get("searchQuery", {})
        query_str = sq.get("term", "") if isinstance(sq, dict) else ""
        if not query_str:
            query_str = item.get("searchString", "").strip().strip('"\'')
        query_str = query_str.strip().strip('"\'')

        # Map query → brand name
        brand_name = query_brand_map.get(query_str.lower(), "")
        if not brand_name:
            # Fallback: strip " UAE" from the query itself
            brand_name = re.sub(r"\s+UAE\s*$", "", query_str, flags=re.IGNORECASE).strip()
        if not brand_name:
            skipped_no_query += 1
            continue

        # ── Name matching — keep only outlets that genuinely belong to the brand ──
        # Rejects false positives like Google returning unrelated venues that
        # happen to share a word with the brand search (e.g. searching
        # "Bakery Bites" and getting "Quick Bite Bakery LLC").
        if not name_matches_brand(name, brand_name):
            skipped_name_mismatch += 1
            continue

        loc  = item.get("location") or {}
        cat  = item.get("categoryName") or ""
        cats = item.get("categories") or ([cat] if cat else [])
        addr = (item.get("address") or "").strip()

        records.append({
            "Brand_Name":      brand_name,
            "place_id":        item.get("placeId", ""),
            "Name":            name,
            "Emirate":         emirate_from_address(addr),
            "City":            (item.get("city")         or "").strip(),
            "Address":         addr,
            "Street":          (item.get("street")       or "").strip(),
            "Neighborhood":    (item.get("neighborhood") or "").strip(),
            "Contact_No":      (item.get("phone")        or "").strip(),
            "Google_Maps_URL": clean_maps_url(item.get("url", "")),
            "Geo_Lat":         loc.get("lat", ""),
            "Geo_Lng":         loc.get("lng", ""),
            "Category":        cat,
            "All_Categories":  cats,
            "Rating":          item.get("totalScore"),
            "Review_Count":    item.get("reviewsCount", 0),
            "Search_Query":    query_str,
        })

    logger.info(f"Skipped closed        : {skipped_closed:,}")
    logger.info(f"Skipped no-name       : {skipped_no_name:,}")
    logger.info(f"Skipped no-query      : {skipped_no_query:,}")
    logger.info(f"Skipped name mismatch : {skipped_name_mismatch:,}  (false positives filtered out)")
    logger.info(f"Total collected       : {len(records):,}")

    if not records:
        return pd.DataFrame(), pd.DataFrame()

    df = pd.DataFrame(records)

    # Dedup within each brand: same place_id
    n_before = len(df)
    df = df[df["place_id"] != ""].copy()
    df = df.drop_duplicates(subset=["place_id"], keep="first")
    logger.info(f"Duplicates removed    : {n_before - len(df):,}")

    # Count locations per brand
    brand_counts = df.groupby("Brand_Name")["place_id"].nunique().rename("Location_Count")
    df = df.merge(brand_counts, on="Brand_Name")

    # Summary before filtering
    total_brands      = df["Brand_Name"].nunique()
    multi_loc_brands  = brand_counts[brand_counts >= min_locations]
    logger.info(f"Total unique brands   : {total_brands:,}")
    logger.info(f"Brands with {min_locations}+ locations : {len(multi_loc_brands):,}")

    # Filter to min_locations
    df = df[df["Location_Count"] >= min_locations].copy()
    df = df.drop(columns=["Location_Count"])
    df = df.sort_values(["Brand_Name", "Emirate", "Name"]).reset_index(drop=True)
    logger.info(f"Outlets in output     : {len(df):,}")

    # Summary df
    summary = (
        brand_counts[brand_counts >= min_locations]
        .reset_index()
        .rename(columns={"place_id": "Location_Count"})
        .sort_values("Location_Count", ascending=False)
        .reset_index(drop=True)
    )

    return df, summary


def write_outputs(df: pd.DataFrame, summary: pd.DataFrame) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_path     = OUTPUT_DIR / "LOCAL_UAE.csv"
    json_path    = OUTPUT_DIR / "LOCAL_UAE.json"
    summary_path = OUTPUT_DIR / "LOCAL_UAE_SUMMARY.csv"

    # Summary CSV
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    logger.info(f"Summary CSV -> {summary_path}  ({len(summary)} brands)")

    # Detail CSV
    csv_df = df.copy()
    csv_df["All_Categories"] = csv_df["All_Categories"].apply(
        lambda v: " | ".join(v) if isinstance(v, list) else str(v or "")
    )
    csv_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info(f"Detail  CSV -> {csv_path}  ({len(csv_df)} rows)")

    # Detail JSON
    records = []
    for _, r in df.iterrows():
        rating = r["Rating"]
        if rating is None or (isinstance(rating, float) and pd.isna(rating)):
            rating = None
        rc = r["Review_Count"]
        records.append({
            "Brand_Name":      r["Brand_Name"],
            "place_id":        r["place_id"],
            "Name":            r["Name"],
            "Emirate":         r["Emirate"],
            "City":            r["City"],
            "Address":         r["Address"],
            "Street":          r["Street"],
            "Neighborhood":    r["Neighborhood"],
            "Contact_No":      r["Contact_No"],
            "Google_Maps_URL": r["Google_Maps_URL"],
            "Geo_Coordinates": {"lat": r["Geo_Lat"], "lng": r["Geo_Lng"]},
            "Category":        r["Category"],
            "All_Categories":  r["All_Categories"] if isinstance(r["All_Categories"], list) else [],
            "Rating":          rating,
            "Review_Count":    0 if pd.isna(rc) else int(rc),
            "Search_Query":    r["Search_Query"],
        })
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"Detail JSON -> {json_path}  ({len(records)} entries)")


def print_summary(df: pd.DataFrame, summary: pd.DataFrame, min_locs: int) -> None:
    total_brands  = len(summary)
    total_outlets = len(df)

    print("\n" + "=" * 62)
    print(f"UAE Local Restaurants — Multi-Location ({min_locs}+ outlets)")
    print("=" * 62)
    print(f"\nBrands with {min_locs}+ locations: {total_brands}")
    print(f"Total outlet records  : {total_outlets}")

    print("\nTop brands by location count:")
    for _, row in summary.head(20).iterrows():
        print(f"  {row['Brand_Name']:<45}  {int(row['Location_Count'])} locations")

    print("\nBy Emirate (all outlets):")
    if not df.empty:
        print(df["Emirate"].value_counts().to_string())

    print("=" * 62)


def main():
    parser = argparse.ArgumentParser(
        description="Process local brand Apify data — multi-location filter"
    )
    parser.add_argument("--input", metavar="PATH",
                        help="Raw JSON (default: latest in brands/local/output/raw/)")
    parser.add_argument("--min-locations", type=int, default=2,
                        help="Minimum locations to keep a brand (default: 2)")
    args = parser.parse_args()

    input_path     = Path(args.input) if args.input else find_latest_raw()
    query_brand_map = load_query_brand_map()

    df, summary = process(input_path, query_brand_map, args.min_locations)

    if df.empty:
        logger.error("No multi-location brands found in this dataset.")
        sys.exit(1)

    write_outputs(df, summary)
    print_summary(df, summary, args.min_locations)


if __name__ == "__main__":
    main()
