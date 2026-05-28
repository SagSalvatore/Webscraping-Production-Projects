"""
run_scraper.py
--------------
Triggers the Apify `compass/crawler-google-places` actor, polls for completion,
then downloads the raw dataset to output/raw/.

Compatible with apify-client v3.0.1+ (uses Pydantic model attribute access,
NOT dict-style access â€” Run.start() and RunClient.get() both return Run models).

Usage:
    python run_scraper.py               # full run
    python run_scraper.py --dry-run     # validate input, don't spend credits
    python run_scraper.py --download-only RUN_ID   # re-download a previous run

Requires:
    APIFY_API_TOKEN in the repository .env file
"""

import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime
from pathlib import Path

from apify_client import ApifyClient
from dotenv import load_dotenv

# -- Load .env from repository root --------------------------------------------
load_dotenv(Path(__file__).parent.parent / ".env")

API_TOKEN    = os.getenv("APIFY_API_TOKEN", "")
ACTOR_ID     = "compass/crawler-google-places"
INPUT_FILE   = Path(__file__).parent / "apify_input.json"
OUTPUT_DIR   = Path(__file__).parent / "output" / "raw"
LOG_DIR      = Path(__file__).parent / "output" / "logs"

POLL_INTERVAL_SEC = 30      # check run status every 30 seconds
MAX_WAIT_MIN      = 90      # bail out after 90 minutes if not done

# Pricing (PAY_PER_EVENT, FREE tier)
COST_PER_PLACE = 0.004      # $0.004 per place scraped


# -- Logging -------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"apify_run_{ts}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("apify_runner")


# -- Helpers -------------------------------------------------------------------
def load_input() -> dict:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"{INPUT_FILE} not found. Run generate_input.py first."
        )
    with open(INPUT_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_dataset(items: list, run_id: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = OUTPUT_DIR / f"dataset_{ts}_{run_id[:8]}.json"
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    return fname


def format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


def get_items_count(run_info) -> int:
    """
    Extract number of items scraped from a Run model.

    For PAY_PER_EVENT actors, charged_event_counts holds the per-event
    breakdown (e.g. {"ACTOR_TASK_PLACE_SCRAPED": 387}).
    Fall back to 0 if the field isn't populated yet (mid-run).
    """
    try:
        counts = run_info.charged_event_counts
        if counts and isinstance(counts, dict):
            return sum(v for v in counts.values() if isinstance(v, (int, float)))
    except AttributeError:
        pass
    return 0


def get_dataset_id(run_info) -> str:
    """
    Return the default dataset ID from a Run model.
    Handles both snake_case attribute (v3 Pydantic model) and
    camelCase key (dict fallback for older clients).
    """
    # Pydantic model attribute (apify-client v3+)
    val = getattr(run_info, "default_dataset_id", None)
    if val:
        return val
    # Dict fallback
    if isinstance(run_info, dict):
        return run_info.get("defaultDatasetId", "")
    return ""


# -- Main ----------------------------------------------------------------------
def run(dry_run: bool = False, download_only: str | None = None):
    logger = setup_logging()

    # Validate token
    if not API_TOKEN:
        logger.error(
            "APIFY_API_TOKEN not set. Add it to the repository .env file.\n"
            "  APIFY_API_TOKEN=<your-token>\n"
            "Get your token at: https://console.apify.com/settings/integrations"
        )
        sys.exit(1)

    client = ApifyClient(API_TOKEN)

    # -- Download-only mode ----------------------------------------------------
    if download_only:
        logger.info(f"Download-only mode for run ID: {download_only}")
        run_info   = client.run(download_only).get()
        dataset_id = get_dataset_id(run_info)
        if not dataset_id:
            logger.error("Could not determine dataset ID from run info.")
            sys.exit(1)
        items = list(client.dataset(dataset_id).iterate_items())
        saved = save_dataset(items, download_only)
        logger.info(f"Downloaded {len(items):,} items -> {saved}")
        return

    # -- Load + validate input -------------------------------------------------
    actor_input = load_input()
    num_queries = len(actor_input.get("searchStringsArray", []))
    max_results = actor_input.get("maxCrawledPlacesPerSearch", 40)
    est_gross   = num_queries * max_results
    est_cost    = est_gross * COST_PER_PLACE

    logger.info("=" * 60)
    logger.info(f"Actor        : {ACTOR_ID}")
    logger.info(f"Queries      : {num_queries}")
    logger.info(f"Max/query    : {max_results}")
    logger.info(f"Gross est.   : {est_gross:,} places")
    logger.info(f"Cost est.    : ~${est_cost:.4f}  (@$0.004/place PAY_PER_EVENT)")
    logger.info("Token        : configured via environment")
    logger.info("=" * 60)

    if dry_run:
        logger.info("[DRY RUN] Input is valid. No credits spent.")
        logger.info(f"First 3 queries: {actor_input['searchStringsArray'][:3]}")
        return

    # -- Start the actor -------------------------------------------------------
    # .start() returns a Run Pydantic model in apify-client v3 (NOT a dict)
    logger.info("Starting Apify actor run...")
    start_time = time.time()

    run_obj = client.actor(ACTOR_ID).start(run_input=actor_input)
    run_id  = run_obj.id          # attribute access â€” NOT run_obj["id"]
    logger.info(f"Run started  : {run_id}")
    logger.info(f"Console URL  : https://console.apify.com/actors/runs/{run_id}")

    # -- Poll for completion ---------------------------------------------------
    logger.info(f"Polling every {POLL_INTERVAL_SEC}s (max {MAX_WAIT_MIN} min)...")
    deadline = time.time() + MAX_WAIT_MIN * 60
    run_info = run_obj            # seed with the start response

    while True:
        time.sleep(POLL_INTERVAL_SEC)
        elapsed = time.time() - start_time

        # .get() also returns a Run Pydantic model in apify-client v3
        run_info = client.run(run_id).get()
        status   = run_info.status   # attribute access â€” NOT run_info.get("status")
        items_so_far = get_items_count(run_info)

        logger.info(
            f"[{format_duration(elapsed)}]  Status: {status}  "
            f"| Items charged: {items_so_far:,}"
        )

        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break

        if time.time() > deadline:
            logger.error(f"Timeout after {MAX_WAIT_MIN} minutes. Aborting poll.")
            logger.info("Run may still be active. Re-download with:")
            logger.info(f"  python run_scraper.py --download-only {run_id}")
            sys.exit(1)

    elapsed = time.time() - start_time

    if status != "SUCCEEDED":
        logger.error(f"Run ended with status: {status} after {format_duration(elapsed)}")
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            # Partial results may still exist â€” try to download them
            dataset_id = get_dataset_id(run_info)
            if dataset_id:
                logger.info("Attempting to download any partial results...")
                items = list(client.dataset(dataset_id).iterate_items())
                if items:
                    saved = save_dataset(items, run_id)
                    logger.info(f"Partial dataset ({len(items):,} items) saved -> {saved}")
                    logger.info("Process partial data with:")
                    logger.info(f"  python process_results.py --input {saved} --skip-seen")
        logger.info(f"Check logs at: https://console.apify.com/actors/runs/{run_id}")
        sys.exit(1)

    # -- Download results ------------------------------------------------------
    dataset_id = get_dataset_id(run_info)
    if not dataset_id:
        logger.error("SUCCEEDED but no dataset ID found in run info.")
        sys.exit(1)

    logger.info(f"Run SUCCEEDED in {format_duration(elapsed)}. Downloading dataset...")
    items = list(client.dataset(dataset_id).iterate_items())
    saved = save_dataset(items, run_id)

    # -- Cost summary ----------------------------------------------------------
    total_items = len(items)
    actual_cost = total_items * COST_PER_PLACE

    # Try to get official cost from the Run model
    official_cost = getattr(run_info, "usage_total_usd", None)
    stats         = getattr(run_info, "stats", None)
    compute_units = getattr(stats, "compute_units", "N/A") if stats else "N/A"
    run_secs      = getattr(stats, "run_time_secs", None)  if stats else None
    run_duration  = format_duration(run_secs) if run_secs else format_duration(elapsed)

    logger.info("=" * 60)
    logger.info(f"Items scraped   : {total_items:,}")
    logger.info(f"Compute units   : {compute_units}")
    logger.info(f"Run time        : {run_duration}")
    if official_cost is not None:
        logger.info(f"Official cost   : ${official_cost:.4f}  (from Apify)")
    else:
        logger.info(f"Estimated cost  : ~${actual_cost:.4f}  ({total_items} x $0.004)")
    logger.info(f"Raw saved to    : {saved}")
    logger.info("=" * 60)
    logger.info("Next step: python process_results.py --skip-seen")


def main():
    parser = argparse.ArgumentParser(description="Run Apify Google Maps scraper")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate input without spending credits")
    parser.add_argument("--download-only", metavar="RUN_ID",
                        help="Skip actor run -- just download an existing run's dataset")
    args = parser.parse_args()
    run(dry_run=args.dry_run, download_only=args.download_only)


if __name__ == "__main__":
    main()


