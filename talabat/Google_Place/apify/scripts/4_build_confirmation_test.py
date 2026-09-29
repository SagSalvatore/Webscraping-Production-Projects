"""
4_build_confirmation_test.py
------------------------------
The "last hope" confirmation test before committing the team's 20 keys
to full production. Uses the REAL production settings (searchMatching=
only_includes, locationQuery=UAE, cap=10 default / up to 100 for a
known-chain sample) but on a small enough query count that even a full
worst-case blowup can't exceed a single $5 free-tier key.

Two separate brand folders, each assigned its OWN key, so a problem in
one cannot cascade into the other (unlike the main-account incident,
where all 7 groups shared one budget and died together):

  brands/uae_confirm_default/input.json   100 names, cap=10   -> key "Ramendu"
  brands/uae_confirm_chains/input.json     10 names, cap=100  -> key "Nadeem"

Worst-case ceiling (FREE tier, $0.004/place):
  default: 100 * 10 * 0.004 = $4.00
  chains :  10 * 100 * 0.004 = $4.00
"""

import json
import random
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

APIFY_DIR = Path(__file__).parent.parent
PREPARED  = APIFY_DIR / "output" / "prepared" / "search_terms.json"
BRANDS    = APIFY_DIR / "brands"

random.seed(7)


def write_brand_input(brand_name, terms, groups, max_cap, display, key_name):
    brand_dir = BRANDS / brand_name
    brand_dir.mkdir(parents=True, exist_ok=True)

    input_json = {
        "brand_name": brand_name,
        "brand_display": display,
        "output_filename": brand_name.upper(),
        "maxCrawledPlacesPerSearch": max_cap,
        "locationQuery": "United Arab Emirates",
        "searchMatching": "only_includes",
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
    print(f"  Saved -> {out_path}  ({len(terms)} queries, cap={max_cap}, key={key_name})")


def main():
    all_terms = json.loads(PREPARED.read_text(encoding="utf-8"))
    known_terms   = [t for t in all_terms if t["tier"] == "known_chain"]
    default_terms = [t for t in all_terms if t["tier"] == "default"]

    random.shuffle(default_terms)
    random.shuffle(known_terms)

    default_sample = default_terms[:100]
    chain_sample   = known_terms[:10]

    print(f"Default-tier confirmation sample: {len(default_sample)} names")
    print(f"Known-chain confirmation sample : {len(chain_sample)} names")

    default_groups = {"group_1": [t["query_string"] for t in default_sample]}
    write_brand_input(
        "uae_confirm_default", default_sample, default_groups,
        max_cap=10, display="UAE Confirmation Test - Default Tier (100 names)",
        key_name="Ramendu",
    )

    chain_groups = {"group_1": [t["query_string"] for t in chain_sample]}
    write_brand_input(
        "uae_confirm_chains", chain_sample, chain_groups,
        max_cap=100, display="UAE Confirmation Test - Known Chains (10 names)",
        key_name="Nadeem",
    )

    print(f"\n{'='*60}")
    print("Worst-case ceiling (FREE tier $0.004/place):")
    print(f"  Default: 100 * 10 * 0.004  = $4.00  (key: Ramendu, $5 budget)")
    print(f"  Chains :  10 * 100 * 0.004 = $4.00  (key: Nadeem, $5 budget)")
    print(f"{'='*60}")
    print("\nNext:")
    print("  python run_brand_scraper.py --brand uae_confirm_default --key-name Ramendu --cost-per-place 0.004 --workers 1")
    print("  python run_brand_scraper.py --brand uae_confirm_chains --key-name Nadeem --cost-per-place 0.004 --workers 1")


if __name__ == "__main__":
    main()
