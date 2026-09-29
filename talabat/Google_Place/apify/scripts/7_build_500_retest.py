"""
7_build_500_retest.py
------------------------
Tests the "UAE Restaurant" suffix fix on 500 FRESH default-tier names
(excludes everything already tested in earlier batches to avoid
re-billing). Splits across several already-used team keys (they still
have $3-4+ remaining each) for fast parallel execution.
"""
import csv
import glob
import json
import random
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

APIFY_DIR = Path(__file__).parent.parent
PREPARED  = APIFY_DIR / "output" / "prepared" / "search_terms.json"
BRANDS    = APIFY_DIR / "brands"

random.seed(99)

SAMPLE_SIZE = 500
KEYS_TO_USE = [
    ("Elakiya Chandrasekhar", 125),
    ("Venkatesh Bestha", 125),
    ("Ravi Chandra", 125),
    ("Thejas K Sabu", 125),
]


def already_tested_names():
    tested = set()
    for brand_dir in BRANDS.glob("uae_confirm_default") :
        pass
    patterns = ["uae_confirm_default", "uae_fast_default_*"]
    dirs = [BRANDS / "uae_confirm_default"] + sorted(BRANDS.glob("uae_fast_default_*"))
    for d in dirs:
        input_path = d / "input.json"
        if input_path.exists():
            cfg = json.loads(input_path.read_text(encoding="utf-8"))
            for m in cfg.get("query_metadata", []):
                tested.add(m["restaurant_name_clean"])
    return tested


def write_brand_input(brand_name, terms, groups, max_cap, display, key_name):
    brand_dir = BRANDS / brand_name
    brand_dir.mkdir(parents=True, exist_ok=True)
    input_json = {
        "brand_name": brand_name,
        "brand_display": display,
        "output_filename": brand_name.upper(),
        "maxCrawledPlacesPerSearch": max_cap,
        "locationQuery": "United Arab Emirates",
        # searchMatching="all" here on purpose: the search string now has a
        # "UAE Restaurant" suffix appended for Google's OWN ranking benefit,
        # but the actor's built-in only_includes/only_exact matching compares
        # titles against that FULL padded string literally -- real place
        # titles never contain "UAE Restaurant", so only_includes would
        # silently reject every result (confirmed: 0 places in 8 min before
        # this fix). Quality control now happens entirely in our own
        # post-hoc relevance filter (3_merge_uae_results.py /
        # 8_merge_retest_and_export.py), which correctly compares titles
        # against just the base restaurant name.
        "searchMatching": "all",
        "language": "en",
        "countryCode": "ae",
        "maxReviews": 0,
        "maxImages": 0,
        "includeOpeningHours": False,
        "scrapeDirectories": False,
        "additionalInfo": False,
        "proxyConfig": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        "assigned_key_name": key_name,
        "query_metadata": [
            {
                "query": t["query_string"],
                "restaurant_name_clean": t["restaurant_name_clean"],
                "tier": t["tier"],
                "emirate_hint": t["emirate"],
                "talabat_branch_count": t["talabat_branch_count"],
                "sample_branch_id": t["sample_branch_id"],
                "sample_map_url": t["sample_map_url"],
            }
            for t in terms
        ],
        "groups": groups,
    }
    out_path = brand_dir / "input.json"
    out_path.write_text(json.dumps(input_json, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {brand_name:<28} {len(terms):>4} names, key={key_name}")


def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def main():
    all_terms = json.loads(PREPARED.read_text(encoding="utf-8"))
    default_terms = [t for t in all_terms if t["tier"] == "default"]

    tested = already_tested_names()
    print(f"Already-tested default-tier names to exclude: {len(tested)}")

    fresh_pool = [t for t in default_terms if t["restaurant_name_clean"] not in tested]
    print(f"Fresh untested pool available: {len(fresh_pool)}")

    random.shuffle(fresh_pool)
    sample = fresh_pool[:SAMPLE_SIZE]
    print(f"Sample selected: {len(sample)}")
    print(f"\nExample new-format query strings:")
    for t in sample[:5]:
        print(f"  {t['query_string']!r}")

    idx = 0
    for key_name, count in KEYS_TO_USE:
        batch = sample[idx:idx + count]
        idx += count
        brand = f"uae_retest_{key_name.replace(' ', '_').lower()}"
        # split into 3 groups per key for intra-key parallelism
        sub = list(chunk(batch, max(1, len(batch) // 3)))
        groups = {f"group_{i+1}": [t["query_string"] for t in g] for i, g in enumerate(sub)}
        write_brand_input(brand, batch, groups, max_cap=10,
                           display=f"UAE Retest (UAE Restaurant suffix) - {key_name}",
                           key_name=key_name)

    print(f"\nTotal names batched: {idx}")


if __name__ == "__main__":
    main()
