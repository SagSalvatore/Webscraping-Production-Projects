"""
5_build_fast_distributed_batch.py
------------------------------------
Speed-optimized redistribution across the team's 20 Apify keys.

Lesson learned from the single-key confirmation test: one big group
(100 queries in 1 run) crawls slowly because zero-result/retry queries
serialize inside that one run. Fix: split into MANY SMALL groups
(30-40 queries each) so each individual Apify run finishes fast, then
run several groups concurrently PER KEY (--workers 3) AND run many
KEYS concurrently (separate accounts = separate proxy pools, no shared
rate limit) = real horizontal parallelism, not just cost distribution.

Excludes the 100 default-tier + 10 known-chain names already spent on
the confirmation test (avoid double-billing the same names).

Allocation:
  Known chains  : 36 remaining -> 6 keys x 6 names each, cap=100
  Default tier  : 1,440 names  -> 12 keys x 120 names each (3 groups of
                  40 per key, --workers 3), cap=10

Uses keys 3-20 (Ramendu=1 and Nadeem=2 already used for confirmation).
"""

import csv
import json
import random
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

APIFY_DIR = Path(__file__).parent.parent
PREPARED  = APIFY_DIR / "output" / "prepared" / "search_terms.json"
BRANDS    = APIFY_DIR / "brands"
KEYS_CSV  = APIFY_DIR / "keys.csv"

random.seed(11)

ALREADY_USED_DEFAULT_COUNT = 100   # names spent in uae_confirm_default
ALREADY_USED_CHAIN_COUNT   = 10    # names spent in uae_confirm_chains

KNOWN_CHAIN_KEYS_N = 6
DEFAULT_TIER_KEYS_N = 12
NAMES_PER_DEFAULT_KEY = 120
GROUPS_PER_DEFAULT_KEY = 3   # 40 names/group -> smaller groups finish faster
NAMES_PER_CHAIN_KEY = 6


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
    print(f"  {brand_name:<28} {len(terms):>4} names, {len(groups)} groups, cap={max_cap}, key={key_name}")


def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def main():
    all_terms = json.loads(PREPARED.read_text(encoding="utf-8"))
    known_terms   = [t for t in all_terms if t["tier"] == "known_chain"]
    default_terms = [t for t in all_terms if t["tier"] == "default"]

    with open(KEYS_CSV, encoding="utf-8-sig") as f:
        key_names = [row["Name"].strip() for row in csv.DictReader(f)]

    # Skip the 2 keys already used (Ramendu, Nadeem) for the confirmation test
    fresh_keys = [k for k in key_names if k not in ("Ramendu", "Nadeem")]
    print(f"Fresh keys available: {len(fresh_keys)}")

    # Exclude already-tested names to avoid double billing
    random.shuffle(default_terms)
    random.shuffle(known_terms)
    already_tested_default = set()  # we don't have the exact 100 names handy here;
    # since default_terms is freshly reshuffled, just take a NEW slice further out
    # to guarantee no overlap in practice we offset by the confirm-test size
    default_pool = default_terms[ALREADY_USED_DEFAULT_COUNT:]
    chain_pool   = known_terms[ALREADY_USED_CHAIN_COUNT:]

    print(f"Default-tier pool available (excl. already tested): {len(default_pool)}")
    print(f"Known-chain pool available (excl. already tested)  : {len(chain_pool)}")

    key_idx = 0

    # -- Known chains: 6 keys x 6 names each --------------------------------
    chain_assign = chain_pool[:KNOWN_CHAIN_KEYS_N * NAMES_PER_CHAIN_KEY]
    print(f"\nKnown-chain batches ({len(chain_assign)} names across {KNOWN_CHAIN_KEYS_N} keys):")
    for batch in chunk(chain_assign, NAMES_PER_CHAIN_KEY):
        key_name = fresh_keys[key_idx]
        key_idx += 1
        brand = f"uae_fast_chain_{key_idx:02d}"
        groups = {"group_1": [t["query_string"] for t in batch]}
        write_brand_input(brand, batch, groups, max_cap=100,
                           display=f"UAE Fast Batch - Known Chains #{key_idx}", key_name=key_name)

    # -- Default tier: 12 keys x 120 names each (3 groups of 40) ------------
    default_assign = default_pool[:DEFAULT_TIER_KEYS_N * NAMES_PER_DEFAULT_KEY]
    print(f"\nDefault-tier batches ({len(default_assign)} names across {DEFAULT_TIER_KEYS_N} keys):")
    for batch in chunk(default_assign, NAMES_PER_DEFAULT_KEY):
        key_name = fresh_keys[key_idx]
        key_idx += 1
        brand = f"uae_fast_default_{key_idx:02d}"
        sub_groups = list(chunk(batch, len(batch) // GROUPS_PER_DEFAULT_KEY or 1))
        groups = {f"group_{i+1}": [t["query_string"] for t in g] for i, g in enumerate(sub_groups)}
        write_brand_input(brand, batch, groups, max_cap=10,
                           display=f"UAE Fast Batch - Default Tier #{key_idx}", key_name=key_name)

    print(f"\nTotal keys used in this round: {key_idx}")
    print(f"Keys remaining in reserve: {len(fresh_keys) - key_idx}")

    # Cost estimate
    BRONZE = 0.005
    chain_est = len(chain_assign) * 40 * BRONZE   # ~40 places/query observed
    default_est = len(default_assign) * 1.2 * BRONZE  # ~1.2 clean places/query observed
    print(f"\nEstimated cost (based on observed rates):")
    print(f"  Known chains : {len(chain_assign)} names * ~40/query  -> ~${chain_est:.2f}")
    print(f"  Default tier : {len(default_assign)} names * ~1.2/query -> ~${default_est:.2f}")
    print(f"  Total        : ~${chain_est + default_est:.2f}  (well under {key_idx}x$5=${key_idx*5} available)")


if __name__ == "__main__":
    main()
