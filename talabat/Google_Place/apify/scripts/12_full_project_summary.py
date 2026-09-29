"""
12_full_project_summary.py
------------------------------
Complete, honest project accounting across EVERYTHING done so far:
KFC, Domino's (pre-existing), all 46 known chains, and all default-tier
batches (confirmation + fast-distributed + 500-retest).

Re-applies the SAME strict entity-matching logic (ordered-prefix +
fused-name fix) to EVERY batch uniformly, including the ones originally
processed with the older, looser SequenceMatcher filter -- so this is
an apples-to-apples count, not a mix of two different matching
standards. Zero new Apify spend; purely local re-analysis of already-
collected raw data.
"""
import csv
import glob
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

APIFY_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = APIFY_ROOT / "Complete_list_apify.csv"

STOPWORDS = {
    "restaurant", "restaurants", "cafe", "cafeteria", "kitchen", "grill",
    "house", "eatery", "bistro", "diner", "cuisine", "cuisines", "food",
    "foods", "corner", "shop", "llc", "branch", "and", "the", "of", "by",
    "bar", "co", "company", "sweets", "bakery", "snack", "snacks",
}
_FUSED_AREA_SUFFIX = re.compile(r"^(.*?)in([A-Z][a-zA-Z0-9\s\-]*?)\s*,\s*UAE$")
_BOM_CHARS = re.compile(r"[﻿​‎‏]")
_UAE_SUFFIX = re.compile(r",?\s*(united arab emirates|u\.a\.e\.?|uae)\s*$", re.IGNORECASE)
_EMIRATE_PATTERNS = [
    ("Al Ain", "Abu Dhabi"), ("Khor Fakkan", "Sharjah"), ("Kalba", "Sharjah"),
    ("Dubai", "Dubai"), ("Abu Dhabi", "Abu Dhabi"), ("Sharjah", "Sharjah"),
    ("Ajman", "Ajman"), ("Ras Al Khaimah", "Ras Al Khaimah"),
    ("Fujairah", "Fujairah"), ("Umm Al Quwain", "Umm Al Quwain"),
]


def fix_fused_name(raw):
    m = _FUSED_AREA_SUFFIX.match(raw.strip())
    return m.group(1).strip() if m else raw


def clean_name(raw):
    raw = fix_fused_name(raw)
    n = raw.strip().lower()
    n = _BOM_CHARS.sub("", n)
    n = re.sub(r"\s+", " ", n)
    n = re.sub(r"[™®©]", "", n)
    n = n.replace(" & ", " and ")
    return n.strip()


def core_token_list(name):
    if not name:
        return []
    name = name.split("|")[0]
    name = re.sub(r"[^\x00-\x7f]", "", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    return [w for w in name.split() if w and w not in STOPWORDS]


def is_exact_entity_match(talabat_name, google_title):
    t = core_token_list(talabat_name)
    g = core_token_list(google_title)
    if not t or not g or len(g) < len(t):
        return False
    return g[:len(t)] == t


def emirate_from_address(address):
    if not address:
        return "Unknown"
    addr = _UAE_SUFFIX.sub("", address)
    for kw, em in _EMIRATE_PATTERNS:
        if kw.lower() in addr.lower():
            return em
    return "Unknown"


# -- Source of truth: full Talabat dataset -----------------------------------
with open(CSV_PATH, encoding="utf-8-sig") as f:
    all_rows = list(csv.DictReader(f))

branch_to_raw = {r["branch_id"]: r["restaurant_name"] for r in all_rows}
all_unique_clean_names = set(clean_name(r["restaurant_name"]) for r in all_rows)
TOTAL_UNIQUE_NAMES = len(all_unique_clean_names)
TOTAL_BRANCHES = len(all_rows)

# Every clean name can correspond to multiple Talabat branches/restaurant_ids
# (chains, multi-branch listings) -- gather them all for traceability.
from collections import defaultdict
name_to_branch_ids = defaultdict(list)
name_to_restaurant_ids = defaultdict(list)
for r in all_rows:
    cn = clean_name(r["restaurant_name"])
    name_to_branch_ids[cn].append(r["branch_id"])
    name_to_restaurant_ids[cn].append(r["restaurant_id"])

# KFC / Domino's -- done separately, pre-existing, excluded from our batches
PRE_EXISTING_DONE = {"kfc", "domino's pizza"}

# -- All brand folders processed so far (in chronological order) ------------
BRAND_FOLDERS = [
    ("uae_validation_default", "FIRST validation batch (1200, pre-fix, was missing from summary)"),
    ("uae_validation_chains", "FIRST known-chain validation (46, pre-fix, was missing from summary)"),
    ("uae_confirm_default", "default-tier confirmation (100)"),
    ("uae_confirm_chains", "known-chain confirmation (10)"),
    ("uae_fast_chain_01", "known chains batch 1"),
    ("uae_fast_chain_02", "known chains batch 2"),
    ("uae_fast_chain_03", "known chains batch 3"),
    ("uae_fast_chain_04", "known chains batch 4"),
    ("uae_fast_chain_05", "known chains batch 5"),
    ("uae_fast_chain_06", "known chains batch 6"),
    ("uae_fast_default_07", "default-tier fast batch 07"),
    ("uae_fast_default_08", "default-tier fast batch 08"),
    ("uae_fast_default_09", "default-tier fast batch 09"),
    ("uae_fast_default_10", "default-tier fast batch 10"),
    ("uae_fast_default_11", "default-tier fast batch 11"),
    ("uae_fast_default_12", "default-tier fast batch 12"),
    ("uae_fast_default_13", "default-tier fast batch 13"),
    ("uae_fast_default_14", "default-tier fast batch 14"),
    ("uae_fast_default_15", "default-tier fast batch 15"),
    ("uae_fast_default_16", "default-tier fast batch 16"),
    ("uae_fast_default_17", "default-tier fast batch 17"),
    ("uae_fast_default_18", "default-tier fast batch 18"),
    ("uae_retest_elakiya_chandrasekhar", "500-retest batch 1"),
    ("uae_retest_venkatesh_bestha", "500-retest batch 2"),
    ("uae_retest_ravi_chandra", "500-retest batch 3"),
    ("uae_retest_thejas_k_sabu", "500-retest batch 4"),
    ("uae_main_batch_01", "main account checkpointed batch 1 (500)"),
    ("uae_main_batch_02", "main account checkpointed batch 2 (100)"),
    ("uae_main_batch_03", "main account checkpointed batch 3 (500)"),
    ("uae_main_batch_04", "main account checkpointed batch 4 (500, 10-group concurrency)"),
    ("uae_main_batch_05", "main account checkpointed batch 5 (500, 20-group concurrency)"),
    ("uae_main_batch_06", "main account checkpointed batch 6 (500, 30-group concurrency)"),
    ("uae_main_batch_07", "main account checkpointed batch 7 (500, 30-group concurrency)"),
    ("uae_main_batch_08", "main account checkpointed batch 8 (500, 30-group concurrency)"),
    ("uae_main_batch_09", "main account checkpointed batch 9 (500, 30-group concurrency)"),
    ("uae_main_batch_10", "main account checkpointed batch 10 (500, 30-group concurrency)"),
    ("uae_main_batch_11", "main account checkpointed batch 11 (500, 30-group concurrency)"),
    ("uae_main_batch_12", "main account checkpointed batch 12 (500, 30-group concurrency)"),
    ("uae_main_batch_13", "main account checkpointed batch 13 (500, 30-group concurrency)"),
    ("uae_main_batch_14", "main account checkpointed batch 14 (500, 30-group concurrency)"),
]

tested_names = set()
matched_rows = []
not_found_names = set()
other_entity_rows = []

for brand, label in BRAND_FOLDERS:
    brand_dir = APIFY_ROOT / "brands" / brand
    input_path = brand_dir / "input.json"
    raw_dir = brand_dir / "output" / "raw"
    if not input_path.exists():
        continue

    config = json.loads(input_path.read_text(encoding="utf-8"))
    metadata = {m["query"]: m for m in config.get("query_metadata", [])}
    tier_default = config.get("maxCrawledPlacesPerSearch", 10) >= 100  # rough tier hint

    files = sorted(glob.glob(str(raw_dir / "dataset_*.json")))
    files = [f for f in files if "merged_all" not in f]
    if not files:
        files = sorted(glob.glob(str(raw_dir / "dataset_*merged_all.json")))
    items = []
    for f in files:
        items.extend(json.loads(Path(f).read_text(encoding="utf-8")))

    by_query = {}
    for it in items:
        q = it.get("searchString")
        if q:
            by_query.setdefault(q, []).append(it)

    for query, meta in metadata.items():
        branch_id = meta.get("sample_branch_id")
        raw_name = branch_to_raw.get(branch_id)
        corrected_name = clean_name(raw_name) if raw_name else meta.get("restaurant_name_clean", query)

        if corrected_name in PRE_EXISTING_DONE:
            continue  # this is actually a KFC/Domino's duplicate that slipped through, don't double count

        tested_names.add(corrected_name)
        raw_results = by_query.get(query, [])

        matched_result = None
        for r in raw_results:
            title = r.get("title") or r.get("name") or ""
            if is_exact_entity_match(corrected_name, title):
                matched_result = r
            else:
                address = r.get("address") or r.get("fullAddress") or ""
                loc = r.get("location") if isinstance(r.get("location"), dict) else {}
                other_entity_rows.append({
                    "Discovered_Via_Search": query,
                    "Google_Business_Name": title,
                    "Address": address,
                    "Phone": r.get("phone") or r.get("phoneUnformatted"),
                    "Website": r.get("website"),
                    "Google_Maps_URL": r.get("url") or (
                        f"https://www.google.com/maps/place/?q=place_id:{r.get('placeId')}" if r.get("placeId") else None
                    ),
                    "Place_ID": r.get("placeId"),
                    "Category": r.get("categoryName") or r.get("category"),
                    "Latitude": loc.get("lat") or r.get("latitude"),
                    "Longitude": loc.get("lng") or r.get("longitude"),
                    "Rating": r.get("totalScore") or r.get("rating"),
                    "Review_Count": r.get("reviewsCount") or r.get("reviewCount"),
                })

        branch_ids_str = ", ".join(name_to_branch_ids.get(corrected_name, []))
        restaurant_ids_str = ", ".join(sorted(set(name_to_restaurant_ids.get(corrected_name, []))))

        if matched_result:
            r = matched_result
            address = r.get("address") or r.get("fullAddress") or ""
            matched_rows.append({
                "Talabat_Restaurant_Name": corrected_name,
                "Branch_IDs": branch_ids_str,
                "Restaurant_IDs": restaurant_ids_str,
                "Google_Business_Name": r.get("title") or r.get("name"),
                "Address": address,
                "Phone": r.get("phone") or r.get("phoneUnformatted"),
                "Website": r.get("website"),
                "Google_Maps_URL": r.get("url") or (
                    f"https://www.google.com/maps/place/?q=place_id:{r.get('placeId')}" if r.get("placeId") else None
                ),
                "Place_ID": r.get("placeId"),
                "Rating": r.get("totalScore") or r.get("rating"),
                "Review_Count": r.get("reviewsCount") or r.get("reviewCount"),
                "Emirate": emirate_from_address(address),
                "Source_Batch": brand,
            })
        else:
            not_found_names.add(corrected_name)

matched_df_rows = matched_rows
unique_place_ids = set(r["Place_ID"] for r in matched_rows if r.get("Place_ID"))

KFC_DOMINOS_DONE = 2  # names, done outside this pipeline (KFC_UAE.csv, DOMINOS_UAE.csv)
remaining_untested = TOTAL_UNIQUE_NAMES - len(tested_names) - KFC_DOMINOS_DONE

print(f"{'='*70}")
print(f"FULL PROJECT SUMMARY — {Path(CSV_PATH).name}")
print(f"{'='*70}")
print(f"Total Talabat branches (raw)              : {TOTAL_BRANCHES:,}")
print(f"Total unique restaurant names (corrected)  : {TOTAL_UNIQUE_NAMES:,}")
print()
print(f"Already done pre-project (KFC + Domino's)  : {KFC_DOMINOS_DONE} names (complete, separate files)")
print()
print(f"Tested via this pipeline so far            : {len(tested_names):,} unique names")
print(f"  Confirmed exact match (real data)        : {len(matched_rows):,} rows -> {len(unique_place_ids):,} unique places (post place_id dedup)")
print(f"  Not Found (needs review or re-search)     : {len(not_found_names):,} names")
print(f"  Other UAE entities discovered (bonus)      : {len(other_entity_rows):,} rows (not merged, separate file)")
print()
print(f"REMAINING (never searched yet)              : {remaining_untested:,} names")
print(f"{'='*70}")
print()
print(f"Progress: {len(tested_names)/TOTAL_UNIQUE_NAMES*100:.1f}% of all unique names tested")
print(f"Match quality: {len(matched_rows)/len(tested_names)*100:.1f}% of tested names confirmed matched")

# Save the consolidated matched directory + not-found list + other entities
OUT_DIR = APIFY_ROOT / "output" / "full_summary"
OUT_DIR.mkdir(parents=True, exist_ok=True)

import pandas as pd
pd.DataFrame(matched_rows).to_csv(OUT_DIR / "All_Matched_So_Far.csv", index=False, encoding="utf-8-sig")

not_found_rows = [
    {
        "Talabat_Restaurant_Name": n,
        "Branch_IDs": ", ".join(name_to_branch_ids.get(n, [])),
        "Restaurant_IDs": ", ".join(sorted(set(name_to_restaurant_ids.get(n, [])))),
    }
    for n in sorted(not_found_names)
]
pd.DataFrame(not_found_rows).to_csv(OUT_DIR / "All_NotFound_So_Far.csv", index=False, encoding="utf-8-sig")

pd.DataFrame(other_entity_rows).drop_duplicates(subset=["Place_ID"]).to_csv(OUT_DIR / "All_OtherEntities_So_Far.csv", index=False, encoding="utf-8-sig")

print(f"\nSaved -> {OUT_DIR / 'All_Matched_So_Far.csv'}")
print(f"Saved -> {OUT_DIR / 'All_NotFound_So_Far.csv'}")
print(f"Saved -> {OUT_DIR / 'All_OtherEntities_So_Far.csv'}")
