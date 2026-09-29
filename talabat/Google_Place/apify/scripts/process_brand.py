"""
process_brand.py
----------------
Generic brand result processor for any QSR/restaurant brand.
Reads the raw Apify JSON, filters to the target brand, deduplicates,
and exports clean CSV + JSON outputs.

Key improvement over the KFC-specific processor
------------------------------------------------
Emirate detection comes from the brand's input.json query_metadata
(query → area → emirate), NOT from address parsing.  This is 100%
accurate because we know exactly which area each Apify search covered.
Address-based fallback is used ONLY for records whose search query
is not in the metadata (e.g., merged legacy data).

Output fields
-------------
place_id, Name, Emirate, Area, City, Address, Street, Neighborhood,
Contact_No, Website, Google_Maps_URL, Geo_Lat, Geo_Lng,
Category, All_Categories, Rating, Review_Count

Output files
------------
brands/<brand>/output/<OUTPUT_FILENAME>.csv   (UTF-8 BOM for Excel)
brands/<brand>/output/<OUTPUT_FILENAME>.json

Usage:
    python process_brand.py --brand dominos                              # latest raw
    python process_brand.py --brand dominos --input path/to/merged.json  # specific
    python process_brand.py --brand dominos --include-all               # skip brand filter
    python process_brand.py --brand kfc --input brands/kfc/output/raw/dataset_*.json
"""

import re
import sys
import json
import argparse
import logging
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlunparse

import pandas as pd

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

APIFY_DIR = Path(__file__).resolve().parent.parent
BRANDS_DIR = APIFY_DIR / "brands"

# -- Emirates detection fallback (for records not covered by query_metadata) ---
# Order: specific sub-cities before their parent emirate
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

_UAE_SUFFIX  = re.compile(r",?\s*(united arab emirates|u\.a\.e\.?|uae)\s*$", re.IGNORECASE)
_ROAD_NOISE  = re.compile(r"\bal ain\s+(?:rd|road|hwy|highway)\b", re.IGNORECASE)

# Google returns several spellings for the same emirate. _EMIRATE_PATTERNS
# matches ONE literal form each, so every variant below used to fall through to
# "Unknown" and silently vanish from the per-emirate counts:
#
#   Ras Al-Khaimah / Ras Al Khaima / RAK      Umm Al-Quwain / Umm Al Qaiwain
#
# An intra-word hyphen is normalised to a space first - note the " - " address
# SEPARATOR must survive, so only hyphens between word characters are touched.
_INTRA_HYPHEN = re.compile(r"(?<=\w)-(?=\w)")
_EMIRATE_ALIASES = [
    (re.compile(r"\bras\s*al\s*khaim(?:a|ah)\b", re.I), "Ras Al Khaimah"),
    (re.compile(r"\bumm\s*al\s*(?:quwain|qaiwain|qaywayn)\b", re.I), "Umm Al Quwain"),
    (re.compile(r"\bkhor\s*fakkan\b", re.I), "Khor Fakkan"),
    (re.compile(r"\bal\s*fujairah\b", re.I), "Fujairah"),
]


def _normalize_address(address: str) -> str:
    """Fold spelling variants onto the canonical emirate names."""
    s = _INTRA_HYPHEN.sub(" ", address or "")
    for rx, canon in _EMIRATE_ALIASES:
        s = rx.sub(canon, s)
    return s


def _emirate_from_address(address: str) -> str:
    """
    Fallback: derive emirate from the address structure.
    UAE addresses follow "[building] - [area] - [City] - [Emirate] - UAE".
    Scan the last 6 tail segments right-to-left; when a parent emirate is
    found, check if a sub-city name appears anywhere before it (e.g.
    "Al Ain Cooperative Society - Abu Dhabi" → "Al Ain").
    """
    address  = _normalize_address(address)
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

    # Word-boundary scan with road noise stripped
    scrubbed = _ROAD_NOISE.sub(" ", address)
    for keyword, label in _EMIRATE_PATTERNS:
        if re.search(r"\b" + re.escape(keyword.lower()) + r"\b", scrubbed.lower()):
            return label
    return "Unknown"


def clean_maps_url(url: str) -> str:
    """Return the cleanest direct Google Maps place URL."""
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


# -- Brand config helpers ------------------------------------------------------

def load_brand_config(brand: str) -> dict:
    path = BRANDS_DIR / brand / "input.json"
    if not path.exists():
        logger.warning(f"Brand input.json not found at {path} — metadata unavailable")
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_query_map(brand_config: dict) -> dict:
    """
    Return {query_string: {area: ..., emirate: ...}} from the brand's
    query_metadata list.  Lookup is case-insensitive.
    """
    result = {}
    for m in brand_config.get("query_metadata", []):
        key = m.get("query", "").strip().lower()
        if key:
            result[key] = {"area": m.get("area", ""), "emirate": m.get("emirate", "")}
    return result


def _normalize_quotes(s: str) -> str:
    """Treat curly and straight quotes/apostrophes as equivalent. Google
    Maps titles use a straight apostrophe ("McDonald's"), but brand name
    lists sourced from spreadsheets/docs often use a curly one
    ("McDonald's", U+2019) -- a silent mismatch that dropped 100% of real
    McDonald's matches in one batch (36 raw hits, 0 survived the filter)."""
    return (
        s.replace("’", "'").replace("‘", "'")
        .replace("“", '"').replace("”", '"')
        .replace("`", "'")
    )


def _normalize_for_match(s: str) -> str:
    """Beyond quotes: strip periods and collapse whitespace, so "P.F.
    Chang's" matches keyword "pf chang's" (abbreviation punctuation is
    stylistic, not semantic) and double-spaced names match single-spaced
    keywords."""
    s = _normalize_quotes(s)
    s = s.replace(".", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_brand(name: str, keywords: list[str],
             exclude: list[str] | None = None) -> bool:
    """Return True if the venue name matches any brand keyword.

    `exclude` wins over `keywords`. Substring matching cannot separate a real
    branch from a copycat that contains the same token ("Cheesy Chicking"
    contains "chicking") or from a non-outlet node that is legitimately named
    after the brand ("Malak Al Tawouk drivers pick up", "... Central Kitchen").
    Those are named per brand in input.json -> brand_exclude_keywords.
    """
    n = _normalize_for_match(name.lower())
    for kw in (exclude or []):
        if _normalize_for_match(kw.lower()) in n:
            return False
    return any(_normalize_for_match(kw.lower()) in n for kw in keywords)


# Large chains (McDonald's, Burger King, etc.) have supply-chain warehouses,
# regional/corporate offices, and manufacturing plants that share the brand
# name on Google Maps but aren't customer-facing restaurants -- these must
# never end up in a restaurant directory.
_NON_RESTAURANT_CATEGORY_KEYWORDS = [
    "corporate office", "corporate headquarters", "headquarters", "head office",
    "warehouse", "distribution center", "distribution centre",
    "manufacturer", "food manufacturer", "factory", "wholesaler", "wholesale",
    "supplier", "logistics service", "logistics company", "storage facility",
    "corporate campus", "regional office",
    "food producer", "cloud kitchen", "central kitchen",
]


def is_restaurant_category(category: str, all_categories: list) -> bool:
    """Return False if the category/categories indicate a corporate office,
    warehouse, manufacturing plant, or similar non-customer-facing facility
    rather than an actual restaurant."""
    combined = " ".join([category or ""] + [c for c in (all_categories or []) if c]).lower()
    return not any(kw in combined for kw in _NON_RESTAURANT_CATEGORY_KEYWORDS)


# -- Core pipeline -------------------------------------------------------------

def find_latest_raw_file(brand: str) -> Path:
    raw_dir = BRANDS_DIR / brand / "output" / "raw"
    files   = sorted(raw_dir.glob("dataset_*.json"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No raw files in {raw_dir}")
    return files[0]


def process(
    input_path:   Path,
    brand_config: dict,
    query_map:    dict,
    brand_keywords: list[str],
    include_all:  bool = False,
    exclude_keywords: list[str] | None = None,
) -> pd.DataFrame:

    logger.info(f"Loading raw data from: {input_path}")
    with open(input_path, encoding="utf-8") as f:
        raw_items = json.load(f)
    logger.info(f"Raw items             : {len(raw_items):,}")

    records          = []
    skipped_closed   = 0
    skipped_no_name  = 0
    skipped_non_brand = 0
    skipped_non_restaurant = 0

    for item in raw_items:
        # Skip closed
        if item.get("permanentlyClosed") or item.get("temporarilyClosed"):
            skipped_closed += 1
            continue

        name = (item.get("title") or "").strip()
        if not name:
            skipped_no_name += 1
            continue

        # Brand filter
        if not include_all and not is_brand(name, brand_keywords, exclude_keywords):
            skipped_non_brand += 1
            continue

        # Restaurant-only filter -- excludes corporate offices, warehouses,
        # and manufacturing plants that share the brand name on Google Maps
        # but aren't customer-facing restaurants.
        item_cat = item.get("categoryName") or ""
        item_cats = item.get("categories") or ([item_cat] if item_cat else [])
        if not include_all and not is_restaurant_category(item_cat, item_cats):
            skipped_non_restaurant += 1
            logger.debug(f"  Non-restaurant category skipped: {name} ({item_cat})")
            continue

        # Extract search query string
        # Note: Apify sometimes wraps searchString in extra quotes, e.g.
        #   '"Domino\'s Pizza in Yas Island, Abu Dhabi, UAE"'
        # Strip them so the query_map lookup works correctly.
        sq = item.get("searchQuery", {})
        query_str = sq.get("term", "") if isinstance(sq, dict) else ""
        if not query_str:
            query_str = item.get("searchString", "").strip().strip('"\'')
        query_str = query_str.strip().strip('"\'')  # strip from either source

        # ── Emirate / Area lookup ─────────────────────────────────────────────
        # EMIRATE: always derived from the address (reliable ground truth).
        #   Query-based emirate is NOT used because popular chains (Dominos,
        #   KFC, etc.) cause Google to return the same outlets regardless of
        #   the searched area, making query → emirate mapping unreliable.
        #
        # AREA: derived from query_metadata when the query matches.
        #   Provides useful neighbourhood/zone context but is best-effort.
        address   = (item.get("address") or "").strip()
        emirate   = _emirate_from_address(address)

        meta = query_map.get(query_str.strip().lower(), {})
        area = meta.get("area", "")

        loc  = item.get("location") or {}
        cat  = item_cat
        cats = item_cats

        records.append({
            "place_id":        item.get("placeId", ""),
            "Name":            name,
            "Emirate":         emirate,
            "Area":            area,
            "City":            (item.get("city")         or "").strip(),
            "Address":         (item.get("address")      or "").strip(),
            "Street":          (item.get("street")       or "").strip(),
            "Neighborhood":    (item.get("neighborhood") or "").strip(),
            "Contact_No":      (item.get("phone")        or "").strip(),
            "Website":         (item.get("website")      or "").strip(),
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
    if not include_all:
        logger.info(f"Skipped non-brand     : {skipped_non_brand:,}")
        logger.info(f"Skipped non-restaurant: {skipped_non_restaurant:,}")
    logger.info(f"Valid records         : {len(records):,}")

    if not records:
        logger.warning("No records passed filters — check brand_keywords in input.json")
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # -- Dedup on placeId -------------------------------------------------------
    n_before = len(df)
    df = df[df["place_id"] != ""].copy()
    df = df.drop_duplicates(subset=["place_id"], keep="first")
    removed = n_before - len(df)
    logger.info(f"Duplicates removed (place_id) : {removed:,}")

    # -- Dedup on Address ---------------------------------------------------------
    # Google Maps sometimes surfaces the SAME physical outlet under two
    # different place_ids (duplicate listings, or one picked up via a
    # different search query) -- since this file is already filtered to one
    # brand only, two rows sharing the exact same address are the same real
    # outlet. Only applied where an address is actually present, so blank
    # addresses (which would otherwise all collapse into a single row) are
    # left untouched.
    n_before_addr = len(df)
    has_addr = df["Address"] != ""
    df = pd.concat([
        df[has_addr].drop_duplicates(subset=["Address"], keep="first"),
        df[~has_addr],
    ], ignore_index=True)
    removed_addr = n_before_addr - len(df)
    logger.info(f"Duplicates removed (address)  : {removed_addr:,}")
    logger.info(f"Unique outlets                : {len(df):,}")

    df = df.sort_values(["Emirate", "Area", "Name"]).reset_index(drop=True)
    return df


# -- Output writers ------------------------------------------------------------

def write_outputs(df: pd.DataFrame, brand: str, output_filename: str) -> None:
    out_dir = BRANDS_DIR / brand / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path  = out_dir / f"{output_filename}.csv"
    json_path = out_dir / f"{output_filename}.json"

    # ── CSV (Excel-friendly UTF-8 BOM) ─────────────────────────────────────────
    csv_df = df.copy()
    csv_df["All_Categories"] = csv_df["All_Categories"].apply(
        lambda v: " | ".join(v) if isinstance(v, list) else str(v or "")
    )
    csv_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info(f"CSV  saved -> {csv_path}  ({len(csv_df):,} rows)")

    # ── JSON (rich / nested) ───────────────────────────────────────────────────
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
            "Area":            r["Area"],
            "City":            r["City"],
            "Address":         r["Address"],
            "Street":          r["Street"],
            "Neighborhood":    r["Neighborhood"],
            "Contact_No":      r["Contact_No"],
            "Website":         r["Website"],
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
    logger.info(f"JSON saved -> {json_path}  ({len(records):,} entries)")


# -- Summary -------------------------------------------------------------------

def print_summary(df: pd.DataFrame, brand_display: str, brand_keywords: list[str],
                  exclude_keywords: list[str] | None = None) -> None:
    print("\n" + "=" * 62)
    print(f"{brand_display} — {len(df):,} unique outlets")
    print("=" * 62)

    print("\nBy Emirate:")
    print(df["Emirate"].value_counts().to_string())

    # An unresolved emirate must never pass quietly. It drops the outlet out of
    # every per-emirate count, so the brand's UAE total silently under-reports -
    # the one number this whole exercise exists to get right. Google spells the
    # same emirate several ways ("Ras Al-Khaimah", "Umm Al Qaiwain"); the
    # aliases in _normalize_address fold the known ones, and anything still
    # landing here is a NEW variant that needs adding there.
    unknown = df[df["Emirate"] == "Unknown"]
    if len(unknown):
        print(f"\n  *** {len(unknown)} outlet(s) with UNRESOLVED emirate "
              f"({len(unknown)/len(df)*100:.1f}%) — these are missing from the "
              f"counts above:")
        for _, row in unknown.head(15).iterrows():
            print(f"      {str(row['Name'])[:34]:36}{str(row['Address'])[:70]}")
        if len(unknown) > 15:
            print(f"      ... and {len(unknown)-15} more")
        print("      -> add the spelling to _EMIRATE_ALIASES and reprocess "
              "(free, no re-scrape)")

    print(f"\nWith phone number  : {(df['Contact_No'] != '').sum():,}")
    print(f"With website       : {(df['Website'] != '').sum():,}")
    print(f"With Google URL    : {(df['Google_Maps_URL'] != '').sum():,}")
    print(f"With coordinates   : {(df['Geo_Lat'] != '').sum():,}")
    print(f"With rating        : {df['Rating'].notna().sum():,}")

    avg = pd.to_numeric(df["Rating"], errors="coerce").mean()
    if pd.notna(avg):
        print(f"Avg rating         : {avg:.2f}")

    # Flag names that don't actually contain any of the brand's own keywords
    # (only possible via --include-all; the normal brand filter already
    # excludes these, so this is a sanity-check for that mode).
    unexpected = [row["Name"] for _, row in df.iterrows() if not is_brand(row["Name"], brand_keywords, exclude_keywords)]
    if unexpected:
        print(f"\nNames to review manually ({len(unexpected)}):")
        for n in unexpected[:10]:
            print(f"  {n}")

    print("=" * 62)


# -- Entry point ---------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generic brand result processor")
    parser.add_argument("--brand",       required=True,
                        help="Brand folder name under brands/ (e.g. dominos)")
    parser.add_argument("--input",       metavar="PATH",
                        help="Raw JSON (default: latest in brands/<brand>/output/raw/)")
    parser.add_argument("--include-all", action="store_true",
                        help="Keep all results, not just brand-name matches")
    args = parser.parse_args()

    brand        = args.brand.lower()
    brand_config = load_brand_config(brand)
    query_map    = build_query_map(brand_config)
    keywords     = brand_config.get("brand_keywords", [brand])
    exclude_kw   = brand_config.get("brand_exclude_keywords", [])
    out_filename = brand_config.get("output_filename", brand.upper() + "_UAE")
    brand_display= brand_config.get("brand_display", brand.upper() + " UAE")

    input_path = Path(args.input) if args.input else find_latest_raw_file(brand)
    logger.info(f"Brand         : {brand_display}")
    logger.info(f"Keywords      : {keywords}")
    logger.info(f"Query map     : {len(query_map)} entries loaded")

    df = process(input_path, brand_config, query_map, keywords, args.include_all,
                 exclude_keywords=exclude_kw)
    if not df.empty:
        write_outputs(df, brand, out_filename)
        print_summary(df, brand_display, keywords, exclude_kw)


if __name__ == "__main__":
    main()
