"""
run_brand_scraper.py
--------------------
Parallel brand scraper: splits queries into groups, fires each group as a
SEPARATE simultaneous Apify actor run, then merges all results.

Why parallel?
  Sequential: 33 queries  →  ~14 min wall-clock time
  Parallel  : 4 groups     →  ~6 min  (time of slowest group)

Group execution model
  Each group starts one `compass/crawler-google-places` actor run on Apify.
  All groups start at (nearly) the same time.
  A ThreadPoolExecutor thread per group polls its run until SUCCEEDED/FAILED.
  Results from all groups are merged into a single raw JSON for processing.

Usage:
    python run_brand_scraper.py --brand dominos           # full run
    python run_brand_scraper.py --brand dominos --dry-run # validate / cost estimate
    python run_brand_scraper.py --brand kfc               # run KFC if input.json exists

Input:
    brands/<brand>/input.json  — must contain a "groups" dict and
                                 "maxCrawledPlacesPerSearch" key.

Output:
    brands/<brand>/output/raw/dataset_<ts>_<group>_<runid>.json   per group
    brands/<brand>/output/raw/dataset_<ts>_merged_all.json        merged

Requires:
    APIFY_API_TOKEN in ../.env  (one level above this apify/ folder)
"""

import os
import sys
import json
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from apify_client import ApifyClient
from dotenv import load_dotenv

# -- Environment ---------------------------------------------------------------
load_dotenv(Path(__file__).parent.parent / ".env")
API_TOKEN      = os.getenv("APIFY_API_TOKEN", "")
ACTOR_ID       = "compass/crawler-google-places"
COST_PER_PLACE = 0.004   # PAY_PER_EVENT pricing
POLL_INTERVAL  = 30      # seconds between status checks
MAX_WAIT_MIN   = 90      # abort if any group takes longer than this

# -- Helpers -------------------------------------------------------------------

def setup_logging(brand: str) -> logging.Logger:
    log_dir = Path(__file__).parent / "brands" / brand / "output" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"brand_run_{ts}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("brand_scraper")


def load_brand_input(brand: str) -> dict:
    path = Path(__file__).parent / "brands" / brand / "input.json"
    if not path.exists():
        raise FileNotFoundError(f"Brand input not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_raw_dir(brand: str) -> Path:
    d = Path(__file__).parent / "brands" / brand / "output" / "raw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_group_dataset(items: list, brand: str, group: str, run_id: str) -> Path:
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = get_raw_dir(brand) / f"dataset_{ts}_{group}_{run_id[:8]}.json"
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    return fname


def fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


def get_charged(run_info) -> int:
    try:
        c = run_info.charged_event_counts
        if c and isinstance(c, dict):
            return sum(v for v in c.values() if isinstance(v, (int, float)))
    except AttributeError:
        pass
    return 0


def get_dataset_id(run_info) -> str:
    val = getattr(run_info, "default_dataset_id", None)
    if val:
        return val
    if isinstance(run_info, dict):
        return run_info.get("defaultDatasetId", "")
    return ""


# -- Per-group runner (runs in a thread) ---------------------------------------

def run_group(group_name: str, queries: list, actor_config: dict) -> tuple:
    """
    Start one Apify actor run for a query group.  Polls until done.
    Returns (group_name, items_list, run_id, cost_usd).
    Designed to run inside a ThreadPoolExecutor worker.
    """
    logger   = logging.getLogger("brand_scraper")
    client   = ApifyClient(API_TOKEN)
    deadline = time.time() + MAX_WAIT_MIN * 60

    run_input = {
        "searchStringsArray":        queries,
        "maxCrawledPlacesPerSearch": actor_config.get("maxCrawledPlacesPerSearch", 35),
        "language":                  actor_config.get("language", "en"),
        "countryCode":               actor_config.get("countryCode", "ae"),
        "maxReviews":                0,
        "maxImages":                 0,
        "includeOpeningHours":       False,
        "scrapeDirectories":         False,
        "additionalInfo":            False,
        "proxyConfig":               actor_config.get("proxyConfig", {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
        }),
    }

    logger.info(f"[{group_name:12}] Starting {len(queries)} queries ...")
    start_time = time.time()
    run_obj    = client.actor(ACTOR_ID).start(
        run_input=run_input,
        memory_mbytes=1024,   # 1 GB per run → 8 concurrent runs on free-tier (8 GB cap)
    )
    run_id     = run_obj.id
    logger.info(f"[{group_name:12}] Run ID: {run_id}  |  "
                f"https://console.apify.com/actors/runs/{run_id}")

    # -- Poll ------------------------------------------------------------------
    run_info = run_obj
    while True:
        time.sleep(POLL_INTERVAL)
        if time.time() > deadline:
            logger.error(f"[{group_name:12}] Timeout after {MAX_WAIT_MIN} min")
            return group_name, [], run_id, 0.0

        run_info = client.run(run_id).get()
        status   = run_info.status
        charged  = get_charged(run_info)
        elapsed  = time.time() - start_time

        logger.info(f"[{group_name:12}] [{fmt(elapsed)}]  {status}  |  charged: {charged}")

        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break

    elapsed = time.time() - start_time
    if status != "SUCCEEDED":
        logger.error(f"[{group_name:12}] Ended with {status} in {fmt(elapsed)}")
        # Try to salvage partial data
        dataset_id = get_dataset_id(run_info)
        if dataset_id:
            items = list(client.dataset(dataset_id).iterate_items())
            logger.info(f"[{group_name:12}] Salvaged {len(items)} partial items")
            return group_name, items, run_id, 0.0
        return group_name, [], run_id, 0.0

    # -- Download --------------------------------------------------------------
    dataset_id = get_dataset_id(run_info)
    items      = list(client.dataset(dataset_id).iterate_items())
    cost       = getattr(run_info, "usage_total_usd", None) or (len(items) * COST_PER_PLACE)

    logger.info(
        f"[{group_name:12}] DONE in {fmt(elapsed)}  |  "
        f"{len(items)} items  |  cost: ${cost:.4f}"
    )
    return group_name, items, run_id, cost


# -- Main entry point ----------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Parallel Apify brand scraper")
    parser.add_argument(
        "--brand", required=True,
        help="Brand folder name under brands/ (e.g. dominos, kfc)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show cost estimate and query breakdown — spend $0",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Max simultaneous Apify runs (default: 4)",
    )
    parser.add_argument(
        "--groups", nargs="+", metavar="GROUP",
        help="Run only specific groups (e.g. --groups abu_dhabi east). "
             "Default: all groups in input.json.",
    )
    parser.add_argument(
        "--download-runs", nargs="+", metavar="RUN_ID",
        help="Skip new runs — download existing Apify run datasets by ID and save "
             "them to brands/<brand>/output/raw/.  Useful for recovering a crashed run.",
    )
    args   = parser.parse_args()
    brand  = args.brand.lower()
    logger = setup_logging(brand)

    # -- Validate token --------------------------------------------------------
    if not API_TOKEN:
        logger.error(
            "APIFY_API_TOKEN not set.  Add to Google_Place/.env:\n"
            "  APIFY_API_TOKEN=apify_api_XXXX...\n"
        )
        sys.exit(1)

    # -- Download-only mode (recover crashed runs) -----------------------------
    if args.download_runs:
        logger.info(f"Download-only mode: recovering {len(args.download_runs)} run(s)")
        client = ApifyClient(API_TOKEN)
        for run_id in args.download_runs:
            run_info   = client.run(run_id).get()
            dataset_id = get_dataset_id(run_info)
            if not dataset_id:
                logger.error(f"  Run {run_id}: no dataset ID found"); continue
            items = list(client.dataset(dataset_id).iterate_items())
            saved = save_group_dataset(items, brand, "recovered", run_id)
            logger.info(f"  Run {run_id}: {len(items):,} items -> {saved.name}")
        logger.info("Download complete.  Merge files then run process_brand.py")
        return

    # -- Load brand input ------------------------------------------------------
    config = load_brand_input(brand)
    groups = config.get("groups", {})
    if not groups:
        logger.error("'groups' key missing or empty in input.json")
        sys.exit(1)

    # Filter to only requested groups if --groups was passed
    if args.groups:
        requested = set(args.groups)
        unknown   = requested - set(groups.keys())
        if unknown:
            logger.error(f"Unknown group(s): {unknown}  Available: {list(groups.keys())}")
            sys.exit(1)
        groups = {k: v for k, v in groups.items() if k in requested}
        logger.info(f"Running subset: {list(groups.keys())}")

    max_per_search   = config.get("maxCrawledPlacesPerSearch", 35)
    total_queries    = sum(len(q) for q in groups.values())
    max_charges      = total_queries * max_per_search
    budget_ceiling   = max_charges * COST_PER_PLACE

    logger.info("=" * 65)
    logger.info(f"Brand         : {config.get('brand_display', brand.upper())}")
    logger.info(f"Groups        : {len(groups)}  ({', '.join(groups.keys())})")
    logger.info(f"Total queries : {total_queries}")
    logger.info(f"Max/query     : {max_per_search}")
    logger.info(f"Cost ceiling  : ~${budget_ceiling:.2f}  (worst case, actual is much less)")
    logger.info(f"Token         : {API_TOKEN[:16]}...{API_TOKEN[-4:]}")
    logger.info("=" * 65)

    if args.dry_run:
        logger.info("[DRY RUN] No credits will be spent.")
        for g, q in groups.items():
            logger.info(f"  Group '{g}': {len(q)} queries")
            for query in q[:3]:
                logger.info(f"    - {query}")
            if len(q) > 3:
                logger.info(f"    ... and {len(q)-3} more")
        return

    # -- Launch all groups in parallel -----------------------------------------
    logger.info(f"Launching {len(groups)} parallel Apify runs ...")
    wall_start = time.time()

    all_items  : list = []
    total_cost : float = 0.0

    num_workers = min(args.workers, len(groups))
    with ThreadPoolExecutor(max_workers=num_workers) as pool:
        future_map = {
            pool.submit(run_group, g_name, g_queries, config): g_name
            for g_name, g_queries in groups.items()
        }
        for future in as_completed(future_map):
            try:
                g_name, items, run_id, cost = future.result()
            except Exception as exc:
                g_name = future_map[future]
                logger.error(f"[{g_name:12}] Group failed: {exc}")
                continue   # one group failure must NOT stop the others

            total_cost += cost
            if items:
                saved = save_group_dataset(items, brand, g_name, run_id)
                logger.info(
                    f"[{g_name:12}] {len(items):,} items saved -> {saved.name}"
                )
                all_items.extend(items)
            else:
                logger.warning(f"[{g_name:12}] 0 items returned")

    wall_elapsed = time.time() - wall_start

    # -- Save merged dataset ---------------------------------------------------
    if not all_items:
        logger.error("No items collected across all groups.  Nothing saved.")
        sys.exit(1)

    ts           = datetime.now().strftime("%Y%m%d_%H%M%S")
    merged_path  = get_raw_dir(brand) / f"dataset_{ts}_merged_all.json"
    with open(merged_path, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    logger.info("=" * 65)
    logger.info(f"All groups done in  : {fmt(wall_elapsed)}  (parallel wall-clock)")
    logger.info(f"Total raw items     : {len(all_items):,}")
    logger.info(f"Total cost          : ~${total_cost:.4f}")
    logger.info(f"Merged raw file     : {merged_path}")
    logger.info("=" * 65)
    logger.info(f"Next step:")
    logger.info(f"  python process_brand.py --brand {brand} --input {merged_path}")


if __name__ == "__main__":
    main()
