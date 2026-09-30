"""Shared config for the restaurant classification pipeline.

Three columns are produced per restaurant:

    restaurant_type      Full-Service Restaurants | Quick-Service Restaurants
                         | Cafes | Bakery | Cloud Kitchen
    outlet_type          Chain | Independent
    chained_outlet_type  Local Chain | MNC Chain | N/A

`chained_outlet_type` is DERIVED, never returned free-form by the model:
    outlet_type == Independent  ->  "N/A"        (nothing to decide)
    outlet_type == Chain        ->  Local Chain | MNC Chain
The previous run let the model emit it directly and the output carried
whitespace variants ('N/A ' x135, 'Local Chain ' x4). Deriving it removes the
whole class of problem.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _argv_value(flag, default):
    """Read a flag from sys.argv BEFORE the constants below are bound.

    argparse runs inside each stage's main(), far too late - the paths are
    module-level and are already resolved by then. Every stage imports config,
    so this is the one place a cohort switch can take effect for all of them.
    """
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    for a in sys.argv:
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return default


# August is the default so a bare re-run still reproduces the shipped batch.
# Every stage takes `--cohort sep`; nothing is overwritten in place because the
# outputs and caches move with it.
COHORT = _argv_value("--cohort", "aug").lower()
if COHORT not in ("aug", "sep"):
    raise SystemExit(f"--cohort must be aug or sep, got {COHORT!r}")

DATA = HERE / ("data" if COHORT == "aug" else f"data_{COHORT}")
LOGS = HERE / "logs"
DATA.mkdir(exist_ok=True)
LOGS.mkdir(exist_ok=True)

# ---- inputs -------------------------------------------------------------
if COHORT == "aug":
    RESTAURANTS = ROOT / "listing_comparison" / "output" / "talabat_restaurants_shipped.jsonl"
    MENU_ITEMS = ROOT / "August_menu" / "data" / "menu_items_with_std_terms.jsonl"
    # Only the pre-August run existed when this file was first written.
    PRIOR_CLASSIFY = [ROOT / "tavily" / "FINAL_CLASSIFY.csv"]
else:
    RESTAURANTS = ROOT / "september" / "data" / "sept_restaurants_for_classification.jsonl"
    # The FINAL menu file - manual verdicts applied and ingredients conformed -
    # not menu_items_with_std_terms, which is the pre-review stage.
    MENU_ITEMS = ROOT / "september" / "data" / "menus" / "menu_items_final.jsonl"
    # Both prior runs. Order matters: later files win, so August's reviewed
    # verdicts override the older 15,768-row run for any brand in both.
    PRIOR_CLASSIFY = [ROOT / "tavily" / "FINAL_CLASSIFY.csv",
                      HERE / "data" / "restaurants_classified.csv"]

# Tavily and Apify keys live in ONE tracker workbook at the repo root (sheets
# `tavily` / `apify`, columns S.No, Name, Keys) - the same file UK/scripts reads.
# tavily/tav_keys.csv was archived to S3 and pruned in Sept 2026.
TAV_KEYS = ROOT.parent / "TRACKER_TAVILY_KEYS_apify.xlsx"
TAV_KEYS_SHEET = "tavily"

# ---- outputs ------------------------------------------------------------
# Caches live with their cohort's DATA dir. They are keyed by brand name, so
# to let September reuse August's paid lookups for free, point these two at
# August's copies rather than starting empty.
SERPER_CACHE = HERE / "data" / "serper_places_cache.json"
TAVILY_CACHE = HERE / "data" / "tavily_cache.json"
RULES_OUT = DATA / "stage1_rules.json"
PLACES_OUT = DATA / "stage2_places.json"
ENRICHMENT_OUT = DATA / "google_places_enrichment.jsonl"
LLM_CACHE = DATA / "llm_classify_cache.json"
FINAL_OUT = DATA / "restaurants_classified.jsonl"
FINAL_CSV = DATA / "restaurants_classified.csv"
REPORT_OUT = DATA / "classification_report.json"
# Gate-3 MNC claims. They ship as Local Chain; this file is the review queue.
MNC_REVIEW = DATA / "mnc_claims_for_review.csv"

# ---- API ----------------------------------------------------------------
OPENAI_MODEL = "gpt-4.1-mini"      # better than 4o-mini on this task, ~2.7x price
OPENAI_BATCH = 30
OPENAI_CONCURRENCY = 12            # usage tier 2 = 4,300 RPM; nowhere near it
SERPER_URL = "https://google.serper.dev/places"
SERPER_CONCURRENCY = 30            # documented rate limit is 100/s
SERPER_QPS = 40.0

# Query template. MEASURED: "{name} uae restaurant" scored 15/15 on a labelled
# set vs 11/15 for "{name} UAE". Adding the word `restaurant` makes Google
# resolve the BUSINESS rather than word-match, which simultaneously finds real
# branches (Ravi Restaurant 1 -> 7 locations) and removes false positives
# (Sour Mango 6 -> 1). Do not "simplify" this template.
SERPER_QUERY = "{name} uae restaurant"
# Tavily uses the SAME wording, per Sagar, so both sources describe one entity.
TAVILY_QUERY = "{name} uae restaurant"

# ---- matching -----------------------------------------------------------
TITLE_MATCH_MIN = 85              # rapidfuzz token_set_ratio on latin-stripped title
CHAIN_MIN_LOCATIONS = 2           # >=2 distinct UAE locations => Chain

UAE_TOKENS = ("dubai", "abu dhabi", "sharjah", "ajman", "fujairah",
              "ras al khaimah", "umm al quwain", "uae", "united arab emirates",
              "al ain", "dxb")
# Explicit foreign markers. Karak Basha's only exact title match sits in
# BAHRAIN and another result in OMAN - gl=ae biases results, it does not
# restrict them, so this filter is load-bearing.
NON_UAE_TOKENS = ("bahrain", "oman", "saudi", "ksa", "kuwait", "qatar",
                  "india", "pakistan", "egypt", "jordan", "lebanon", "turkey",
                  "united kingdom", "usa", "united states", "iraq", "iran")

# ---- MNC detection ------------------------------------------------------
# THREE independent gates, because no single one is sufficient. Google Maps
# tells us a brand is a Chain; it says NOTHING about whether that chain is
# multinational. That distinction is the weakest link in this pipeline and is
# handled deliberately rather than left to a single model guess.
#
# Gate 1 - VERIFIED SEED (28 brands, 1,989 real UAE locations in
#          mnc_chain_locations). Evidence-backed, not guessed. Expect ~0 hits
#          on a genuinely-new-brand batch.
MNC_SEED_VERIFIED = {
    "kfc", "mcdonalds", "pizza hut", "subway", "starbucks", "costa coffee",
    "burger king", "tim hortons", "dunkin donuts", "dominos pizza",
    "caribou coffee", "arabica", "filli cafe", "popeyes", "albaik",
    "peets coffee", "jollibee", "pickl", "caffe nero", "zaatar w zeit",
    "wendys", "five guys", "shake shack", "german doner kebab",
    "pret a manger", "haagendazs", "new york fries", "burgerfuel",
}
# Gate 2 - KNOWN GLOBAL BRANDS not yet scraped into the DB. Recognisable
#          multinationals that could plausibly appear in a new batch.
MNC_SEED_KNOWN = {
    "texas chicken", "hardees", "papa johns", "taco bell", "wingstop",
    "krispy kreme", "baskin robbins", "cinnabon", "dairy queen", "nandos",
    "wagamama", "pf changs", "chilis", "tgi fridays", "the cheesecake factory",
    "ihop", "applebees", "outback steakhouse", "hard rock cafe", "carls jr",
    "little caesars", "cold stone creamery", "auntie annes", "second cup",
    "gloria jeans", "the coffee bean tea leaf", "paul", "laduree", "pattiserie",
    "eataly", "vapiano", "sushi samba", "zaatar w zeit", "operation falafel",
    "kcal", "juan valdez", "oakberry", "pinkberry", "yo sushi", "leon",
    "burger fuel", "mos burger", "lotteria", "marrybrown", "chowking",
}
MNC_SEED = MNC_SEED_VERIFIED | MNC_SEED_KNOWN

# Gate 3 - FOOTPRINT OUTSIDE THE UAE. Per Sagar: a brand with branches in any
#          other country is an MNC Chain, full stop. "Multinational" is read
#          literally - present in more than one nation.
#              operates ONLY in the UAE          -> Local Chain
#              branches in ANY other country     -> MNC Chain
#          This includes GCC neighbours: a UAE brand that has expanded into
#          Saudi or Kuwait is multinational by this definition.
#          The model reports two FACTS - `operates_outside_uae` and
#          `country_of_origin` - rather than the judgement "is this an MNC".
#          Asking where a brand operates is a lookup; asking whether it counts
#          as multinational is an opinion.
UAE_ORIGIN = {"uae", "united arab emirates", "emirates", "dubai", "abu dhabi"}

# Human-reviewed overrides, applied LAST and beating every gate. Keyed by
# norm_name(brand).
#
# Why this exists: gate 3 asks the model for `operates_outside_uae` as a fact,
# but for an unfamiliar brand it guesses. On the August batch it promoted 10
# brands to MNC, including "Happy Restaurant -> origin Bulgaria" (fabricated)
# and three with origin="unknown" - asserting foreign operations for a brand it
# could not identify, which contradicts the prompt's own "if you have never
# heard of it, it is local" instruction.
# Sagar reviewed all 10 on 2026-08-15: only Kyan Cafe is genuinely an MNC.
#
# Sagar reviewed September's single promotion on 2026-09-10 and rejected it.
# "GRATEFUL CAFE" is Grateful Cafe and Roastry, two Abu Dhabi outlets (SZF and
# Villa, ~47 km apart, both UAE). The model's own reasoning was "Cafe Gratitude
# is a US-based chain with multiple countries" - it had answered about a
# DIFFERENT brand, then reported origin=USA and operates_outside_uae=True as
# facts. Sagar: not prominent, so Local Chain on 2 addresses.
#
# Running tally for gate 3: 11 model MNC promotions across two months, 1 upheld.
CHAIN_TYPE_OVERRIDE = {
    "grateful cafe": "Local Chain",
    "kyan cafe": "MNC Chain",
    "d7": "Local Chain",
    "happy restaurant": "Local Chain",
    "sidra": "Local Chain",
    "mokko": "Local Chain",
    "salman sweets": "Local Chain",
    "chicken hut restaurant": "Local Chain",
    "red palace restaurant": "Local Chain",
}

# ---- stage-1 cuisine rules ---------------------------------------------
BAKERY_CUISINES = {"bakery", "pastries", "cakes", "bread", "croissants",
                   "desserts", "pastries & sweets", "arabic sweets"}
CAFE_CUISINES = {"coffee", "cafe", "tea", "bubble tea", "juices", "juice",
                 "smoothies", "ice cream", "gelato", "milkshake", "shakes",
                 "frozen yogurt", "acai", "matcha"}
# A place tagged Coffee AND Biryani is a restaurant that also sells coffee.
CAFE_BLOCKERS = {"indian", "arabic", "grills", "rice", "biryani", "pizza",
                 "burgers", "seafood", "chinese", "pasta", "steaks", "kebab"}
CLOUD_KITCHEN_NAME = ("cloud kitchen", "dark kitchen", "virtual kitchen",
                      "delivery only", "delivery kitchen", "ghost kitchen")

VALID_TYPE = {"Full-Service Restaurants", "Quick-Service Restaurants",
              "Cafes", "Bakery", "Cloud Kitchen"}
VALID_OUTLET = {"Chain", "Independent"}
VALID_CHAINED = {"Local Chain", "MNC Chain", "N/A"}

# ---- the prompt ---------------------------------------------------------
# Written to Sagar's framing: the CLIENT judges chain status by what they see
# on Google Maps. Talabat branch counts are deliberately de-emphasised -
# Talabat lists 12 of KFC's 247 real UAE locations, so it under-counts badly.
SYSTEM_PROMPT = """\
You are a foodservice market research analyst covering the UAE.
For each restaurant, decide restaurant_type and outlet_type.

=========================  OUTLET TYPE  =========================
This is judged by REAL-WORLD PRESENCE IN THE UAE, specifically how many
separate locations exist on Google Maps / Google Places. A client looking the
brand up on Google Maps and seeing 2, 3 or more locations regards it as a
chain - so that is the standard we match.

  Chain        The brand operates MULTIPLE outlets in the UAE. Two or more
               distinct Google Maps locations for the same brand is enough.
  Independent  A single location. No other branches. One address on Google
               Maps, one storefront.

You are given `google_locations_uae` - the count of distinct same-brand
locations found on Google Maps THAT ARE INSIDE THE UAE. Locations in any other
country are already excluded from this number and must not be counted, and a
result belonging to a DIFFERENT business that merely shares the name has
already been excluded too. TRUST IT. It decides outlet_type outright:
    google_locations_uae >= 2  -> Chain
    google_locations_uae <= 1  -> Independent
There is no exception. One UAE location, or none found, means Independent even
if you believe the brand has more; your belief is not evidence and the count
is. Do NOT infer chain status from the restaurant's name sounding like a
franchise.

=========================  CHAIN SUB-TYPE  ======================
Only asked when outlet_type is Chain. If Independent, this is not your call -
it is filled in as "N/A" automatically.

  MNC Chain    A large MULTINATIONAL brand present across many countries -
               KFC, McDonald's, Domino's, Subway, Pizza Hut, Burger King,
               Starbucks, Costa Coffee, Tim Hortons, Dunkin', Popeyes,
               Jollibee, Shake Shack, Five Guys, Pret A Manger, Caffe Nero,
               Krispy Kreme, Baskin Robbins, Cinnabon, Nando's, Wagamama,
               Texas Chicken, Hardee's, Papa John's and the like.
               The brand's identity is international, not UAE-born.
  Local Chain  A brand well known WITHIN the UAE with several UAE branches,
               but not an international franchise. UAE/GCC-born brands belong
               here even when they have many outlets. This is the DEFAULT for
               a chain you do not clearly recognise as multinational.

THE TEST IS WHERE THE BRAND HAS BRANCHES:

  Operates ONLY inside the UAE            -> Local Chain
  Has branches in ANY other country       -> MNC Chain

"Any other country" means literally that, and includes GCC neighbours. A UAE
brand that has opened outlets in Saudi Arabia, Kuwait, Qatar, Oman, Bahrain,
Egypt, India, the UK or anywhere else is an MNC Chain. A brand with fifty
outlets but all of them inside the UAE is a Local Chain.

Report two FACTS and let them decide it:
  1. country_of_origin      - where the brand started. If you do not genuinely
                              know, answer "unknown". Do not guess.
  2. operates_outside_uae   - true if it has outlets in any country other than
                              the UAE, false if it is UAE-only, based on what
                              you actually know about the brand.

Then: operates_outside_uae = true -> MNC Chain, otherwise Local Chain.

An unfamiliar brand name is evidence of a UAE-only Local Chain, not of an MNC.
Real multinationals are famous; if you have never heard of it, it is local and
operates_outside_uae should be false. When unsure, choose Local Chain.

=========================  RESTAURANT TYPE  =====================
Apply in order, stop at the first match.

1. Cloud Kitchen  Delivery/takeaway only, no dine-in seating, no customer-
                  facing storefront to eat in. Virtual/ghost brands.
2. Bakery         Core business is baked goods - bakery, patisserie, pastries,
                  cakes, bread, Arabic sweets. Uses `cuisines` and the menu
                  profile: if baked goods and sweets dominate, it is a Bakery
                  even when seating exists.
3. Cafes          Beverages and light bites are the main draw - coffee shops,
                  tea houses, juice/smoothie bars, dessert/ice-cream parlours,
                  shisha cafes. Ambience and drinks over a full meal.
4. Quick-Service Restaurants
                  Order at a counter, self-collect, carry your own food, or
                  takeaway-first with minimal seating. Lower price point,
                  speed and convenience. Shawarma counters, burger/fried
                  chicken joints, food-court units, cafeterias.
5. Full-Service Restaurants
                  Seated at a table, a server takes the order and brings food
                  to the table. Full menu with starters, mains and desserts.

Tie-breaks:
  - Table service AND alcohol, but a full meal menu  -> Full-Service
  - Counter ordering, but seating is for cake/coffee -> Cafes
  - Food-court counter with self-carry trays         -> Quick-Service
  - Sweets/pastry shop with a few tables             -> Bakery

=========================  OUTPUT  ==============================
Return one JSON object per line, no commentary, no markdown fence:
{"id": <id>, "restaurant_type": "...", "outlet_type": "...",
 "chained_outlet_type": "...", "country_of_origin": "<country or unknown>",
 "operates_outside_uae": true|false, "confidence": "High|Medium|Low",
 "reasoning": "<max 15 words>"}

- restaurant_type MUST be one of: Full-Service Restaurants,
  Quick-Service Restaurants, Cafes, Bakery, Cloud Kitchen
- outlet_type MUST be: Chain or Independent
- chained_outlet_type MUST be: Local Chain, MNC Chain, or N/A
  and MUST be exactly "N/A" whenever outlet_type is Independent
- Return exactly one line for every id given. Never skip one.
"""
