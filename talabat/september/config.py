"""Shared configuration for the September discovery cycle.

WHY A NEW FOLDER RATHER THAN RE-RUNNING listing_comparison/.
That package hardcodes "the existing universe" as the RUN-1 files only
(common.py: EXISTING_CONFIRMED = restaurants_confirmed.jsonl). Running it
unchanged in September would report all 7,129 August restaurants as new. The
universe has to grow every month, so it is computed here instead of pinned.

WHAT THE MONTHLY CYCLE IS
    1  build_universe.py   assemble every branch_id we have ever evaluated
    2  probe_zones.py      probe all zones, snapshot them, diff vs last month
    3  (crawl)             listing_scraper/run2_collector.py, seeded from step 1
    4  split_new_listings.py   newly LISTED vs newly FOUND
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # talabat/
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

MONTH = "202609"
PREV_MONTH = "202608"

# ---- where the existing universe lives -----------------------------------
RI = ROOT / "Restaurant Identifier" / "data"
URLS = ROOT / "data" / "urls"

# Shipped deliverables - definitely known.
JULY_EXPORT = ROOT / "export" / "talabat_export.json"
AUGUST_EXPORT = ROOT / "August_menu" / "data" / "August_export.json"

# Raw crawl output - branch_ids we FOUND, whether or not they shipped.
RAW_CRAWLS = [
    URLS / "talabat_restaurant_urls.jsonl",
    URLS / "talabat_restaurant_urls_run2.jsonl",
]

# Identification verdicts. A branch_id here has already been JUDGED, so
# re-discovering it next month wastes identification and classification budget.
IDENTIFIED = [
    RI / "restaurants_confirmed.jsonl",
    RI / "restaurants_confirmed_run2.jsonl",
    RI / "non_restaurants.jsonl",          # judged NOT a restaurant - stay out
    RI / "non_restaurants_run2.jsonl",
]

# ---- the deliberate carve-out --------------------------------------------
# These failed to FETCH. They were never judged, so excluding them would bury
# them permanently. 36 ids across both runs - they are re-offered to the crawler.
RETRY = [
    RI / "failed_urls.jsonl",
    RI / "failed_urls_run2.jsonl",
]

# ---- zone list -----------------------------------------------------------
CITY_URLS = ROOT / "CITY-URLS.csv"

# Snapshots are DATED. map_areas.py writes a fixed area_map.json and would
# overwrite the only baseline we hold, so this cycle never reuses that name.
ZONE_SNAPSHOT = DATA / f"zone_snapshot_{MONTH}.json"
PREV_ZONE_SNAPSHOT = ROOT / "listing_scraper" / "smoke_results" / "area_map.json"
ZONE_DELTA = DATA / f"zone_delta_{MONTH}.json"

UNIVERSE = DATA / f"known_branch_ids_{MONTH}.json"
NEW_SPLIT = DATA / f"new_listings_split_{MONTH}.json"

# ---- Layer 3 threshold ---------------------------------------------------
# branch_id is assigned sequentially by Talabat, verified via restaurant.createdAt
# which decreases strictly as branch_id rises (33/33 samples). So an id above
# everything we have ever seen was listed AFTER our last crawl.
#   above  -> genuinely newly listed
#   within -> existed already, our earlier crawl missed it
# Written by build_universe.py; do not hardcode.
