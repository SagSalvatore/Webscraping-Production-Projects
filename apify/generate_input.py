"""
generate_input.py
-----------------
Generates the Apify actor input JSON for Dubai F&B scraping.
55 Dubai areas x 12 search categories = 660 total queries (full Dubai)

Usage:
    python generate_input.py --dry-run                        # preview, no file written
    python generate_input.py --max-results 60                 # full run, all 55 areas
    python generate_input.py --group premium --max-results 60 # one area group only
    python generate_input.py --group all --max-results 60     # all groups explicitly
    python generate_input.py --skip-done --max-results 60     # skip the 7 already-scraped areas
    python generate_input.py --budget 300                     # recalculate against custom budget

Area groups:
    high_density   12 areas   Old Dubai, Deira sub-areas, Bur Dubai sub-areas
    expat_belts    14 areas   International City, Al Nahda, Al Qusais, JVC, Mirdif...
    premium         9 areas   Downtown, DIFC, Marina, JBR, Palm, City Walk...
    mid_tier       12 areas   JLT, Al Barsha, Barsha Heights, DSO, Motor City...
    industrial      8 areas   Al Quoz x4, DIP x2, Jebel Ali, Ras Al Khor
"""

import json
import argparse
from pathlib import Path

# ── Area Groups ───────────────────────────────────────────────────────────────
# Each entry: (display_name, lat, lng)
# Grouped by density / character so you can batch scrape by priority tier.

HIGH_DENSITY_AREAS = [
    # Old Dubai & Deira sub-areas — massive volume, QSR + FSR + street food
    ("Al Rigga, Deira, Dubai, UAE",            25.2697,  55.3232),
    ("Naif, Deira, Dubai, UAE",                25.2743,  55.3097),
    ("Hor Al Anz, Deira, Dubai, UAE",          25.2800,  55.3282),
    ("Al Muteena, Deira, Dubai, UAE",          25.2635,  55.3163),
    ("Abu Hail, Deira, Dubai, UAE",            25.2822,  55.3361),
    ("Al Karama, Dubai, UAE",                  25.2363,  55.3013),
    ("Al Mankhool, Bur Dubai, Dubai, UAE",     25.2468,  55.2958),
    ("Al Hamriya, Bur Dubai, Dubai, UAE",      25.2533,  55.2867),
    ("Oud Metha, Dubai, UAE",                  25.2333,  55.3091),
    ("Al Rafaa, Bur Dubai, Dubai, UAE",        25.2511,  55.2878),
    ("Al Satwa, Dubai, UAE",                   25.2231,  55.2644),
    ("Al Jafiliya, Dubai, UAE",                25.2278,  55.2883),
]

EXPAT_BELT_AREAS = [
    # High-density expat commuter belts — QSR + delivery heavy
    ("International City Phase 1, Dubai, UAE",         25.1657,  55.4134),
    ("International City China Cluster, Dubai, UAE",   25.1650,  55.4100),
    ("International City France Cluster, Dubai, UAE",  25.1680,  55.4150),
    ("Al Nahda 1, Dubai, UAE",                         25.2857,  55.3750),
    ("Al Nahda 2, Dubai, UAE",                         25.2900,  55.3800),
    ("Al Qusais 1, Dubai, UAE",                        25.2700,  55.3750),
    ("Al Qusais 2, Dubai, UAE",                        25.2750,  55.3700),
    ("Al Qusais 3, Dubai, UAE",                        25.2800,  55.3650),
    ("Jumeirah Village Circle, Dubai, UAE",            25.0598,  55.2016),
    ("Discovery Gardens, Dubai, UAE",                  25.0478,  55.1556),
    ("Al Furjan, Dubai, UAE",                          25.0400,  55.1800),
    ("Mirdif, Dubai, UAE",                             25.2298,  55.4148),
    ("Al Warqa 1, Dubai, UAE",                         25.2100,  55.4000),
    ("Muhaisnah, Dubai, UAE",                          25.2860,  55.3900),
]

PREMIUM_AREAS = [
    # Premium commercial & tourist hubs — FSR + fine dining + high-end bakeries
    ("Downtown Dubai, Dubai, UAE",             25.1972,  55.2744),
    ("Business Bay, Dubai, UAE",               25.1871,  55.2617),
    ("DIFC, Dubai, UAE",                       25.2131,  55.2822),
    ("Dubai Marina, Dubai, UAE",               25.0787,  55.1426),
    ("Jumeirah Beach Residence, Dubai, UAE",   25.0780,  55.1330),
    ("Palm Jumeirah, Dubai, UAE",              25.1124,  55.1390),
    ("Dubai Hills Estate, Dubai, UAE",         25.1166,  55.2409),
    ("City Walk, Dubai, UAE",                  25.2007,  55.2480),
    ("Bluewaters Island, Dubai, UAE",          25.0851,  55.1192),
]

MID_TIER_AREAS = [
    # Mid-tier residential & freezones — mixed FSR, QSR, cafes
    ("Jumeirah Lake Towers Cluster A-M, Dubai, UAE",   25.0688,  55.1552),
    ("Jumeirah Lake Towers Cluster N-Z, Dubai, UAE",   25.0800,  55.1550),
    ("Al Barsha 1, Dubai, UAE",                        25.1006,  55.1999),
    ("Al Barsha 2, Dubai, UAE",                        25.0900,  55.2100),
    ("Barsha Heights, Dubai, UAE",                     25.0890,  55.1758),
    ("Dubai Silicon Oasis, Dubai, UAE",                25.1172,  55.3785),
    ("Motor City, Dubai, UAE",                         25.0559,  55.2427),
    ("Sports City, Dubai, UAE",                        25.0413,  55.2258),
    ("Production City, Dubai, UAE",                    25.0329,  55.1932),
    ("Studio City, Dubai, UAE",                        25.0512,  55.2132),
    ("Arjan, Dubai, UAE",                              25.0586,  55.2265),
    ("Meydan, Dubai, UAE",                             25.1653,  55.3107),
]

INDUSTRIAL_AREAS = [
    # Industrial zones — cloud kitchen capital of Dubai
    ("Al Quoz Industrial Area 1, Dubai, UAE",  25.1380,  55.2250),
    ("Al Quoz Industrial Area 2, Dubai, UAE",  25.1420,  55.2320),
    ("Al Quoz Industrial Area 3, Dubai, UAE",  25.1350,  55.2400),
    ("Al Quoz Industrial Area 4, Dubai, UAE",  25.1381,  55.2303),
    ("Dubai Investment Park 1, Dubai, UAE",    24.9919,  55.1624),
    ("Dubai Investment Park 2, Dubai, UAE",    24.9850,  55.1700),
    ("Jebel Ali Industrial Area, Dubai, UAE",  25.0044,  55.0700),
    ("Ras Al Khor Industrial Area, Dubai, UAE", 25.1975, 55.3586),
]

# All 55 areas combined
AREAS = (
    HIGH_DENSITY_AREAS
    + EXPAT_BELT_AREAS
    + PREMIUM_AREAS
    + MID_TIER_AREAS
    + INDUSTRIAL_AREAS
)

AREA_GROUPS = {
    "high_density": HIGH_DENSITY_AREAS,
    "expat_belts":  EXPAT_BELT_AREAS,
    "premium":      PREMIUM_AREAS,
    "mid_tier":     MID_TIER_AREAS,
    "industrial":   INDUSTRIAL_AREAS,
    "all":          AREAS,
}

# ── Search Categories ─────────────────────────────────────────────────────────
# 12 search terms instead of 4 — covers the full F&B spectrum on Google Maps.
# NOTE: these are the SEARCH STRINGS sent to Google, not CATEGORY_MAP labels.
# Adding more terms here is the ONLY way to discover more venue types.
CATEGORIES = [
    # Core coverage (Run 1 & 2 — already done for 16 areas)
    ("restaurants",                    "restaurant"),
    ("fast food",                      "fast_food"),
    ("cafes and bakeries",             "cafe_bakery"),
    ("food delivery kitchen",          "delivery"),
    # NEW — captures venues that don't rank in generic "restaurants" searches
    ("indian restaurants",             "indian"),        # largest single cuisine in UAE
    ("juice bars and smoothies",       "juice_drinks"),  # Dessert & Drinks not in restaurant results
    ("dessert shops and ice cream",    "dessert"),       # standalone dessert venues
    ("shawarma and kebab",             "shawarma_kebab"),# Middle Eastern QSR not in "fast food"
    ("brunch restaurants",             "brunch"),        # huge Dubai segment, separate search intent
    ("pizza restaurants",              "pizza"),         # large enough to warrant own query
    ("sushi restaurants",              "sushi"),         # fast-growing segment
    ("food trucks",                    "food_truck"),    # emerging / unregistered segment
]

# ── Areas already scraped in Runs 1 & 2 (use --skip-done to exclude) ─────────
# Short area names must exactly match area_name.split(",")[0] from the AREAS list above.
# Currently 7 exact matches between old 16-area list and new 55-area list.
# (Deira sub-areas, Bur Dubai sub-areas, JLT clusters etc. are NEW and will be scraped fresh)
DONE_AREAS = {
    "Downtown Dubai",           # Run 1 — 142 venues
    "Business Bay",             # Run 1 — 111 venues
    "Dubai Marina",             # Run 1 — 93 venues
    "Al Barsha 1",              # Run 2 — 60 venues
    "Al Karama",                # Run 2 — 50 venues
    "Dubai Silicon Oasis",      # Run 2 — 59 venues
    "Motor City",               # Run 2 — 58 venues
}
# NOTE: Even done areas were only scraped with 4 of the 12 search terms.
# Omit --skip-done to re-run them with the 8 new terms to find missing venues.

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_MAX_RESULTS  = 60        # captures beyond top-40 in dense areas
COST_PER_PLACE       = 0.004     # PAY_PER_EVENT rate, same on all Apify plans
DEFAULT_BUDGET       = 0.57      # remaining FREE balance after Runs 1 & 2
OUTPUT_FILE          = Path(__file__).parent / "apify_input.json"


# ── Core functions ─────────────────────────────────────────────────────────────

def build_search_strings(areas=None, categories=None,
                         skip_done: bool = False) -> list[dict]:
    """
    Build one query dict per (area, category) combination.

    skip_done=True  ->  skip the 7 areas fully scraped in Runs 1 & 2.
                        Use this to avoid re-paying for known areas.
                        Omit if you want to run the 8 new search terms
                        on previously scraped areas.
    """
    if areas is None:
        areas = AREAS
    if categories is None:
        categories = CATEGORIES
    queries = []
    for area_name, lat, lng in areas:
        short_area = area_name.split(",")[0]   # "Al Rigga"
        if skip_done and short_area in DONE_AREAS:
            continue
        for search_term, label in categories:
            queries.append({
                "query":    f"{search_term} in {short_area}, Dubai UAE",
                "area":     short_area,
                "category": label,
                "lat":      lat,
                "lng":      lng,
            })
    return queries


def build_actor_input(queries: list[dict], max_results: int) -> dict:
    """Build the Apify actor input payload from a list of query dicts."""
    return {
        "searchStringsArray":        [q["query"] for q in queries],
        "maxCrawledPlacesPerSearch": max_results,
        "language":                  "en",
        "countryCode":               "ae",
        # Cost optimizations — disabled fields not needed for our schema
        "maxReviews":                0,
        "maxImages":                 0,
        "includeOpeningHours":       False,
        "includePeopleAlsoSearch":   False,
        "scrapeDirectories":         False,
        "additionalInfo":            False,
        "includeHistogram":          False,
        # Residential proxy required for Google Maps anti-bot
        "proxyConfig": {
            "useApifyProxy":    True,
            "apifyProxyGroups": ["RESIDENTIAL"],
        },
    }


def estimate_cost(num_queries: int, max_results: int,
                  budget: float = DEFAULT_BUDGET) -> dict:
    """
    Cost estimate for a scrape run.

    PAY_PER_EVENT pricing — $0.004/place on ALL Apify plans.
    Plans (Starter, Scale, Business) do NOT reduce this per-place rate.
    Plans only affect: concurrent runs, proxy allocation, CU rate.
    """
    DEDUP_RATE      = 0.28           # ~28% cross-query overlap (12 terms more overlap than 4)
    gross           = num_queries * max_results
    unique_est      = int(gross * (1 - DEDUP_RATE))
    scrape_cost     = gross * COST_PER_PLACE
    actor_start_fee = 0.0002
    total           = scrape_cost + actor_start_fee
    wall_min        = round((num_queries / 32) * 90 / 60, 1)   # 32 concurrent (Starter)

    return {
        "queries":          num_queries,
        "gross_results":    gross,
        "unique_estimated": unique_est,
        "scrape_cost_usd":  round(scrape_cost, 2),
        "total_cost_usd":   round(total, 2),
        "wall_clock_min":   wall_min,
        "budget_after":     round(budget - total, 2),
    }


def print_budget_scenarios(areas_list, skip_done: bool):
    """Print full budget table for different max_results settings."""
    queries = build_search_strings(areas=areas_list, skip_done=skip_done)
    n = len(queries)
    print()
    print("BUDGET SCENARIOS (what different max_results settings cost):")
    print("-" * 70)
    print(f"  {'max/query':>10}  {'gross':>8}  {'~unique':>8}  {'cost':>8}  {'time':>8}")
    print("-" * 70)
    for mx in [20, 40, 60, 80, 100]:
        e = estimate_cost(n, mx, budget=0)
        print(f"  {mx:>10}  {e['gross_results']:>8,}  {e['unique_estimated']:>8,}"
              f"  ${e['total_cost_usd']:>7.2f}  ~{e['wall_clock_min']:>5.1f}m")
    print("-" * 70)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate Apify actor input for Dubai F&B scraping",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Area groups:
  high_density  (12)  Al Rigga, Naif, Hor Al Anz, Al Muteena, Abu Hail,
                      Al Karama, Al Mankhool, Al Hamriya, Oud Metha, Al Rafaa,
                      Al Satwa, Al Jafiliya
  expat_belts   (14)  International City x3, Al Nahda x2, Al Qusais x3,
                      JVC, Discovery Gardens, Al Furjan, Mirdif, Al Warqa, Muhaisnah
  premium        (9)  Downtown Dubai, Business Bay, DIFC, Dubai Marina, JBR,
                      Palm Jumeirah, Dubai Hills, City Walk, Bluewaters
  mid_tier      (12)  JLT Cluster A-M, JLT Cluster N-Z, Al Barsha 1 & 2,
                      Barsha Heights, DSO, Motor City, Sports City, Production City,
                      Studio City, Arjan, Meydan
  industrial     (8)  Al Quoz 1-4, DIP 1-2, Jebel Ali, Ras Al Khor
        """
    )
    parser.add_argument("--group", default="all",
                        choices=list(AREA_GROUPS.keys()),
                        help="Which area group to generate queries for (default: all)")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS,
                        help=f"Max venues per query (default: {DEFAULT_MAX_RESULTS})")
    parser.add_argument("--skip-done", action="store_true",
                        help="Skip the 7 areas fully scraped in Runs 1 & 2")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET,
                        help=f"Starting credit balance for cost display (default: ${DEFAULT_BUDGET})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview queries and cost — do NOT write files")
    args = parser.parse_args()

    selected_areas = AREA_GROUPS[args.group]
    queries        = build_search_strings(areas=selected_areas, skip_done=args.skip_done)
    actor_input    = build_actor_input(queries, args.max_results)
    cost_est       = estimate_cost(len(queries), args.max_results, budget=args.budget)
    num_areas      = len({q["area"] for q in queries})

    # ── Summary ────────────────────────────────────────────────────────────────
    print("=" * 70)
    print("APIFY INPUT SUMMARY")
    print("=" * 70)
    skipped = len(DONE_AREAS & {q["area"] for a,_,_ in selected_areas
                                 for q in [{"area": a.split(",")[0]}]}) if args.skip_done else 0
    print(f"  Group              : {args.group}  ({len(selected_areas)} areas total)")
    if args.skip_done:
        print(f"  Skipping done      : {len(DONE_AREAS)} areas already in database")
    print(f"  Areas queried      : {num_areas}")
    print(f"  Search terms       : {len(CATEGORIES)}")
    print(f"  Total queries      : {cost_est['queries']:,}")
    print(f"  Max per query      : {args.max_results}")
    print(f"  Gross est.         : {cost_est['gross_results']:,} venues")
    print(f"  Unique est.        : {cost_est['unique_estimated']:,} venues  (~28% dedup)")
    print()
    print("COST  (PAY_PER_EVENT: $0.004/place — same on ALL Apify plans)")
    print("-" * 70)
    print(f"  Scraping cost      : {cost_est['gross_results']:,} x $0.004 = ${cost_est['scrape_cost_usd']:.2f}")
    print(f"  Actor start fee    :                              ~$0.00")
    print(f"  TOTAL THIS RUN     :                              ${cost_est['total_cost_usd']:.2f}")
    print(f"  Starting balance   :                              ${args.budget:.2f}")
    print(f"  Balance after run  :                              ${cost_est['budget_after']:.2f}")
    print(f"  Wall-clock time    : ~{cost_est['wall_clock_min']:.1f} min  (32 concurrent, Starter plan)")
    print()

    if cost_est["budget_after"] < 0:
        shortfall = abs(cost_est["budget_after"])
        print(f"  NEED TO TOP UP: ${shortfall:.2f} more credits required")
    elif cost_est["budget_after"] < 1.00:
        print(f"  WARNING: Less than $1.00 buffer remaining after this run")
    else:
        print(f"  Budget OK - safe to proceed")
    print("=" * 70)

    print_budget_scenarios(selected_areas, args.skip_done)

    # ── Show search queries sample ─────────────────────────────────────────────
    print(f"\nSEARCH QUERIES (first 12 shown — one full area):")
    for i, q in enumerate(queries[:12]):
        print(f"  {i+1:>3}. {q['query']}")
    if len(queries) > 12:
        print(f"  ... and {len(queries) - 12} more")

    if args.dry_run:
        print("\n[DRY RUN] - no files written.")
        return

    # ── Write files ────────────────────────────────────────────────────────────
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(actor_input, f, indent=2, ensure_ascii=False)
    print(f"\nWritten: {OUTPUT_FILE}  ({len(queries)} queries, max={args.max_results})")

    # Full 55-area metadata always written (used for area-name lookup in process_results.py)
    full_queries = build_search_strings(skip_done=False)
    meta_file    = OUTPUT_FILE.parent / "query_metadata.json"
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(full_queries, f, indent=2, ensure_ascii=False)
    print(f"Written: {meta_file}  ({len(full_queries)} entries — all 55 areas x 12 terms)")


if __name__ == "__main__":
    main()
