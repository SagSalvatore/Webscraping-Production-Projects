"""
process_kfc_results.py
----------------------
Cleans and exports Apify raw data for KFC UAE outlet scrapes.

Output fields (as requested):
  place_id, Name, Emirate, City, Address,
  Contact_No, Google_Maps_URL,
  Geo_Lat, Geo_Lng,
  Category, All_Categories,
  Rating, Review_Count

Outputs:
  output/KFC_UAE.csv
  output/KFC_UAE.json

Usage:
    python process_kfc_results.py                        # latest raw file
    python process_kfc_results.py --input path/to.json  # specific raw file
    python process_kfc_results.py --include-all          # keep non-KFC results too
"""

import re
import sys
import json
import argparse
import logging
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlunparse

import pandas as pd

RAW_DIR     = Path(__file__).parent / "output" / "raw"
OUTPUT_DIR  = Path(__file__).parent / "output"
OUTPUT_CSV  = OUTPUT_DIR / "KFC_UAE.csv"
OUTPUT_JSON = OUTPUT_DIR / "KFC_UAE.json"

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# ── Emirates detection ────────────────────────────────────────────────────────
# Maps address keywords -> Emirate label.
# ORDER MATTERS: more-specific city names must come BEFORE their parent emirate.
# e.g. "Al Ain, Abu Dhabi" -> must match "Al Ain" before "Abu Dhabi"
# e.g. "Khor Fakkan, Sharjah" -> must match "Khor Fakkan" before "Sharjah"
_EMIRATE_PATTERNS = [
    ("Al Ain",          "Al Ain"),          # Abu Dhabi emirate — check before "Abu Dhabi"
    ("Khor Fakkan",     "Khor Fakkan"),     # Sharjah exclave — check before "Sharjah"
    ("Kalba",           "Kalba"),           # Sharjah exclave — check before "Sharjah"
    ("Dubai",           "Dubai"),
    ("Abu Dhabi",       "Abu Dhabi"),
    ("Sharjah",         "Sharjah"),
    ("Ajman",           "Ajman"),
    ("Ras Al Khaimah",  "Ras Al Khaimah"),
    ("Fujairah",        "Fujairah"),
    ("Umm Al Quwain",   "Umm Al Quwain"),
]

_UAE_SUFFIX = re.compile(
    r',?\s*(united arab emirates|u\.a\.e\.?|uae)\s*$', re.IGNORECASE
)

def detect_emirate(address: str, city: str = "", search_query: str = "") -> str:
    """
    Determine the emirate for a KFC record.

    UAE Google Maps addresses follow the pattern:
        "[building] - [area] - [City] - [Emirate] - UAE"
    e.g. "Al Jimi Mall - Al Ain - Abu Dhabi - UAE"
         "Mirdif City Center - Mirdif - Dubai - UAE"

    Algorithm
    ---------
    1. Structured City field — exact keyword match (most reliable when populated).
    2. Address-tail RIGHT-TO-LEFT scan — strip UAE from end, split by " - ",
       scan from rightmost segment inward.  When we find a PARENT emirate (Abu
       Dhabi, Sharjah), peek one segment to the left: if it contains a sub-city
       keyword ("Al Ain", "Khor Fakkan", "Kalba") we return the sub-city instead.
       RIGHT-TO-LEFT is essential: "Oud Metha St - Al Ain - Dubai Rd - Dubai - UAE"
       correctly returns "Dubai" (rightmost hit) not "Al Ain" (road name earlier).
    3. Full-address word-boundary scan after stripping road-name noise.
    4. Search query — last resort (Google sometimes returns cross-city results).
    """
    # Layer 1 — structured city field (exact)
    if city.strip():
        city_lower = city.strip().lower()
        for keyword, label in _EMIRATE_PATTERNS:
            if keyword.lower() == city_lower:
                return label

    # Layer 2 — address-tail RIGHT-TO-LEFT scan
    addr_stripped = _UAE_SUFFIX.sub("", address).strip(", -")
    parts = [p.strip() for p in re.split(r"\s*[-,]\s*", addr_stripped) if p.strip()]
    tail = parts[-6:] if len(parts) >= 6 else parts   # last 6 segments

    _road_noise = re.compile(r"\bal ain\s+(?:rd|road|hwy|highway)\b", re.IGNORECASE)

    for i in range(len(tail) - 1, -1, -1):            # right → left
        part_lower = tail[i].lower()
        for keyword, label in _EMIRATE_PATTERNS:
            if keyword.lower() == part_lower:
                # When we find a parent emirate, scan the ENTIRE tail to the LEFT
                # to see if a sub-city appears anywhere in the address (not just
                # the immediately-previous segment).
                # e.g. "inside Al Ain Cooperative Society - Al Khalidiyya - Abu Dhabi"
                #       ^^^^^^^^^^^^^^^^^^^^^^^^^^ found here, not adjacent to Abu Dhabi
                if label in ("Abu Dhabi", "Sharjah") and i > 0:
                    # Scan address parts BEFORE the parent emirate segment,
                    # plus the search_query (catches cases like "Khorfakkan"
                    # where the address just says "Sharjah" at the end).
                    before   = " ".join(tail[:i])
                    combined = _road_noise.sub("", before) + " " + search_query
                    if label == "Abu Dhabi":
                        if re.search(r"\bal ain\b", combined, re.IGNORECASE):
                            return "Al Ain"
                    if label == "Sharjah":
                        # Match both "Khor Fakkan" (spaced) and "Khorfakkan" (merged)
                        if re.search(r"\bkhor\s*fakkan\b", combined, re.IGNORECASE):
                            return "Khor Fakkan"
                        if re.search(r"\bkalba\b", combined, re.IGNORECASE):
                            return "Kalba"
                return label

    # Layer 3 — full-text word-boundary scan, road-names stripped first
    addr_scrubbed = re.sub(
        r"\b(?:dubai\s*[-–]\s*)?al ain\s*[-–]?\s*(?:rd|road|hwy|highway)\b",
        " ", address, flags=re.IGNORECASE
    )
    for keyword, label in _EMIRATE_PATTERNS:
        if re.search(r"\b" + re.escape(keyword.lower()) + r"\b", addr_scrubbed.lower()):
            return label

    # Layer 4 — search query fallback
    sq_lower = search_query.lower()
    for keyword, label in _EMIRATE_PATTERNS:
        if keyword.lower() in sq_lower:
            return label

    return "Unknown"


# ── URL cleaner ───────────────────────────────────────────────────────────────
def clean_maps_url(url: str) -> str:
    """
    Return the cleanest possible direct Google Maps link for a place.

    Priority:
    1.  cid=  param  →  .../maps/...?cid=<id>       (stable numeric ID)
    2.  query_place_id=  →  .../maps/place/?q=place_id:<id>  (clean place URL)
    3.  Fallback: return raw URL as received
    """
    if not url:
        return ""
    p      = urlparse(url)
    params = parse_qs(p.query)

    cid = params.get("cid", [""])[0]
    if cid:
        return urlunparse((p.scheme, p.netloc, p.path, "", f"cid={cid}", ""))

    qpid = params.get("query_place_id", [""])[0]
    if qpid:
        return f"https://www.google.com/maps/place/?q=place_id:{qpid}"

    return url


# ── KFC name check ────────────────────────────────────────────────────────────
def is_kfc(name: str) -> bool:
    """Return True if the venue name looks like a KFC outlet."""
    n = name.lower()
    return "kfc" in n or "kentucky" in n or "kentucky fried" in n


# ── Core processor ────────────────────────────────────────────────────────────
def process(input_path: Path, include_all: bool = False) -> pd.DataFrame:
    logger.info(f"Loading raw data from: {input_path}")
    with open(input_path, encoding="utf-8") as f:
        raw_items = json.load(f)
    logger.info(f"Raw items: {len(raw_items):,}")

    records         = []
    skipped_closed  = 0
    skipped_no_name = 0
    skipped_non_kfc = 0

    for item in raw_items:
        # Skip permanently / temporarily closed
        if item.get("permanentlyClosed") or item.get("temporarilyClosed"):
            skipped_closed += 1
            continue

        name = (item.get("title") or "").strip()
        if not name:
            skipped_no_name += 1
            continue

        # Filter to KFC-only unless --include-all is set
        if not include_all and not is_kfc(name):
            skipped_non_kfc += 1
            logger.debug(f"  Non-KFC result skipped: {name}")
            continue

        # Determine which city this result came from (search query context)
        search_query = item.get("searchQuery", {})
        query_str    = search_query.get("term", "") if isinstance(search_query, dict) else ""
        if not query_str:
            query_str = item.get("searchString", "")

        loc     = item.get("location") or {}
        cat     = item.get("categoryName") or ""
        cats    = item.get("categories") or ([cat] if cat else [])
        address = (item.get("address") or "").strip()

        city_field = (item.get("city") or "").strip()

        records.append({
            "place_id":        item.get("placeId", ""),
            "Name":            name,
            "Emirate":         detect_emirate(address, city=city_field, search_query=query_str),
            "City":            city_field,
            "Address":         address,
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

    logger.info(f"Skipped closed         : {skipped_closed:,}")
    logger.info(f"Skipped no-name        : {skipped_no_name:,}")
    if not include_all:
        logger.info(f"Skipped non-KFC        : {skipped_non_kfc:,}")
    logger.info(f"Valid records          : {len(records):,}")

    df = pd.DataFrame(records)
    if df.empty:
        logger.warning("No records to process.")
        return df

    # ── Dedup on placeId ──────────────────────────────────────────────────────
    n_before = len(df)
    df = df[df["place_id"] != ""].copy()
    df = df.drop_duplicates(subset=["place_id"], keep="first")
    logger.info(f"Duplicates removed     : {n_before - len(df):,}")
    logger.info(f"Unique KFC outlets     : {len(df):,}")

    df = df.sort_values(["Emirate", "City", "Name"]).reset_index(drop=True)
    return df


# ── Output writers ────────────────────────────────────────────────────────────
def write_outputs(df: pd.DataFrame):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── CSV ────────────────────────────────────────────────────────────────────
    csv_df = df.copy()
    csv_df["All_Categories"] = csv_df["All_Categories"].apply(
        lambda v: " | ".join(v) if isinstance(v, list) else str(v or "")
    )
    csv_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")   # utf-8-sig = Excel-friendly BOM
    logger.info(f"CSV  saved -> {OUTPUT_CSV}  ({len(csv_df):,} rows)")

    # ── JSON ───────────────────────────────────────────────────────────────────
    records = []
    for _, r in df.iterrows():
        rating = r["Rating"]
        if rating is None or (isinstance(rating, float) and pd.isna(rating)):
            rating = None
        rc = r["Review_Count"]
        records.append({
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

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"JSON saved -> {OUTPUT_JSON}  ({len(records):,} entries)")


# ── Summary ───────────────────────────────────────────────────────────────────
def print_summary(df: pd.DataFrame):
    print("\n" + "=" * 60)
    print(f"KFC UAE — {len(df):,} unique outlets")
    print("=" * 60)

    print("\nBy Emirate:")
    print(df["Emirate"].value_counts().to_string())

    print("\nWith phone number  :", (df["Contact_No"] != "").sum())
    print("With Google URL    :", (df["Google_Maps_URL"] != "").sum())
    print("With coordinates   :", (df["Geo_Lat"] != "").sum())
    print("With rating        :", df["Rating"].notna().sum())

    avg = pd.to_numeric(df["Rating"], errors="coerce").mean()
    if pd.notna(avg):
        print(f"Avg rating         : {avg:.2f}")

    # Flag any non-KFC names that crept in
    non_kfc = df[~df["Name"].str.lower().str.contains("kfc|kentucky", na=False)]
    if not non_kfc.empty:
        print(f"\nNon-KFC names ({len(non_kfc)} — review manually):")
        for _, row in non_kfc.iterrows():
            print(f"  {row['Name']}  |  {row['Address'][:60]}")

    print("=" * 60)


# ── Entry point ───────────────────────────────────────────────────────────────
def find_latest_raw_file() -> Path:
    files = sorted(RAW_DIR.glob("dataset_*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No raw dataset files found in {RAW_DIR}")
    return files[0]


def main():
    parser = argparse.ArgumentParser(description="Process KFC UAE Apify dataset")
    parser.add_argument("--input", metavar="PATH",
                        help="Raw JSON file (default: latest in output/raw/)")
    parser.add_argument("--include-all", action="store_true",
                        help="Keep all results, not just KFC-named venues")
    args = parser.parse_args()

    input_path = Path(args.input) if args.input else find_latest_raw_file()
    df         = process(input_path, include_all=args.include_all)
    if not df.empty:
        write_outputs(df)
        print_summary(df)


if __name__ == "__main__":
    main()
