"""
process_menu_results.py
-----------------------
Flatten raw Apify JSON datasets into a clean CSV with target columns:
  - restaurant_name, restaurant_slug, restaurant_url
  - item_name, item_category, item_description, item_price_aed

Maps scraped brand menus back to original branch URLs by matching candidate brand slugs.
Optimized to handle prefix matching and strip locations correctly.
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import csv

import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
RAW_DIR = BASE_DIR / "output" / "raw"
OUTPUT_DIR = BASE_DIR / "output"
URLS_CSV = BASE_DIR / "urls.csv"

# Target columns
TARGET_COLUMNS = [
    "restaurant_name",
    "restaurant_slug",
    "restaurant_url",
    "cuisine",
    "rating",
    "total_ratings",
    "latitude",
    "longitude",
    "country",
    "item_name",
    "item_category",
    "item_description",
    "item_price_aed",
    "item_old_price_aed",
    "item_image",
    "item_has_choices",
    "scraped_at",
]

# Suffixes used for candidate brand slug extraction (must match run_talabat_menu.py)
LOCATION_SUFFIXES = {
    "madinat", "khalifa", "mbz", "city", "tgo", "nahyan", "markaziyah", "khalidiyah", "zahiya", "dafrah", "muroor",
    "mushrif", "buteen", "reem", "yas", "saadiyat", "falah", "shamkha", "shahama", "ain", "rowdah",
    "quoz", "industrial", "area", "bay", "rigga", "dalma", "mall", "motor", "forsan", "village",
    "rak", "dso", "dsodubai", "raffa", "jumeirah", "barsha", "south", "muhaisnah", "road",
    "szr", "waterfront", "jlt", "marina", "karama", "deira", "satwa", "hosn", "difc",
    "garhoud", "wasl", "muteena", "mina", "jaddaf", "greens", "lakes", "springs",
    "meadows", "ranches", "sports", "studio", "production", "impz", "furjan", "discovery",
    "gardens", "jebel", "ali", "dip", "warqa", "mirdif", "international", "khawaneej",
    "meydan", "sheba", "hamar", "twash", "mizhar", "quasais", "nahda", "anz", "mamzar",
    "sharjah", "ajman", "fujairah", "hili", "al", "and", "the", "by", "of", "in", "at",
    "towers", "tower", "building", "plaza", "walk", "beach", "creek", "harbour", "hills",
    "valley", "ranch", "dunes", "oasis", "gate", "palace", "heights", "estates",
    "community", "town", "square", "park", "boulevard", "avenue",
    "dubai", "abudhabi", "abu-dhabi", "alain", "al-ain", "fujairah", "uaq", "jbr", "jvc",
    "jvt", "tecom", "uae", "gcc", "silicon-oasis"
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("process_menu")


# ---------------------------------------------------------------------------
# Candidate Generation
# ---------------------------------------------------------------------------
def extract_slug(url: str) -> str:
    path = urlparse(url.strip()).path.rstrip("/")
    return path.split("/")[-1]


def extract_country(url: str) -> str:
    parts = urlparse(url.strip()).path.strip("/").split("/")
    return parts[0].lower() if parts else "uae"


def extract_brand_slug(slug: str) -> str:
    parts = slug.lower().split("-")
    parts = [p for p in parts if p]
    orig_parts = list(parts)
    while parts:
        last = parts[-1]
        if last.isdigit() or len(last) == 1 or last in LOCATION_SUFFIXES:
            parts.pop()
        else:
            break
    if not parts:
        if len(orig_parts) >= 2 and orig_parts[0] in {"al", "the", "el", "la"}:
            return "-".join(orig_parts[:2])
        return orig_parts[0] if orig_parts else slug
    return "-".join(parts)


def generate_candidates(url: str) -> list[str]:
    slug = extract_slug(url)
    brand = extract_brand_slug(slug)
    parts = brand.split("-")
    candidates = set()
    for i in range(len(parts), 1, -1):
        candidates.add("-".join(parts[:i]))
    if not candidates:
        candidates.add(brand)
    return sorted(list(candidates), key=len, reverse=True)


# ---------------------------------------------------------------------------
# URL Loading
# ---------------------------------------------------------------------------
def load_urls(csv_path: Path) -> list[str]:
    urls = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if row:
                u = row[0].strip().strip("\r\n")
                if u.startswith("http"):
                    urls.append(u)
    return urls


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------
def build_scraped_index(input_files: list[Path]) -> dict[str, dict]:
    """Loads all scraped brand results from raw JSON files and indexes them by slug."""
    index = {}
    for fp in sorted(input_files):
        with open(fp, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except Exception as e:
                print(f"Error loading {fp}: {e}")
                continue

            if not isinstance(data, list):
                data = [data]

            for item in data:
                slug = item.get("slug")
                if slug:
                    # If multiple items exist for same slug, keep the one with menu items
                    if slug not in index or len(item.get("menu_items", [])) > len(index[slug].get("menu_items", [])):
                        index[slug] = item
    return index


def flatten_restaurant(url: str, brand_data: dict | None, slug: str) -> list[dict]:
    """Flatten a single restaurant record into rows -- one per menu item."""
    # Convert slug to default fallback name
    fallback_name = slug.replace("-", " ").title()

    base = {
        "restaurant_name": brand_data.get("name", fallback_name) if brand_data else fallback_name,
        "restaurant_slug": slug,
        "restaurant_url": url,
        "cuisine": brand_data.get("cuisine", "") if brand_data else "",
        "rating": brand_data.get("rating") if brand_data else None,
        "total_ratings": brand_data.get("total_ratings") if brand_data else None,
        "latitude": brand_data.get("latitude") if brand_data else None,
        "longitude": brand_data.get("longitude") if brand_data else None,
        "country": brand_data.get("country", "") if brand_data else "",
        "scraped_at": brand_data.get("scraped_at", "") if brand_data else "",
    }

    menu_items = brand_data.get("menu_items") if brand_data else None
    if not menu_items:
        row = {**base}
        row["item_name"] = None
        row["item_category"] = None
        row["item_description"] = None
        row["item_price_aed"] = None
        row["item_old_price_aed"] = None
        row["item_image"] = None
        row["item_has_choices"] = None
        return [row]

    rows = []
    for item in menu_items:
        row = {**base}
        row["item_name"] = item.get("name", "")
        row["item_category"] = item.get("category", "")
        row["item_description"] = item.get("description", "")
        row["item_price_aed"] = item.get("price")
        row["item_old_price_aed"] = item.get("old_price")
        row["item_image"] = item.get("image", "")
        row["item_has_choices"] = item.get("has_choices", False)
        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    logger = setup_logging()

    parser = argparse.ArgumentParser(
        description="Process Talabat menu scrape results into final CSV by mapping brand candidates"
    )
    parser.add_argument(
        "--input", nargs="+", metavar="FILE",
        help="Specific raw JSON file(s) to process (default: all in output/raw/)"
    )
    parser.add_argument(
        "--output", metavar="FILE",
        help="Output CSV filename (default: talabat_menu_YYYYMMDD.csv)"
    )
    parser.add_argument(
        "--no-dedup", action="store_true",
        help="Skip deduplication of menu items"
    )
    args = parser.parse_args()

    # -- Collect input files ---------------------------------------------------
    if args.input:
        input_files = [Path(f) for f in args.input]
    else:
        if not RAW_DIR.exists():
            logger.error(f"No raw data directory found: {RAW_DIR}")
            logger.info("Run the scraper first: py run_talabat_menu.py")
            sys.exit(1)
        input_files = list(RAW_DIR.glob("*.json"))
        if not input_files:
            logger.error(f"No JSON files found in {RAW_DIR}")
            sys.exit(1)

    logger.info(f"Processing {len(input_files)} raw JSON file(s)...")

    # Load and index all scraped brand data
    scraped_index = build_scraped_index(input_files)
    logger.info(f"Loaded {len(scraped_index)} unique brand menus from scraped files")

    # Load original URLs from CSV
    urls = load_urls(URLS_CSV)
    logger.info(f"Loaded {len(urls)} target branch URLs from {URLS_CSV.name}")

    all_rows = []
    success_count = 0
    failure_count = 0

    for url in urls:
        slug = extract_slug(url)
        candidates = generate_candidates(url)

        # Match the longest candidate that was successfully scraped
        matched_data = None
        for cand in candidates:
            if cand in scraped_index:
                item = scraped_index[cand]
                # Prefer matches that have menu items
                if len(item.get("menu_items", [])) > 0:
                    matched_data = item
                    break
                elif not matched_data:
                    matched_data = item

        if matched_data:
            success_count += 1
        else:
            failure_count += 1

        rows = flatten_restaurant(url, matched_data, slug)
        all_rows.extend(rows)

    df = pd.DataFrame(all_rows, columns=TARGET_COLUMNS)

    logger.info(f"Mapping success rate: {success_count}/{len(urls)} ({success_count/len(urls)*100:.1f}%)")
    logger.info(f"Mapping failed      : {failure_count}/{len(urls)}")
    logger.info(f"Menu items (raw)    : {len(df)}")

    # -- Dedup -----------------------------------------------------------------
    if not args.no_dedup:
        before = len(df)
        # Deduplicate by (restaurant_slug, item_name, item_category)
        df = df.drop_duplicates(
            subset=["restaurant_slug", "item_name", "item_category"],
            keep="first"
        )
        removed = before - len(df)
        if removed > 0:
            logger.info(f"Duplicates removed  : {removed}")

    logger.info(f"Menu items (final)  : {len(df)}")

    # -- Price stats -----------------------------------------------------------
    has_menu = df[df["item_name"].notna()]
    prices = pd.to_numeric(has_menu["item_price_aed"], errors="coerce")
    valid_prices = prices.dropna()
    if len(valid_prices) > 0:
        logger.info(f"Price range (AED)   : {valid_prices.min():.2f} - {valid_prices.max():.2f}")
        logger.info(f"Average price (AED) : {valid_prices.mean():.2f}")
        logger.info(f"Median price (AED)  : {valid_prices.median():.2f}")

    # -- Save CSV --------------------------------------------------------------
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.output:
        out_path = OUTPUT_DIR / args.output
    else:
        ts = datetime.now().strftime("%Y%m%d")
        out_path = OUTPUT_DIR / f"talabat_menu_{ts}.csv"

    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info(f"\nCSV saved to: {out_path}")
    logger.info(f"File size: {out_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
