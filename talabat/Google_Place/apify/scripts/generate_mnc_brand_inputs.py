"""
generate_mnc_brand_inputs.py
-----------------------------
One-shot generator: creates brands/<slug>/input.json for each MNC brand in
apify/mnc_apify/mnc_apify.csv, following the exact same structure already
proven for KFC/Domino's (query_metadata for area lookup + groups for
parallel execution), so run_brand_scraper.py and process_brand.py work
on them unmodified.

Also writes a key-assignment map (one team key per brand, from
apify/keys.csv) so all 16 brands can run as separate, simultaneous Apify
accounts instead of queuing on one.
"""
import csv
import json
from pathlib import Path

APIFY_ROOT = Path(__file__).resolve().parent.parent
BRANDS_DIR = APIFY_ROOT / "brands"
MNC_CSV = APIFY_ROOT / "mnc_apify" / "mnc_apify.csv"
KEYS_CSV = APIFY_ROOT / "keys.csv"

# name -> (slug, display name, brand_keywords)
BRANDS = [
    ("Caribou Coffee",                 "caribou_coffee",     "Caribou Coffee UAE",           ["caribou coffee", "caribou"]),
    ("caffenero",                      "caffe_nero",         "Caffè Nero UAE",                ["caffe nero", "caffè nero", "caffenero"]),
    ("dunkin donuts",                  "dunkin_donuts",      "Dunkin' Donuts UAE",            ["dunkin donuts", "dunkin' donuts", "dunkin"]),
    ("% Arabica",                      "arabica",            "% Arabica UAE",                 ["% arabica", "arabica"]),
    ("Pret A Manger",                  "pret_a_manger",      "Pret A Manger UAE",              ["pret a manger", "pret manger"]),
    ("FiLLi Cafe",                     "filli_cafe",         "FiLLi Cafe UAE",                 ["filli cafe", "filli"]),
    ("Popeyes",                        "popeyes",            "Popeyes UAE",                    ["popeyes"]),
    ("Wendy's",                        "wendys",             "Wendy's UAE",                    ["wendy's", "wendys"]),
    ("Jollibee",                       "jollibee",           "Jollibee UAE",                   ["jollibee"]),
    ("Pickl",                          "pickl",              "Pickl UAE",                      ["pickl"]),
    ("German Doner Kebab (GDK)",       "german_doner_kebab", "German Doner Kebab (GDK) UAE",   ["german doner kebab", "gdk"]),
    ("Zaatar w Zeit",                  "zaatar_w_zeit",      "Zaatar w Zeit UAE",              ["zaatar w zeit", "zaatar & zeit", "zaatar and zeit"]),
    ("Five Guys Burgers and Fries",    "five_guys",          "Five Guys UAE",                  ["five guys"]),
    ("Shake Shack",                    "shake_shack",        "Shake Shack UAE",                ["shake shack"]),
    ("New York Fries",                 "new_york_fries",     "New York Fries UAE",             ["new york fries"]),
    ("Burger Fuel",                    "burger_fuel",        "BurgerFuel UAE",                 ["burgerfuel", "burger fuel"]),
]

# (city query text, area label, emirate label) -- matches kfc_input.json's
# 10-query per-emirate/city template exactly.
CITIES = [
    ("Dubai", "Dubai", "Dubai"),
    ("Abu Dhabi", "Abu Dhabi", "Abu Dhabi"),
    ("Al Ain", "Al Ain", "Abu Dhabi"),
    ("Sharjah", "Sharjah", "Sharjah"),
    ("Ajman", "Ajman", "Ajman"),
    ("Ras Al Khaimah", "Ras Al Khaimah", "Ras Al Khaimah"),
    ("Fujairah", "Fujairah", "Fujairah"),
    ("Umm Al Quwain", "Umm Al Quwain", "Umm Al Quwain"),
    ("Khor Fakkan", "Khor Fakkan", "Sharjah"),
    ("Kalba", "Kalba", "Sharjah"),
]

MAX_CRAWLED_PER_SEARCH = 100


def build_input_json(brand_name: str, brand_display: str, keywords: list[str]) -> dict:
    query_metadata = []
    queries = []
    for city_query, area, emirate in CITIES:
        q = f"{brand_name} in {city_query}, UAE"
        queries.append(q)
        query_metadata.append({"query": q, "area": area, "emirate": emirate})

    group_a = queries[:5]
    group_b = queries[5:]

    return {
        "brand_name": brand_name,
        "brand_display": brand_display,
        "brand_keywords": keywords,
        "output_filename": brand_display.upper().replace(" ", "_").replace("'", "").replace("(", "").replace(")", ""),
        # CRITICAL: "only_includes" (run_brand_scraper.py's default) requires the
        # place TITLE to literally contain the full search phrase (e.g. "in Dubai,
        # UAE") -- real listings are titled things like "Wendy's - Al Saqr Tower",
        # so every genuine match gets rejected by the actor itself before it ever
        # reaches process_brand.py. Confirmed empirically: 14+ real Wendy's outlets
        # found and discarded this way in one test. Use "all" and let
        # process_brand.py's own brand_keywords filter (is_brand()) do the real
        # matching instead -- same fix already established earlier in this project.
        "searchMatching": "all",
        "maxCrawledPlacesPerSearch": MAX_CRAWLED_PER_SEARCH,
        "language": "en",
        "countryCode": "ae",
        "maxReviews": 0,
        "maxImages": 0,
        "includeOpeningHours": False,
        "scrapeDirectories": False,
        "additionalInfo": False,
        "proxyConfig": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
        },
        "_comment_query_metadata": "Lookup table: search query -> area + emirate.",
        "query_metadata": query_metadata,
        "_comment_groups": "2 groups run in parallel per brand (5 city queries each).",
        "groups": {
            "group_a": group_a,
            "group_b": group_b,
        },
    }


def main():
    with open(KEYS_CSV, encoding="utf-8-sig") as f:
        team_keys = [row["Name"].strip() for row in csv.DictReader(f)]

    if len(team_keys) < len(BRANDS):
        raise ValueError(f"Not enough team keys ({len(team_keys)}) for {len(BRANDS)} brands")

    assignment = {}
    for i, (brand_name, slug, display, keywords) in enumerate(BRANDS):
        brand_dir = BRANDS_DIR / slug
        brand_dir.mkdir(parents=True, exist_ok=True)
        config = build_input_json(brand_name, display, keywords)
        with open(brand_dir / "input.json", "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        assignment[slug] = team_keys[i]
        print(f"  {slug:20s} -> input.json written, key: {team_keys[i]}")

    assignment_path = APIFY_ROOT / "mnc_apify" / "key_assignment.json"
    with open(assignment_path, "w", encoding="utf-8") as f:
        json.dump(assignment, f, indent=2)
    print(f"\nSaved key assignment -> {assignment_path}")
    print(f"Total brands: {len(BRANDS)}, total queries per brand: {len(CITIES)}")


if __name__ == "__main__":
    main()
