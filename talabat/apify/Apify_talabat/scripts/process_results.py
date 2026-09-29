"""
process_results.py
------------------
Cleans, deduplicates, normalises and exports the raw Apify dataset
to output/OUTPUT.csv and output/OUTPUT.json.

Deduplication — three safeguards so the same physical restaurant never
appears more than once, regardless of which area query scraped it:

  Layer 1  exact placeId          — same place returned by multiple area queries
  Layer 2  (Name, norm_address)   — same restaurant even if placeId differs
                                    (applies only when address is non-empty)

Cross-run dedup — pass --skip-seen to skip placeIds already written to
OUTPUT.json from a previous run, so re-running after new data arrives
never inflates the output with already-known places.

PlaceId enrichment — once you have placeIds you can call the Google Places
Details API directly (GET /v1/places/{placeId}) instead of a text search.
Use build_enrichment_ids() to get the ready-to-use list.

Usage:
    python process_results.py                         # process latest raw file
    python process_results.py --input path/to.json    # specific file
    python process_results.py --include-closed        # keep closed places
    python process_results.py --skip-seen             # skip known place IDs
"""

import re
import sys
import json
import argparse
import logging
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

RAW_DIR     = Path(__file__).parent / "output" / "raw"
OUTPUT_DIR  = Path(__file__).parent / "output"
OUTPUT_CSV  = OUTPUT_DIR / "OUTPUT.csv"
OUTPUT_JSON = OUTPUT_DIR / "OUTPUT.json"
META_FILE   = Path(__file__).parent / "query_metadata.json"

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


# ── Category mapping ──────────────────────────────────────────────────────────
# Maps Google's 100+ venue category labels to our 8 internal category types.
# NOTE: this is post-processing classification only — it does NOT control what
# gets scraped. Scraping coverage is determined by the search terms in
# generate_input.py (CATEGORIES list).
CATEGORY_MAP = {
    # Full-Service — sit-down dining, any cuisine
    "restaurant":                  "Full-Service",
    "indian restaurant":           "Full-Service",
    "pakistani restaurant":        "Full-Service",
    "chinese restaurant":          "Full-Service",
    "japanese restaurant":         "Full-Service",
    "thai restaurant":             "Full-Service",
    "italian restaurant":          "Full-Service",
    "lebanese restaurant":         "Full-Service",
    "arabic restaurant":           "Full-Service",
    "arab restaurant":             "Full-Service",
    "middle eastern restaurant":   "Full-Service",
    "seafood restaurant":          "Full-Service",
    "steak house":                 "Full-Service",
    "buffet restaurant":           "Full-Service",
    "family restaurant":           "Full-Service",
    "fine dining restaurant":      "Full-Service",
    "mexican restaurant":          "Full-Service",
    "american restaurant":         "Full-Service",
    "mediterranean restaurant":    "Full-Service",
    "turkish restaurant":          "Full-Service",
    "iranian restaurant":          "Full-Service",
    "afghan restaurant":           "Full-Service",
    "filipino restaurant":         "Full-Service",
    "sri lankan restaurant":       "Full-Service",
    "korean restaurant":           "Full-Service",
    "korean barbecue restaurant":  "Full-Service",
    "vietnamese restaurant":       "Full-Service",
    "british restaurant":          "Full-Service",
    "vegetarian restaurant":       "Full-Service",
    "vegan restaurant":            "Full-Service",
    "health food restaurant":      "Full-Service",
    "gluten-free restaurant":      "Full-Service",
    "asian restaurant":            "Full-Service",
    "pan-asian restaurant":        "Full-Service",
    "asian fusion restaurant":     "Full-Service",
    "fusion restaurant":           "Full-Service",
    "south indian restaurant":     "Full-Service",
    "kerala restaurant":           "Full-Service",
    "hyderabadi restaurant":       "Full-Service",
    "biryani restaurant":          "Full-Service",
    "mughlai restaurant":          "Full-Service",
    "marathi restaurant":          "Full-Service",
    "momo restaurant":             "Full-Service",
    "tiffin center":               "Full-Service",
    "rice restaurant":             "Full-Service",
    "nepalese restaurant":         "Full-Service",
    "sushi restaurant":            "Full-Service",
    "wok restaurant":              "Full-Service",
    "indonesian restaurant":       "Full-Service",
    "uzbeki restaurant":           "Full-Service",
    "uyghur cuisine restaurant":   "Full-Service",
    "yemeni restaurant":           "Full-Service",
    "syrian restaurant":           "Full-Service",
    "tunisian restaurant":         "Full-Service",
    "egyptian restaurant":         "Full-Service",
    "barbecue restaurant":         "Full-Service",
    "grill":                       "Full-Service",
    "lunch restaurant":            "Full-Service",
    "spanish restaurant":          "Full-Service",
    "french restaurant":           "Full-Service",
    "greek restaurant":            "Full-Service",
    "peruvian restaurant":         "Full-Service",
    "cajun restaurant":            "Full-Service",
    "taco restaurant":             "Full-Service",
    # QSR — quick service, counter service, takeout-first
    "fast food restaurant":        "QSR",
    "hamburger restaurant":        "QSR",
    "pizza restaurant":            "QSR",
    "pizza takeout":               "QSR",
    "chicken restaurant":          "QSR",
    "chicken wings restaurant":    "QSR",
    "sandwich shop":               "QSR",
    "shawarma restaurant":         "QSR",
    "kebab shop":                  "QSR",
    "meal takeaway":               "QSR",
    "takeout restaurant":          "QSR",
    "hot dog restaurant":          "QSR",
    "wrap restaurant":             "QSR",
    # Cafe — coffee, brunch, light bites
    "cafe":                        "Cafe",
    "coffee shop":                 "Cafe",
    "coffee store":                "Cafe",
    "coffee roastery":             "Cafe",
    "tea house":                   "Cafe",
    "brunch restaurant":           "Cafe",
    "breakfast restaurant":        "Cafe",
    "internet cafe":               "Cafe",
    # Bakery — baked goods, sweets, patisserie
    "bakery":                      "Bakery",
    "cake shop":                   "Bakery",
    "patisserie":                  "Bakery",
    "pastry shop":                 "Bakery",
    "donut shop":                  "Bakery",
    "dessert shop":                "Bakery",
    "chocolate shop":              "Bakery",
    "confectionery store":         "Bakery",
    # Cloud Kitchen — delivery-only, no dine-in
    "meal delivery":               "Cloud Kitchen",
    "delivery restaurant":         "Cloud Kitchen",
    "ghost kitchen":               "Cloud Kitchen",
    "shared-use commercial kitchen": "Cloud Kitchen",
    "delivery service":            "Cloud Kitchen",
    "food delivery":               "Cloud Kitchen",
    "delivery chinese restaurant": "Cloud Kitchen",
    "pizza delivery":              "Cloud Kitchen",
    # Dessert & Drinks — standalone dessert / drinks venues
    "ice cream shop":              "Dessert & Drinks",
    "juice bar":                   "Dessert & Drinks",
    "bubble tea store":            "Dessert & Drinks",
    "frozen yogurt shop":          "Dessert & Drinks",
    "smoothie bar":                "Dessert & Drinks",
    # Bar — licensed, adult beverages (kept in data; filter by Category_Type if needed)
    "bar":                         "Bar",
    "pub":                         "Bar",
    "wine bar":                    "Bar",
    "sports bar":                  "Bar",
    "cocktail bar":                "Bar",
    "hookah bar":                  "Bar",
    # Bar & grill / gastropub — primarily food venues that also serve alcohol
    "bar & grill":                 "Full-Service",
    "gastropub":                   "Full-Service",
    # Food court — collection of QSR stalls inside a mall; IS F&B
    "food court":                  "QSR",
    "cafeteria":                   "QSR",
}

# ── Non-F&B exclusion list ─────────────────────────────────────────────────────
# Records whose primary Google category falls in this set are dropped completely
# during normalise_record(). These are venues that appear in F&B search results
# but are NOT restaurants, cafes, bakeries, or cloud kitchens.
#
# Target taxonomy:  Full-Service · QSR · Cafe · Bakery · Cloud Kitchen
# Everything else is either remapped above (CATEGORY_MAP) or excluded here.
NON_FNB_CATEGORIES = {
    # ── Retail / grocery ──────────────────────────────────────────────────────
    "hypermarket",                      # retail superstore, not a restaurant
    "supermarket",                      # retail grocery, not a restaurant
    "convenience store",
    "discount store",
    "dairy products shop",
    "dairy store",
    "food products supplier",           # wholesale, B2B
    # ── Kitchen / equipment trade ─────────────────────────────────────────────
    "kitchen supply store",
    "kitchen furniture store",
    "kitchen remodeler",
    "restaurant supply store",
    # ── B2B catering & events (not consumer dining) ───────────────────────────
    "catering food and drink supplier",
    "caterer",                          # event catering, not a dining venue
    # ── Hospitality / lodging ─────────────────────────────────────────────────
    "hotel",                            # accommodation; F&B inside hotels is secondary
    "motel",
    "hostel",
    "bed and breakfast",
    "resort",
    # ── Lifestyle / entertainment (no primary F&B service) ────────────────────
    "beach club",                       # beach/pool venue; F&B is incidental
    "nightclub",
    "karaoke bar",                      # entertainment-first, not F&B
    "amusement center",
    # ── Unrelated services ────────────────────────────────────────────────────
    "florist",
    "laundry service",
    "moving service",
    "moving and storage service",
    "machine maintenance service",
    "logistics service",
    "corporate office",
    # ── Venues / infrastructure ───────────────────────────────────────────────
    "shopping mall",                    # venue container; not an F&B operator
    "tourist attraction",
    "tourist information center",
}


def map_category(category_name: str) -> str:
    if not category_name:
        return "Other"
    return CATEGORY_MAP.get(category_name.lower().strip(), "Other")


def clean_maps_url(url: str) -> str:
    """Keep only the cid= param — strip all tracking noise."""
    if not url:
        return ""
    if "cid=" in url:
        from urllib.parse import urlparse, parse_qs, urlunparse
        p      = urlparse(url)
        params = parse_qs(p.query)
        cid    = params.get("cid", [""])[0]
        if cid:
            return urlunparse((p.scheme, p.netloc, p.path, "", f"cid={cid}", ""))
    return url


# ── Address normalisation ─────────────────────────────────────────────────────

# Tokens that carry no discriminating information in a UAE address comparison
_UAE_NOISE = re.compile(
    r'\b(united arab emirates|uae|u\.a\.e\.?)\b',
    re.IGNORECASE,
)

def norm_address(addr: str) -> str:
    """
    Produce a canonical address key for deduplication.

    Rules applied (in order):
      1. Lower-case
      2. Remove UAE country tokens  ("United Arab Emirates", "UAE", "U.A.E.")
      3. Replace common punctuation  , . - / # \\ ( )  with a space
      4. Collapse all whitespace to a single space and strip

    Two addresses that share the same normalised form are considered the same
    physical location when the restaurant name also matches.

    >>> norm_address("Dubai Mall, Downtown Dubai, Dubai, UAE")
    'dubai mall downtown dubai dubai'
    >>> norm_address("Dubai Mall, Downtown Dubai, Dubai, United Arab Emirates")
    'dubai mall downtown dubai dubai'
    """
    if not addr:
        return ""
    s = _UAE_NOISE.sub("", addr.lower())
    s = re.sub(r'[,.\-/#\\()]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


# ── Record normalisation ──────────────────────────────────────────────────────

def normalise_record(raw: dict, area: str = "") -> dict | None:
    """
    Map a raw Apify compass/crawler-google-places item to our output schema.
    Returns None if the record should be skipped (closed or no name).

    Output schema
    -------------
    place_id, Name, Address, Street, Neighborhood, City,
    Contact_No, Website,
    Geo_Lat, Geo_Lng, Google_Maps_URL, Plus_Code,
    Category, All_Categories, Category_Type,
    Rating, Review_Count, Rating_Distribution {1_star..5_star},
    Price_Range, Menu_URL, Opening_Hours,
    Area, Scraped_At, Permanently_Closed
    """
    if raw.get("permanentlyClosed") or raw.get("temporarilyClosed"):
        return None

    name = (raw.get("title") or "").strip()
    if not name:
        return None

    # Exclude non-F&B venues that slip into F&B searches
    cat_raw = (raw.get("categoryName") or "").strip().lower()
    if cat_raw in NON_FNB_CATEGORIES:
        return "non_fnb"   # sentinel — caller increments skipped_non_fnb

    loc = raw.get("location") or {}
    cat = raw.get("categoryName") or ""

    rd = raw.get("reviewsDistribution") or {}
    rating_dist = {
        "1_star": rd.get("oneStar",   0),
        "2_star": rd.get("twoStar",   0),
        "3_star": rd.get("threeStar", 0),
        "4_star": rd.get("fourStar",  0),
        "5_star": rd.get("fiveStar",  0),
    }

    return {
        "place_id":            raw.get("placeId", ""),
        "Name":                name,
        "Address":             (raw.get("address")      or "").strip(),
        "Street":              (raw.get("street")       or "").strip(),
        "Neighborhood":        (raw.get("neighborhood") or "").strip(),
        "City":                (raw.get("city")         or "").strip(),
        "Contact_No":          (raw.get("phone")        or "").strip(),
        "Website":             (raw.get("website")      or "").strip(),
        "Geo_Lat":             loc.get("lat", ""),
        "Geo_Lng":             loc.get("lng", ""),
        "Google_Maps_URL":     clean_maps_url(raw.get("url", "")),
        "Plus_Code":           (raw.get("plusCode")     or "").strip(),
        "Category":            cat,
        "All_Categories":      raw.get("categories") or ([cat] if cat else []),
        "Category_Type":       map_category(cat),
        "Rating":              raw.get("totalScore"),
        "Review_Count":        raw.get("reviewsCount", 0),
        "Rating_Distribution": rating_dist,
        "Price_Range":         (raw.get("price")        or "").strip(),
        "Menu_URL":            (raw.get("menu")         or "").strip(),
        "Opening_Hours":       raw.get("openingHours")  or [],
        "Area":                area,
        "Scraped_At":          (raw.get("scrapedAt")    or "").strip(),
        "Permanently_Closed":  bool(raw.get("permanentlyClosed", False)),
    }


# ── Cross-run deduplication helpers ──────────────────────────────────────────

def load_seen_ids(source: Path = None) -> set[str]:
    """
    Return the set of placeIds already written in a previous output run.
    Pass source=Path(...) to point at a non-default JSON file.
    Returns an empty set if the file doesn't exist or can't be read.

    Use with process(..., skip_seen=True) to avoid adding known places to
    the output when you re-scrape with new Apify data.
    """
    path = source or OUTPUT_JSON
    if not path.exists():
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        ids = {r["place_id"] for r in data if r.get("place_id")}
        logger.info(f"Cross-run: {len(ids):,} known place IDs loaded from {path.name}")
        return ids
    except Exception as exc:
        logger.warning(f"Could not load seen IDs from {path}: {exc}")
        return set()


def build_enrichment_ids(source: Path = None) -> list[str]:
    """
    Return all placeIds from OUTPUT.json (or a custom path) as a plain list.

    Why use this instead of a new text search?
    ------------------------------------------
    A Google Places Details call  GET /v1/places/{placeId}
    costs ~$17/1 000 requests and is a single deterministic lookup —
    no name-matching, no fallback queries, no false positives.
    A Text Search costs ~$32/1 000 and may return the wrong place.

    Example usage with the existing fetch_places helper:
        ids = build_enrichment_ids()
        for pid in ids:
            resp = requests.get(
                f"https://places.googleapis.com/v1/places/{pid}",
                headers={"X-Goog-Api-Key": API_KEY,
                         "X-Goog-FieldMask": "displayName,rating,priceLevel,regularOpeningHours"}
            )
    """
    path = source or OUTPUT_JSON
    if not path.exists():
        logger.warning(f"No output file found at {path} — returning empty list")
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        ids = [r["place_id"] for r in data if r.get("place_id")]
        logger.info(f"Enrichment: {len(ids):,} placeIds ready for Google Places Details API")
        return ids
    except Exception as exc:
        logger.warning(f"Could not read {path}: {exc}")
        return []


# ── Multi-layer deduplication ─────────────────────────────────────────────────

def _deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove duplicate restaurant records through two progressive layers.

    Layer 1 — exact placeId
        Google Maps guarantees a unique placeId per physical location.
        This handles the most common case: the same restaurant returned by
        two overlapping area queries (e.g. a café on the Downtown / Business
        Bay boundary appearing in both searches).

    Layer 2 — (Name.lower, norm_address)  [only for non-empty addresses]
        Safety net for rare cases where the same place appears with a
        different placeId (Google does reassign IDs occasionally, and Apify
        edge cases can sometimes return stale IDs).
        Crucially, two DIFFERENT restaurants at the same address — e.g.
        multiple outlets in a food court — are kept because their names differ.
        Records with an empty address bypass this layer entirely.

    Preserves the first occurrence and drops later duplicates.
    """
    n0 = len(df)

    # ── Layer 1: exact placeId ────────────────────────────────────────────────
    df = df[df["place_id"] != ""].copy()
    df = df.drop_duplicates(subset=["place_id"], keep="first")
    n1 = len(df)

    # ── Layer 2: (name, normalised address) for non-empty addresses ───────────
    df["_name"] = df["Name"].str.lower().str.strip()
    df["_addr"] = df["Address"].apply(norm_address)

    has_addr  = df["_addr"] != ""
    deduped   = df[has_addr].drop_duplicates(
        subset=["_name", "_addr"], keep="first"
    )
    df = pd.concat([deduped, df[~has_addr]], ignore_index=True)
    n2 = len(df)

    df = df.drop(columns=["_name", "_addr"]).reset_index(drop=True)

    logger.info(f"Dedup Layer 1 (placeId)     : {n0 - n1:,} removed")
    logger.info(f"Dedup Layer 2 (name+address): {n1 - n2:,} removed")
    logger.info(f"Unique places remaining     : {n2:,}")
    return df


# ── Core pipeline ─────────────────────────────────────────────────────────────

def load_query_metadata() -> dict:
    """Return {query_string: area} from query_metadata.json."""
    if not META_FILE.exists():
        logger.warning("query_metadata.json not found - Area column will be empty")
        return {}
    with open(META_FILE, encoding="utf-8") as f:
        meta = json.load(f)
    return {m["query"]: m["area"] for m in meta}


def find_latest_raw_file() -> Path:
    files = sorted(RAW_DIR.glob("dataset_*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No raw dataset files found in {RAW_DIR}")
    return files[0]


def process(input_path: Path,
            include_closed: bool = False,
            skip_seen:      bool = False) -> pd.DataFrame:
    """
    Load a raw Apify JSON file, normalise every record, deduplicate, and
    filter to UAE-only addresses.

    Parameters
    ----------
    input_path    : path to the raw dataset JSON
    include_closed: if True, include permanently / temporarily closed places
    skip_seen     : if True, skip placeIds already present in OUTPUT.json
                    (cross-run dedup — saves re-adding known places when
                    you process a fresh Apify run against an existing output)
    """
    logger.info(f"Loading raw data from: {input_path}")
    with open(input_path, encoding="utf-8") as f:
        raw_items = json.load(f)
    logger.info(f"Raw items: {len(raw_items):,}")

    query_to_area = load_query_metadata()

    # Load known placeIds for cross-run dedup
    seen_ids     = load_seen_ids() if skip_seen else set()
    skipped_seen = 0

    records          = []
    skipped_closed   = 0
    skipped_no_name  = 0
    skipped_non_fnb  = 0   # hotels, hypermarkets, corporate offices, etc.

    for item in raw_items:
        # Cross-run guard — skip places already in the output file
        pid = item.get("placeId", "")
        if skip_seen and pid and pid in seen_ids:
            skipped_seen += 1
            continue

        # Determine area from query metadata
        # Actor v3+ uses "searchString"; older test data uses "searchQuery.term"
        search_query = item.get("searchQuery", {})
        query_str    = search_query.get("term", "") if isinstance(search_query, dict) else ""
        if not query_str:
            query_str = item.get("searchString", "")
        area         = query_to_area.get(query_str, "")

        if item.get("permanentlyClosed") or item.get("temporarilyClosed"):
            if not include_closed:
                skipped_closed += 1
                continue

        rec = normalise_record(item, area=area)
        if rec == "non_fnb":
            skipped_non_fnb += 1
            continue
        if rec is None:
            skipped_no_name += 1
            continue
        records.append(rec)

    if skip_seen:
        logger.info(f"Skipped (cross-run seen) : {skipped_seen:,}")
    logger.info(f"Skipped closed          : {skipped_closed:,}")
    logger.info(f"Skipped non-F&B         : {skipped_non_fnb:,}  (hotels, retail, corporate, etc.)")
    logger.info(f"Skipped no-name         : {skipped_no_name:,}")
    logger.info(f"Valid before dedup      : {len(records):,}")

    df = pd.DataFrame(records)

    # ── Multi-layer deduplication ──────────────────────────────────────────────
    df = _deduplicate(df)

    # ── Filter: UAE addresses only ─────────────────────────────────────────────
    uae_keywords = ["UAE", "United Arab Emirates", "Dubai", "Sharjah",
                    "Abu Dhabi", "Ajman", "Fujairah", "Ras Al Khaimah"]
    mask    = df["Address"].apply(
        lambda a: any(k.lower() in a.lower() for k in uae_keywords)
    )
    non_uae = (~mask).sum()
    df = df[mask]
    if non_uae:
        logger.info(f"Filtered non-UAE        : {non_uae:,} removed")

    df = df.sort_values(["Area", "Category_Type", "Name"]).reset_index(drop=True)
    return df


# ── Merge helper ─────────────────────────────────────────────────────────────

def load_existing_output(path: Path = None) -> pd.DataFrame:
    """
    Load the previously written OUTPUT.json as a DataFrame so new records
    can be appended to it without losing Run N data.
    Returns an empty DataFrame (with correct columns) if the file doesn't exist.
    """
    src = path or OUTPUT_JSON
    if not src.exists():
        return pd.DataFrame()
    try:
        with open(src, encoding="utf-8") as f:
            data = json.load(f)
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        # Geo_Coordinates is stored as {"lat":..,"lng":..} in JSON; unpack it
        if "Geo_Coordinates" in df.columns and "Geo_Lat" not in df.columns:
            df["Geo_Lat"] = df["Geo_Coordinates"].apply(
                lambda v: v.get("lat", "") if isinstance(v, dict) else "")
            df["Geo_Lng"] = df["Geo_Coordinates"].apply(
                lambda v: v.get("lng", "") if isinstance(v, dict) else "")
            df = df.drop(columns=["Geo_Coordinates"])
        logger.info(f"Merged existing records : {len(df):,} from {src.name}")
        return df
    except Exception as exc:
        logger.warning(f"Could not load existing output from {src}: {exc}")
        return pd.DataFrame()


# ── Output writers ────────────────────────────────────────────────────────────

def write_outputs(df: pd.DataFrame, merge_existing: bool = False) -> pd.DataFrame:
    """Write CSV + JSON and return the final (possibly merged) DataFrame."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # When called with merge_existing=True (--skip-seen run), prepend the
    # previously saved records so the output always holds the full corpus.
    if merge_existing and OUTPUT_JSON.exists():
        existing = load_existing_output()
        if not existing.empty:
            df = pd.concat([existing, df], ignore_index=True)
            # Final dedup pass in case any IDs slipped through both runs
            df = df.drop_duplicates(subset=["place_id"], keep="first")
            df = df.sort_values(["Area", "Category_Type", "Name"]).reset_index(drop=True)
            logger.info(f"After merge + dedup     : {len(df):,} total unique places")

    # ── CSV (flat) ─────────────────────────────────────────────────────────────
    csv_df = df.copy()
    if "All_Categories" in csv_df.columns:
        csv_df["All_Categories"] = csv_df["All_Categories"].apply(
            lambda v: " | ".join(v) if isinstance(v, list) else str(v or "")
        )
    for col in ("Opening_Hours", "Rating_Distribution"):
        if col in csv_df.columns:
            csv_df[col] = csv_df[col].apply(
                lambda v: json.dumps(v, ensure_ascii=False)
                          if isinstance(v, (list, dict)) else v
            )
    csv_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    logger.info(f"CSV  saved -> {OUTPUT_CSV}  ({len(csv_df):,} rows)")

    # ── JSON (rich, nested) ────────────────────────────────────────────────────
    records = []
    for _, r in df.iterrows():
        rating = r["Rating"]
        if rating is None or (isinstance(rating, float) and pd.isna(rating)):
            rating = None

        records.append({
            "place_id":            r["place_id"],
            "Name":                r["Name"],
            "Address":             r["Address"],
            "Street":              r["Street"],
            "Neighborhood":        r["Neighborhood"],
            "City":                r["City"],
            "Contact_No":          r["Contact_No"],
            "Website":             r["Website"],
            "Geo_Coordinates":     {"lat": r["Geo_Lat"], "lng": r["Geo_Lng"]},
            "Google_Maps_URL":     r["Google_Maps_URL"],
            "Plus_Code":           r["Plus_Code"],
            "Category":            r["Category"],
            "All_Categories":      r["All_Categories"]
                                   if isinstance(r["All_Categories"], list) else [],
            "Category_Type":       r["Category_Type"],
            "Rating":              rating,
            "Review_Count":        0 if pd.isna(r["Review_Count"]) else int(r["Review_Count"]),
            "Rating_Distribution": r["Rating_Distribution"]
                                   if isinstance(r["Rating_Distribution"], dict) else {},
            "Price_Range":         r["Price_Range"],
            "Menu_URL":            r["Menu_URL"],
            "Opening_Hours":       r["Opening_Hours"]
                                   if isinstance(r["Opening_Hours"], list) else [],
            "Area":                r["Area"],
            "Scraped_At":          r["Scraped_At"],
            "Permanently_Closed":  bool(r["Permanently_Closed"]),
        })

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"JSON saved -> {OUTPUT_JSON}  ({len(records):,} entries)")

    return df


def print_summary(df: pd.DataFrame):
    print("\n" + "=" * 60)
    print(f"FINAL SUMMARY  -  {len(df):,} unique places")
    print("=" * 60)
    print("\nBy Category_Type:")
    print(df["Category_Type"].value_counts().to_string())
    print("\nBy Area (top 10):")
    print(df["Area"].value_counts().head(10).to_string())
    print(f"\nWith phone         : {(df['Contact_No'] != '').sum():,}")
    print(f"With website       : {(df['Website']    != '').sum():,}")
    print(f"With rating        : {df['Rating'].notna().sum():,}")
    avg = pd.to_numeric(df["Rating"], errors="coerce").mean()
    if pd.notna(avg):
        print(f"Avg rating         : {avg:.2f}")
    if "Price_Range" in df.columns:
        print(f"With price range   : {(df['Price_Range'] != '').sum():,}")
    if "Menu_URL" in df.columns:
        print(f"With menu URL      : {(df['Menu_URL'] != '').sum():,}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Process raw Apify dataset")
    parser.add_argument("--input", metavar="PATH",
                        help="Path to raw JSON (default: latest in output/raw/)")
    parser.add_argument("--include-closed", action="store_true",
                        help="Include permanently/temporarily closed places")
    parser.add_argument("--skip-seen", action="store_true",
                        help="Skip placeIds already in output/OUTPUT.json "
                             "(cross-run dedup)")
    args = parser.parse_args()

    input_path = Path(args.input) if args.input else find_latest_raw_file()

    df = process(input_path,
                 include_closed=args.include_closed,
                 skip_seen=args.skip_seen)
    final_df = write_outputs(df, merge_existing=args.skip_seen)
    print_summary(final_df)


if __name__ == "__main__":
    main()
