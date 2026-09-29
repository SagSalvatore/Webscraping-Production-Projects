"""
run_talabat_menu.py
-------------------
Scrape Talabat restaurant menus using Apify `thirdwatch/talabat-scraper`.

Strategy:
  - Extract brand-level slug from each UAE branch URL (strip location suffix)
  - Use `restaurantSlugs` input field — this directly navigates to the right page
  - Deduplicate by brand slug (multi-branch brands scraped once, not N times)
  - Map brand slug back to all source URLs for traceability

Why NOT `queries`:
  Talabat's search is fuzzy. "zam zam mandi madinat khalifa" returned
  Johnny Rockets — completely wrong. vendor_id matching confirmed 0/5 correct.

Why `restaurantSlugs` works:
  Talabat UAE brand URLs: talabat.com/uae/{brand-slug}
  Actor navigates directly to these pages → correct restaurant, correct menu.
  Test (5 URLs): 4/5 had menu items; 3/5 exact vendor_id match.

URL anatomy:
  https://www.talabat.com/uae/restaurant/748535/zam-zam-mandi-madinat-khalifa--a?aid=6484
                                                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                                 branch slug → strip location → zam-zam-mandi (brand slug)

Budget (FREE tier $0.002/result):
  1000 branch URLs → ~700-800 unique brand slugs → ~$1.40-$1.60 total

Usage:
    py run_talabat_menu.py --test 10       # test: first 10 URLs
    py run_talabat_menu.py --dry-run       # show brand slugs, estimate cost
    py run_talabat_menu.py --reset         # clear progress and start fresh
    py run_talabat_menu.py                 # full run (all 1000 URLs)
    py run_talabat_menu.py --start-from 3  # resume from batch 3
    py run_talabat_menu.py --batch-size 50 # slugs per Apify run (default: 50)
"""

import os
import sys
import json
import time
import csv
import re
import argparse
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from apify_client import ApifyClient
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR      = Path(__file__).parent
PROJECT_ROOT  = BASE_DIR.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

API_TOKEN     = os.getenv("APIFY_API_TOKEN", "")
ACTOR_ID      = "thirdwatch/talabat-scraper"
URLS_CSV      = BASE_DIR / "urls.csv"
RAW_DIR       = BASE_DIR / "output" / "raw"
LOG_DIR       = BASE_DIR / "output" / "logs"
PROGRESS_FILE = BASE_DIR / "output" / "progress.json"
NO_MENU_FILE  = BASE_DIR / "output" / "no_menu_restaurants.json"

POLL_INTERVAL_SEC      = 30
MAX_WAIT_PER_BATCH_MIN = 30
COST_PER_RESULT        = 0.002

# Location words to strip from the end of branch slugs to get brand slugs.
# Only applied from the RIGHT side — restaurant name words on the LEFT are kept.
LOCATION_SUFFIXES = {
    # UAE areas / neighbourhoods
    "madinat", "khalifa", "mbz", "tgo", "nahyan", "markaziyah",
    "khalidiyah", "zahiya", "dafrah", "muroor", "mushrif", "buteen",
    "reem", "yas", "saadiyat", "falah", "shamkha", "shahama", "rowdah",
    "quoz", "industrial", "bay", "rigga", "dalma", "motor", "forsan",
    "dso", "dsodubai", "raffa", "jumeirah", "barsha", "muhaisnah", "szr",
    "waterfront", "jlt", "marina", "karama", "deira", "satwa", "hosn",
    "difc", "garhoud", "wasl", "muteena", "mina", "jaddaf", "greens",
    "lakes", "springs", "meadows", "ranches", "sports", "studio",
    "production", "impz", "furjan", "discovery", "gardens", "jebel",
    "ali", "dip", "warqa", "mirdif", "khawaneej", "meydan",
    "mizhar", "quasais", "nahda", "anz", "mamzar",
    # Cities
    "dubai", "sharjah", "ajman", "fujairah", "alain", "uaq", "rak",
    # Generic location descriptors
    "mall", "village", "towers", "tower", "building", "plaza", "walk",
    "beach", "creek", "harbour", "hills", "valley", "ranch", "dunes",
    "oasis", "silicon", "gate", "palace", "heights", "estates", "community",
    "town", "square", "park", "boulevard", "avenue", "road", "area",
    "centre", "center", "city", "south", "north", "east", "west",
    "business", "free", "zone",
    # UAE-specific person/area names that appear in branch location suffixes
    "sheikh", "zayed", "bin", "mohammed", "rashid", "maktoum",
    # Common stop-words used as separators in branch naming
    "al", "the", "and",
    # Numbers and short suffixes
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
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"talabat_menu_{ts}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("talabat_menu")


def _fmt(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    return f"{m}m {s}s"


# ---------------------------------------------------------------------------
# Brand slug extraction
# ---------------------------------------------------------------------------
def extract_branch_slug(url: str) -> str:
    """Last path segment of the URL (no query string)."""
    return urlparse(url.strip()).path.rstrip("/").split("/")[-1]


def extract_country(url: str) -> str:
    parts = urlparse(url.strip()).path.strip("/").split("/")
    return parts[0].lower() if parts else "uae"


def branch_to_brand_slug(branch_slug: str) -> str:
    """
    Strip location/branch suffix words from the right of a branch slug
    to get a brand-level slug that matches talabat.com/uae/{brand-slug}.

    Examples:
      zam-zam-mandi-madinat-khalifa--a  →  zam-zam-mandi
      zahrat-lebnan-mbz-city-tgo        →  zahrat-lebnan
      wet-black-burger-business-bay     →  wet-black-burger
      wejdan-cafe-restaurant-al-rigga   →  wejdan-cafe-restaurant
    """
    # Strip trailing branch marker like --a, --b, --1
    slug = re.sub(r"--[a-z0-9]+$", "", branch_slug.lower())
    parts = [p for p in slug.split("-") if p]

    while parts:
        last = parts[-1]
        if last.isdigit() or len(last) == 1 or last in LOCATION_SUFFIXES:
            parts.pop()
        else:
            break

    # Fallback: keep at least the first two parts
    if not parts:
        original = [p for p in branch_slug.lower().split("-") if p]
        parts = original[:2] if len(original) >= 2 else original

    return "-".join(parts)


# ---------------------------------------------------------------------------
# URL loading
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
# Progress tracking
# ---------------------------------------------------------------------------
def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed_batches": [], "run_ids": {}, "total_scraped": 0, "total_no_menu": 0}


def save_progress(p: dict):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2, ensure_ascii=False)


def reset_progress(logger):
    for f in [PROGRESS_FILE, NO_MENU_FILE]:
        if f.exists():
            f.unlink()
    logger.info("Progress cleared.")


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------
def _get_dataset_id(run_info) -> str:
    val = getattr(run_info, "default_dataset_id", None)
    if val:
        return val
    if isinstance(run_info, dict):
        return run_info.get("defaultDatasetId", "")
    return ""


def save_batch_json(items: list, batch_num: int, run_id: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RAW_DIR / f"batch_{batch_num:03d}_{ts}_{run_id[:8]}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    return path


def append_no_menu(items: list):
    existing = []
    if NO_MENU_FILE.exists():
        with open(NO_MENU_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    existing.extend(
        {"name": r.get("name"), "slug": r.get("slug"), "url": r.get("url")}
        for r in items
    )
    NO_MENU_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(NO_MENU_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(
    dry_run:    bool = False,
    batch_size: int  = 50,
    start_from: int  = 0,
    test_count: int  = 0,
    reset:      bool = False,
):
    logger = setup_logging()

    if not API_TOKEN:
        logger.error("APIFY_API_TOKEN not set. Add to Google_Place/.env")
        sys.exit(1)

    if reset:
        reset_progress(logger)

    # -- Load URLs ---------------------------------------------------------------
    urls = load_urls(URLS_CSV)
    logger.info(f"Loaded {len(urls):,} URLs from {URLS_CSV.name}")

    if test_count > 0:
        urls = urls[:test_count]
        logger.info(f"TEST MODE: first {test_count} URLs")

    primary_country = "uae"

    # -- Build brand-slug → [source_urls] mapping --------------------------------
    # Multiple branch URLs may resolve to the same brand slug — deduplicate.
    brand_slug_to_sources: dict[str, list[str]] = {}
    for url in urls:
        branch = extract_branch_slug(url)
        brand  = branch_to_brand_slug(branch)
        brand_slug_to_sources.setdefault(brand, []).append(url)

    unique_brand_slugs = list(brand_slug_to_sources.keys())
    dedup_saved = len(urls) - len(unique_brand_slugs)

    logger.info(f"Unique brand slugs : {len(unique_brand_slugs):,}  "
                f"(saved {dedup_saved} duplicate brand scrapes)")

    # -- Batches -----------------------------------------------------------------
    batches = [
        unique_brand_slugs[i: i + batch_size]
        for i in range(0, len(unique_brand_slugs), batch_size)
    ]
    est_cost = len(unique_brand_slugs) * COST_PER_RESULT

    logger.info("=" * 62)
    logger.info(f"Actor          : {ACTOR_ID}")
    logger.info(f"Country        : {primary_country}")
    logger.info(f"Input URLs     : {len(urls):,}")
    logger.info(f"Unique brands  : {len(unique_brand_slugs):,}")
    logger.info(f"Batch size     : {batch_size} slugs/run")
    logger.info(f"Total batches  : {len(batches)}")
    logger.info(f"scrapeMenu     : True")
    logger.info(f"Cost estimate  : ~${est_cost:.2f}  (@${COST_PER_RESULT}/result)")
    logger.info(f"Token          : {API_TOKEN[:16]}...{API_TOKEN[-4:]}")
    logger.info("=" * 62)

    # Sample preview
    logger.info("Sample brand slugs (first 5):")
    for slug in unique_brand_slugs[:5]:
        sources = brand_slug_to_sources[slug]
        logger.info(f"  {slug}  ← {len(sources)} source URL(s)")

    if dry_run:
        logger.info("\n[DRY RUN] No credits spent.")
        preview = BASE_DIR / "output" / "brand_slug_preview.txt"
        preview.parent.mkdir(parents=True, exist_ok=True)
        with open(preview, "w", encoding="utf-8") as f:
            f.write("brand_slug\tsource_urls\n")
            for slug, srcs in brand_slug_to_sources.items():
                f.write(f"{slug}\t{' | '.join(srcs)}\n")
        logger.info(f"Preview written: {preview}")
        return

    # -- Run batches -------------------------------------------------------------
    client   = ApifyClient(API_TOKEN)
    progress = load_progress()
    total_scraped  = 0
    total_no_menu  = 0
    total_cost     = 0.0

    for batch_idx, batch_slugs in enumerate(batches):
        batch_num = batch_idx + 1

        if batch_num < start_from:
            logger.info(f"Skipping batch {batch_num}/{len(batches)} (start_from={start_from})")
            continue
        if batch_num in progress["completed_batches"]:
            logger.info(f"Skipping batch {batch_num}/{len(batches)} (already done)")
            continue

        logger.info(f"\n{'=' * 62}")
        logger.info(f"BATCH {batch_num}/{len(batches)}  —  {len(batch_slugs)} brand slugs")
        logger.info(f"{'=' * 62}")
        logger.info(f"Slugs: {batch_slugs[:4]}{'...' if len(batch_slugs) > 4 else ''}")

        actor_input = {
            "restaurantSlugs":  batch_slugs,
            "country":          primary_country,
            "scrapeMenu":       True,
            "scrapeMenuChoices": False,
        }

        # Start
        start_t = time.time()
        run_obj = client.actor(ACTOR_ID).start(run_input=actor_input)
        run_id  = run_obj.id
        logger.info(f"Run ID  : {run_id}")
        logger.info(f"Console : https://console.apify.com/actors/runs/{run_id}")

        # Poll
        deadline = time.time() + MAX_WAIT_PER_BATCH_MIN * 60
        run_info = run_obj
        while True:
            time.sleep(POLL_INTERVAL_SEC)
            run_info = client.run(run_id).get()
            status   = run_info.status
            logger.info(f"  [{_fmt(time.time() - start_t)}]  status={status}")
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break
            if time.time() > deadline:
                logger.error(f"Batch {batch_num} timed out after {MAX_WAIT_PER_BATCH_MIN} min")
                break

        # Download
        dataset_id = _get_dataset_id(run_info)
        if not dataset_id:
            logger.error(f"Batch {batch_num}: no dataset. status={status}")
            continue

        items = list(client.dataset(dataset_id).iterate_items())
        batch_cost = len(items) * COST_PER_RESULT
        total_cost += batch_cost

        with_menu    = [r for r in items if r.get("menu_items")]
        without_menu = [r for r in items if not r.get("menu_items")]

        logger.info(f"  Results      : {len(items)}  (cost ~${batch_cost:.4f})")
        logger.info(f"  With menu    : {len(with_menu)}")
        logger.info(f"  Without menu : {len(without_menu)}")

        # Save raw
        saved = save_batch_json(items, batch_num, run_id)
        logger.info(f"  Saved        : {saved.name}")

        if without_menu:
            append_no_menu(without_menu)
            for r in without_menu:
                logger.warning(f"  No menu: {r.get('name')} ({r.get('url')})")

        total_scraped += len(items)
        total_no_menu += len(without_menu)

        # Update progress
        progress["completed_batches"].append(batch_num)
        progress["run_ids"][str(batch_num)] = run_id
        progress["total_scraped"]  = progress.get("total_scraped", 0)  + len(items)
        progress["total_no_menu"]  = progress.get("total_no_menu", 0)  + len(without_menu)
        save_progress(progress)

    # -- Summary -----------------------------------------------------------------
    menu_rate = (total_scraped - total_no_menu) / total_scraped * 100 if total_scraped else 0
    logger.info("\n" + "=" * 62)
    logger.info("SCRAPING COMPLETE")
    logger.info(f"Total restaurants  : {total_scraped:,}")
    logger.info(f"With menu          : {total_scraped - total_no_menu:,}  ({menu_rate:.1f}%)")
    logger.info(f"No menu (flagged)  : {total_no_menu:,}")
    logger.info(f"Total cost         : ~${total_cost:.4f}")
    logger.info(f"Raw data dir       : {RAW_DIR}")
    if total_no_menu:
        logger.info(f"No-menu list       : {NO_MENU_FILE}")
    logger.info("=" * 62)
    logger.info("Next step:")
    logger.info(f"  py process_menu_results.py")


def main():
    parser = argparse.ArgumentParser(description="Scrape Talabat restaurant menus via Apify")
    parser.add_argument("--dry-run",    action="store_true", help="Estimate cost, no API calls")
    parser.add_argument("--reset",      action="store_true", help="Clear progress and start fresh")
    parser.add_argument("--test",       type=int, default=0, metavar="N",
                        help="Test with first N URLs only")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Brand slugs per Apify run (default: 50)")
    parser.add_argument("--start-from", type=int, default=0,
                        help="Resume from batch N (1-indexed)")
    args = parser.parse_args()

    run(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        start_from=args.start_from,
        test_count=args.test,
        reset=args.reset,
    )


if __name__ == "__main__":
    main()
