"""
generate_input.py
-----------------
Reads LOCAL.csv (120 restaurant/cafe names), cleans each name,
then writes brands/local/input.json ready for run_brand_scraper.py.

Cleaning rules applied (in order):
  1. Strip leading/trailing quotes and whitespace
  2. Remove " delivery from … - Order with Deliveroo" suffix
  3. Remove " in [Location], UAE" suffix
  4. Remove trailing " - [UAE area name]" (Al Barsha, Motor City, JVC, etc.)
  5. Strip leftover dashes / whitespace

Usage:
    python brands/local/generate_input.py
    python brands/local/generate_input.py --max 15   # override max results
    python brands/local/generate_input.py --dry-run  # print cleaned names only
"""

import re
import sys
import json
import math
import argparse
from pathlib import Path

CSV_FILE    = Path(__file__).parent / "LOCAL.csv"
OUTPUT_FILE = Path(__file__).parent / "input.json"
NUM_GROUPS  = 6      # parallel Apify runs
DEFAULT_MAX = 10     # max results per search query

# Patterns for UAE area / descriptor suffixes to strip from brand names
_AREA_SUFFIX_PATTERNS = [
    r"\s*-\s*Al\s+\w[\w\s]*$",                          # - Al Nahdha, - Al Barsha South
    r"\s*-\s*Abu\s+\w[\w\s]*$",                         # - Abu Dhabi
    r"\s*-\s*(?:Dubai|Sharjah|Ajman|Fujairah|RAK)\b.*$",
    r"\s*-\s*(?:Silicon Oasis|Motor City|JVC|JBR|DIFC|Mirdif|Karama)\b.*$",
    r"\s*-\s*Beach Canteen(?: Dine Out)?\s*$",           # Moishi - Beach Canteen
    r"\s*-\s*Dine Out\s*$",
]


def clean_name(raw: str) -> str:
    s = raw.strip().strip('"\'')

    # 1. Remove Deliveroo delivery suffix (everything from " delivery from" onward)
    s = re.sub(r"\s+delivery\s+from\b.*$", "", s, flags=re.IGNORECASE)

    # 2. Safety net: remove any leftover "- Order with Deliveroo"
    s = re.sub(r"\s*[-–]\s*Order with Deliveroo.*$", "", s, flags=re.IGNORECASE)

    # 3. Remove "in [Location], UAE" suffix
    s = re.sub(r"\s+in\s+[\w\s,]+,\s*UAE\s*$", "", s, flags=re.IGNORECASE)

    # 4. Remove trailing UAE area / location qualifiers
    for pat in _AREA_SUFFIX_PATTERNS:
        s = re.sub(pat, "", s, flags=re.IGNORECASE)

    return s.strip(" -").strip()


def build_query(brand: str) -> str:
    return f"{brand} UAE"


def main():
    parser = argparse.ArgumentParser(description="Generate Apify input for local brands")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX,
                        help=f"Max results per search (default {DEFAULT_MAX})")
    parser.add_argument("--groups", type=int, default=NUM_GROUPS,
                        help=f"Number of parallel groups (default {NUM_GROUPS})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print cleaned names only, don't write input.json")
    args = parser.parse_args()

    # Read CSV
    raw_lines = CSV_FILE.read_text(encoding="utf-8-sig").splitlines()
    raw_names = [l.strip().strip('"') for l in raw_lines if l.strip()]
    print(f"Raw names loaded : {len(raw_names)}")

    # Clean and deduplicate
    seen_queries : set[str] = set()
    brands = []
    for raw in raw_names:
        cleaned = clean_name(raw)
        if not cleaned:
            continue
        query = build_query(cleaned)
        # Deduplicate (same cleaned name from different Deliveroo entries)
        if query.lower() not in seen_queries:
            seen_queries.add(query.lower())
            brands.append({"original": raw, "brand_name": cleaned, "query": query})

    print(f"Unique brands    : {len(brands)}")

    if args.dry_run:
        print()
        print(f"{'#':>3}  {'Cleaned Name':<50}  {'Query'}")
        print("-" * 100)
        for i, b in enumerate(brands, 1):
            print(f"{i:3}.  {b['brand_name']:<50}  {b['query']}")
        return

    # Confirm any manual review items
    print()
    for b in brands:
        if b["brand_name"] != b["original"]:
            pass  # silently cleaned

    # Split into groups
    n = len(brands)
    g = args.groups
    size = math.ceil(n / g)
    chunks = [brands[i:i + size] for i in range(0, n, size)]
    group_dict = {f"batch_{i+1}": [b["query"] for b in chunk]
                  for i, chunk in enumerate(chunks)}

    query_meta = [
        {
            "query":      b["query"],
            "brand_name": b["brand_name"],
            "original":   b["original"],
        }
        for b in brands
    ]

    total_queries = sum(len(v) for v in group_dict.values())
    budget_ceil   = total_queries * args.max * 0.004

    config = {
        "brand_name":    "Local",
        "brand_display": "UAE Local Multi-Location Restaurants",
        "brand_keywords": [],        # no brand filter in runner — handled in process_local.py
        "output_filename": "LOCAL_UAE",
        "maxCrawledPlacesPerSearch": args.max,
        "language":    "en",
        "countryCode": "ae",
        "maxReviews":  0,
        "maxImages":   0,
        "includeOpeningHours": False,
        "scrapeDirectories":   False,
        "additionalInfo":      False,
        "proxyConfig": {
            "useApifyProxy":      True,
            "apifyProxyGroups": ["RESIDENTIAL"],
        },
        "query_metadata": query_meta,
        "groups": group_dict,
    }

    OUTPUT_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Groups           : {len(group_dict)}")
    for gname, queries in group_dict.items():
        print(f"  {gname}: {len(queries)} queries")
    print(f"Total queries    : {total_queries}")
    print(f"Max per query    : {args.max}")
    print(f"Budget ceiling   : ~${budget_ceil:.2f}  (worst case)")
    print(f"Written to       : {OUTPUT_FILE}")
    print()
    print("Next steps:")
    print("  python run_brand_scraper.py --brand local --dry-run")
    print("  python run_brand_scraper.py --brand local --workers 6")


if __name__ == "__main__":
    main()
