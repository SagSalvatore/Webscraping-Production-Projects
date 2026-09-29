"""
2_build_validation_batch.py
----------------------------
Builds the pre-production validation test: a stratified sample of
~1,200 default-tier names (within the user's requested 1,000-1,500
range) plus the FULL 46-name known-chain allowlist.

Stratification across:
  - emirate (proportional to real distribution)
  - talabat_branch_count bucket (1 vs 2+ — since a 1-branch name could
    still turn out to be a real local chain; we want both represented)

Produces two brand folders compatible with the existing, proven
run_brand_scraper.py (ThreadPoolExecutor-parallel Apify runner used
for KFC/Domino's):

  brands/uae_validation_default/input.json   (cap=10,  ~1,200 names, N groups)
  brands/uae_validation_chains/input.json    (cap=150, 46 names,   1 group)

FIX applied after the first validation run blew through the full $29
monthly budget in ~7 minutes: searchMatching defaulted to "all" (fuzzy,
matches any place sharing a word with the search term — e.g. "Sultan
Biryani" matched "Falafel Sultan Dubai Restaurant"). Now uses
"only_includes" + a run-level locationQuery instead of embedding the
city in each search string (title-matching can't see embedded city
text anyway). Default-tier cap also lowered 30 -> 10, since tight
matching should rarely need more than a handful of results per name.

Each input.json also carries "query_metadata": a query_string -> Talabat
lookup table (name, tier, branch count, sample branch/url, emirate),
used later by 3_merge_uae_results.py to attach traceability fields
without any address parsing.
"""

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

APIFY_DIR = Path(__file__).parent.parent
PREPARED  = APIFY_DIR / "output" / "prepared" / "search_terms.json"
BRANDS    = APIFY_DIR / "brands"

VALIDATION_SAMPLE_SIZE = 1200   # within the requested 1,000-1,500 range
N_GROUPS_DEFAULT        = 6      # parallel groups for the default-tier validation run
SEED                    = 42

random.seed(SEED)


def stratified_sample(default_terms: list, sample_size: int) -> list:
    """Sample proportionally across (emirate, branch_bucket) strata."""
    def bucket(t):
        b = t["talabat_branch_count"]
        bucket_label = "1" if b == 1 else "2+"
        return (t["emirate"], bucket_label)

    strata = defaultdict(list)
    for t in default_terms:
        strata[bucket(t)].append(t)

    total = len(default_terms)
    sample = []
    for key, items in strata.items():
        random.shuffle(items)
        take = max(1, round(len(items) / total * sample_size))
        sample.extend(items[:take])

    random.shuffle(sample)
    return sample[:sample_size]


def split_into_groups(terms: list, n_groups: int) -> dict:
    groups = defaultdict(list)
    for i, t in enumerate(terms):
        groups[f"group_{i % n_groups + 1}"].append(t["query_string"])
    return dict(groups)


def build_metadata(terms: list) -> list:
    return [
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
    ]


def write_brand_input(brand_name: str, terms: list, groups: dict, max_cap: int, display: str):
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
        "proxyConfig": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
        },
        "_comment_query_metadata": "Lookup table: query -> Talabat source data. Used by 3_merge_uae_results.py for traceability (no address parsing needed for Talabat linkage).",
        "query_metadata": build_metadata(terms),
        "_comment_groups": f"{len(groups)} groups run in parallel via run_brand_scraper.py --workers N",
        "groups": groups,
    }

    out_path = brand_dir / "input.json"
    out_path.write_text(json.dumps(input_json, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Saved -> {out_path}  ({len(terms)} queries, {len(groups)} groups, cap={max_cap})")


def main():
    all_terms = json.loads(PREPARED.read_text(encoding="utf-8"))
    known_terms   = [t for t in all_terms if t["tier"] == "known_chain"]
    default_terms = [t for t in all_terms if t["tier"] == "default"]

    print(f"Loaded {len(all_terms)} prepared search terms")
    print(f"  known_chain : {len(known_terms)}")
    print(f"  default     : {len(default_terms)}")

    sample = stratified_sample(default_terms, VALIDATION_SAMPLE_SIZE)
    print(f"\nStratified validation sample (default tier): {len(sample)} names")

    from collections import Counter
    print("  By emirate:", dict(Counter(t["emirate"] for t in sample)))
    print("  By branch bucket:", dict(Counter("1" if t["talabat_branch_count"] == 1 else "2+" for t in sample)))

    # -- Default-tier validation batch --
    default_groups = split_into_groups(sample, N_GROUPS_DEFAULT)
    print(f"\nWriting default-tier validation batch ({N_GROUPS_DEFAULT} groups)...")
    write_brand_input(
        "uae_validation_default", sample, default_groups,
        max_cap=10, display="UAE Validation Batch - Default Tier (1200 names)"
    )

    # -- Known-chain validation batch (all 46, single group) --
    print(f"\nWriting known-chain validation batch (46 names, 1 group)...")
    known_groups = {"known_chains": [t["query_string"] for t in known_terms]}
    write_brand_input(
        "uae_validation_chains", known_terms, known_groups,
        max_cap=150, display="UAE Validation Batch - Known Global Chains (46 names)"
    )

    # -- Cost estimate for this validation run --
    BRONZE_PRICE = 0.003
    print(f"\n{'='*60}")
    print("Validation batch cost estimate (BRONZE tier, $0.003/place):")
    for avg in [2, 3, 5]:
        default_places = len(sample) * avg
        default_cost = default_places * BRONZE_PRICE
        print(f"  Default tier @ avg {avg} places/search: {default_places:>5} places -> ${default_cost:.2f}")
    for avg in [20, 40, 60]:
        known_places = len(known_terms) * avg
        known_cost = known_places * BRONZE_PRICE
        print(f"  Known-chain tier @ avg {avg} places/search: {known_places:>5} places -> ${known_cost:.2f}")
    print(f"{'='*60}")
    print("\nNext step:")
    print("  cd .. && python run_brand_scraper.py --brand uae_validation_default --dry-run")
    print("  cd .. && python run_brand_scraper.py --brand uae_validation_chains --dry-run")


if __name__ == "__main__":
    main()
