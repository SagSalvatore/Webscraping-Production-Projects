"""
fetch_places.py
---------------
Fetches address details for the first 500 restaurants in RESTRO_LIST.csv
using the Google Places API (New) searchText endpoint.

Usage:
    python fetch_places.py

Outputs:
    output/OUTPUT.csv
    output/OUTPUT.json
    output/output_partial.csv   (checkpoint — auto resume on restart)
    logs/run_YYYYMMDD_HHMMSS.log
"""

import os
import sys
import json
import time
import logging
import difflib
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from tqdm import tqdm
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
API_KEY       = os.getenv("GOOGLE_API_KEY", "")
ENDPOINT      = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK    = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.googleMapsUri",
    "places.types",               # used to filter out non-food businesses
])

# Any place whose types intersect this set is considered a food establishment.
# Covers restaurants, cafes, bakeries, bars, takeaways, dessert shops, etc.
FOOD_TYPES = {
    "restaurant", "cafe", "bar", "bakery", "food",
    "meal_takeaway", "meal_delivery", "fast_food_restaurant",
    "pizza_restaurant", "hamburger_restaurant", "sandwich_shop",
    "ice_cream_shop", "dessert_shop", "coffee_shop", "tea_house",
    "juice_bar", "donut_shop", "bagel_shop", "diner", "food_court",
    "brunch_restaurant", "breakfast_restaurant", "ramen_restaurant",
    "sushi_restaurant", "seafood_restaurant", "steak_house",
    "indian_restaurant", "chinese_restaurant", "japanese_restaurant",
    "korean_restaurant", "thai_restaurant", "italian_restaurant",
    "mexican_restaurant", "american_restaurant", "vegetarian_restaurant",
    "vegan_restaurant", "buffet_restaurant", "bubble_tea_store",
}

# UAE bounding box — hard restriction so ONLY UAE results come back
# Covers Dubai, Sharjah, Abu Dhabi, Ajman, RAK, Fujairah, UAQ
UAE_BBOX = {
    "rectangle": {
        "low":  {"latitude": 22.6, "longitude": 51.5},   # SW corner
        "high": {"latitude": 26.2, "longitude": 56.4},   # NE corner
    }
}

INPUT_FILE        = "RESTRO_LIST.csv"
CHECKPOINT_FILE   = "output/output_partial.csv"
OUTPUT_CSV        = "output/OUTPUT.csv"
OUTPUT_JSON       = "output/OUTPUT.json"
LOG_DIR           = "logs"

MAX_RESTAURANTS   = 100             # process first 100 listings
RATE_SLEEP        = 0.12            # ~8 req/sec (well under 50 QPS quota)
CHECKPOINT_EVERY  = 25              # save progress every N rows

# ── Columns written to output ────────────────────────────────────────────────
USER_COLS = [
    "row_id", "slug",
    "Restaurant_Name", "Address", "Contact_No",
    "Website", "Geo_Lat", "Geo_Lng", "Google_Maps_URL",
    "status",
]


# ── Logging ──────────────────────────────────────────────────────────────────
def setup_logging() -> logging.Logger:
    Path(LOG_DIR).mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = Path(LOG_DIR) / f"run_{ts}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger(__name__)


# ── Utilities ────────────────────────────────────────────────────────────────
def slug_to_name(slug: str) -> str:
    """Convert snake_case slug to a human-readable title-case name."""
    return slug.strip().replace("_", " ").title()


def empty_record(row_id, slug) -> dict:
    return {
        "row_id": row_id,
        "slug": slug,
        "Restaurant_Name": "",
        "Address": "",
        "Contact_No": "",
        "Website": "",
        "Geo_Lat": "",
        "Geo_Lng": "",
        "Google_Maps_URL": "",
        "status": "not_found",
        "error_msg": "",
    }


# Common filler words to ignore when comparing query vs result names
_NOISE = {"restaurant", "cafe", "the", "of", "and", "by", "al", "el", "la",
          "le", "a", "an", "&", "-", "grill", "house", "kitchen"}

def _name_matches(query_name: str, result_name: str, threshold: float = 0.35) -> bool:
    """
    Return True if the Google result name is plausibly the same place we searched for.
    Uses two checks:
      1. difflib ratio on lowercased names (catches typos, articles, punctuation)
      2. meaningful word overlap (catches abbreviated names like 'P.F. Chang's' vs 'Pf Changs')
    """
    q = query_name.lower().strip()
    r = result_name.lower().strip()

    # Fast path — exact substring match
    if q in r or r in q:
        return True

    # Difflib similarity
    ratio = difflib.SequenceMatcher(None, q, r).ratio()
    if ratio >= threshold:
        return True

    # Word-level overlap (ignoring noise words and pure numbers)
    def _meaningful(words):
        return {w.strip("'.,!") for w in words.split()
                if w not in _NOISE and not w.isdigit() and len(w) >= 3}

    q_words = _meaningful(q)
    r_words = _meaningful(r)
    if q_words and r_words and (q_words & r_words):
        return True

    return False


def _is_food_place(types: list) -> bool:
    """Return True if Google's place types include at least one food category."""
    return bool(set(types) & FOOD_TYPES)


def _clean_maps_url(raw_url: str) -> str:
    """Strip tracking params from Maps URL, keeping only ?cid= for a clean link."""
    if not raw_url:
        return ""
    parsed = urlparse(raw_url)
    params = parse_qs(parsed.query)
    cid = params.get("cid", [""])[0]
    if cid:
        clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", f"cid={cid}", ""))
        return clean
    return raw_url  # fallback: return as-is if no cid param found


# ── API ───────────────────────────────────────────────────────────────────────
def _post_search(query: str) -> dict:
    """
    Raw POST to Places searchText with UAE locationRestriction (hard boundary).
    All results are guaranteed to be inside UAE — no cross-border false matches.
    """
    body = {
        "textQuery": query,
        "maxResultCount": 1,
        "languageCode": "en",
        "locationRestriction": UAE_BBOX,   # hard filter — UAE results only
    }
    response = requests.post(
        ENDPOINT,
        json=body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": FIELD_MASK,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    reraise=True,
)
def call_places_api(query: str) -> dict:
    """Call Google Places searchText (UAE-restricted) with automatic retry."""
    return _post_search(query)


def parse_result(raw: dict) -> dict | None:
    """
    Extract the fields we care about from the raw API response.
    Returns None if no places were found.
    """
    places = raw.get("places", [])
    if not places:
        return None

    p = places[0]
    loc = p.get("location", {})

    # Prefer international format for phone (includes country code)
    phone = (
        p.get("internationalPhoneNumber")
        or p.get("nationalPhoneNumber")
        or ""
    )

    return {
        "place_id":         p.get("id", ""),
        "Restaurant_Name":  p.get("displayName", {}).get("text", ""),
        "Address":          p.get("formattedAddress", ""),
        "Contact_No":       phone,
        "Website":          p.get("websiteUri", ""),
        "Geo_Lat":          loc.get("latitude", ""),
        "Geo_Lng":          loc.get("longitude", ""),
        "Google_Maps_URL":  _clean_maps_url(p.get("googleMapsUri", "")),
        "types":            p.get("types", []),
    }


def fetch_restaurant(slug: str, row_id: int, logger: logging.Logger) -> dict:
    """
    Full fetch pipeline for a single restaurant slug:
      1. Try with Dubai location bias
      2. On not-found, retry with no bias (broader UAE search)
    Returns a fully populated record dict.
    """
    name   = slug_to_name(slug)
    record = empty_record(row_id, slug)

    # Query ladder — each attempt is UAE-restricted (locationRestriction hard boundary)
    # We do NOT force "restaurant" in the query so cafes, bakeries, etc. resolve correctly.
    # Dubai is tried first since most listings are Dubai-based; fallback covers all UAE.
    query_attempts = [
        f"{name} Dubai",   # primary: name + city (most specific)
        f"{name} UAE",     # fallback: name + country (catches Sharjah, Abu Dhabi etc.)
    ]

    try:
        for attempt_num, query in enumerate(query_attempts, start=1):
            raw    = call_places_api(query)
            result = parse_result(raw)

            if not result:
                continue

            name_ok = _name_matches(name, result["Restaurant_Name"])
            food_ok = _is_food_place(result.get("types", []))

            if name_ok and food_ok:
                record.update(result)
                record["status"] = "found" if attempt_num == 1 else "found_broad"
                tag = "[OK]   " if attempt_num == 1 else "[BROAD]"
                logger.info(
                    f"{tag} {slug:40s} -> {result['Restaurant_Name']} "
                    f"| {result['Address'][:45]}"
                )
                return record

            # Log specific reason for rejection
            if not food_ok:
                logger.warning(
                    f"[SKIP]  attempt {attempt_num} '{slug}': "
                    f"'{result['Restaurant_Name']}' is not a food place "
                    f"(types: {result.get('types', [])})"
                )
            elif not name_ok:
                logger.warning(
                    f"[SKIP]  attempt {attempt_num} '{slug}': "
                    f"name mismatch -> got '{result['Restaurant_Name']}'"
                )

        logger.warning(f"[MISS]  {slug}")

    except requests.exceptions.HTTPError as exc:
        record["status"] = "error"
        record["error_msg"] = f"HTTP {exc.response.status_code}: {exc}"
        logger.error(f"[ERR]   {slug}: {exc}")
    except requests.exceptions.Timeout:
        record["status"] = "error"
        record["error_msg"] = "Request timed out after 3 retries"
        logger.error(f"[TIMEOUT] {slug}")
    except Exception as exc:
        record["status"] = "error"
        record["error_msg"] = str(exc)
        logger.error(f"[ERR]   {slug}: {exc}")

    return record


# ── Checkpoint ────────────────────────────────────────────────────────────────
def load_checkpoint() -> tuple[set, list]:
    if Path(CHECKPOINT_FILE).exists():
        df = pd.read_csv(CHECKPOINT_FILE, dtype=str).fillna("")
        done = set(df["slug"].tolist())
        return done, df.to_dict("records")
    return set(), []


def save_checkpoint(rows: list) -> None:
    Path("output").mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(CHECKPOINT_FILE, index=False)


# ── Output writers ────────────────────────────────────────────────────────────
def write_outputs(rows: list, logger: logging.Logger) -> None:
    df = pd.DataFrame(rows)

    # CSV — user-facing columns only
    df[USER_COLS].to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    logger.info(f"CSV  saved → {OUTPUT_CSV}  ({len(df)} rows)")

    # JSON — list of objects, geo as nested object for readability
    json_out = []
    for r in df[USER_COLS].to_dict("records"):
        entry = {
            "slug":            r["slug"],
            "Restaurant_Name": r["Restaurant_Name"],
            "Address":         r["Address"],
            "Contact_No":      r["Contact_No"],
            "Website":         r["Website"],
            "Geo_Coordinates": {
                "lat": r["Geo_Lat"],
                "lng": r["Geo_Lng"],
            },
            "Google_Maps_URL": r["Google_Maps_URL"],
            "status":          r["status"],
        }
        json_out.append(entry)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(json_out, f, ensure_ascii=False, indent=2)
    logger.info(f"JSON saved → {OUTPUT_JSON}  ({len(json_out)} entries)")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    logger = setup_logging()
    Path("output").mkdir(exist_ok=True)

    if not API_KEY:
        logger.error("GOOGLE_API_KEY not set. Create a .env file with your key.")
        sys.exit(1)

    # Load input — one slug per line, UTF-8 BOM safe, no header
    df_input = pd.read_csv(
        INPUT_FILE, header=None, names=["slug"],
        encoding="utf-8-sig",   # strips UTF-8 BOM if present
        skip_blank_lines=True,
    )
    df_input["slug"]   = df_input["slug"].str.strip()
    df_input["row_id"] = range(1, len(df_input) + 1)   # assign sequential IDs
    df_input = df_input.head(MAX_RESTAURANTS)
    logger.info(f"Loaded {len(df_input)} restaurants from {INPUT_FILE}")

    # Resume from checkpoint if available
    done_slugs, rows = load_checkpoint()
    if done_slugs:
        logger.info(f"Resuming — {len(done_slugs)} already fetched")

    pending = df_input[~df_input["slug"].isin(done_slugs)]
    logger.info(f"Querying {len(pending)} remaining restaurants…\n")

    for _, row in tqdm(pending.iterrows(), total=len(pending), desc="Fetching"):
        record = fetch_restaurant(row["slug"], row["row_id"], logger)
        rows.append(record)

        if len(rows) % CHECKPOINT_EVERY == 0:
            save_checkpoint(rows)

        time.sleep(RATE_SLEEP)

    # Final save
    save_checkpoint(rows)
    write_outputs(rows, logger)

    # Summary
    df_out = pd.DataFrame(rows)
    counts = df_out["status"].value_counts()
    found  = counts.get("found", 0) + counts.get("found_broad", 0)
    logger.info(f"\n{'─'*50}")
    logger.info(f"Total processed : {len(df_out)}")
    logger.info(f"Found           : {found}")
    logger.info(f"Not found       : {counts.get('not_found', 0)}")
    logger.info(f"Errors          : {counts.get('error', 0)}")
    logger.info(f"{'─'*50}")
    print(f"\nDone! {found}/{len(df_out)} restaurants found.")


if __name__ == "__main__":
    main()
