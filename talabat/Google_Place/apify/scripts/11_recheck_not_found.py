"""
11_recheck_not_found.py
---------------------------
Re-examines the 261 "Not Found" entries from the strict 500-name retest
using the CORRECTED name (fused-name fix applied via branch_id lookup
back to the original CSV), against the SAME already-collected Google
raw data. Zero new Apify spend -- purely local re-analysis.

Answers: of the 261 "Not Found", how many actually have a real match
once the fused-name fix is applied to the comparison name?
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

BRANDS = [
    "uae_retest_elakiya_chandrasekhar",
    "uae_retest_venkatesh_bestha",
    "uae_retest_ravi_chandra",
    "uae_retest_thejas_k_sabu",
]

STOPWORDS = {
    "restaurant", "restaurants", "cafe", "cafeteria", "kitchen", "grill",
    "house", "eatery", "bistro", "diner", "cuisine", "cuisines", "food",
    "foods", "corner", "shop", "llc", "branch", "and", "the", "of", "by",
    "bar", "co", "company", "sweets", "bakery", "snack", "snacks",
}

_FUSED_AREA_SUFFIX = re.compile(r"^(.*?)in([A-Z][a-zA-Z0-9\s\-]*?)\s*,\s*UAE$")
_BOM_CHARS = re.compile(r"[﻿​‎‏]")


def fix_fused_name(raw: str) -> str:
    m = _FUSED_AREA_SUFFIX.match(raw.strip())
    return m.group(1).strip() if m else raw


def clean_name(raw: str) -> str:
    raw = fix_fused_name(raw)
    n = raw.strip().lower()
    n = _BOM_CHARS.sub("", n)
    n = re.sub(r"\s+", " ", n)
    n = re.sub(r"[™®©]", "", n)
    n = n.replace(" & ", " and ")
    return n.strip()


def core_token_list(name: str) -> list:
    if not name:
        return []
    name = name.split("|")[0]
    name = re.sub(r"[^\x00-\x7f]", "", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    return [w for w in name.split() if w and w not in STOPWORDS]


def is_exact_entity_match(talabat_name: str, google_title: str) -> bool:
    t = core_token_list(talabat_name)
    g = core_token_list(google_title)
    if not t or not g or len(g) < len(t):
        return False
    return g[:len(t)] == t


# -- Build branch_id -> corrected clean name lookup --------------------------
branch_to_raw = {}
with open(CSV_PATH, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        branch_to_raw[r["branch_id"]] = r["restaurant_name"]

recheck_results = []
still_not_found = 0
newly_found = 0
same_as_before_found = 0

for brand in BRANDS:
    brand_dir = APIFY_ROOT / "brands" / brand
    input_path = brand_dir / "input.json"
    raw_dir = brand_dir / "output" / "raw"
    config = json.loads(input_path.read_text(encoding="utf-8"))
    metadata = {m["query"]: m for m in config.get("query_metadata", [])}

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
        old_base_name = meta.get("restaurant_name_clean", query)
        branch_id = meta.get("sample_branch_id")
        raw_talabat_name = branch_to_raw.get(branch_id, old_base_name)
        corrected_base_name = clean_name(raw_talabat_name)

        raw_results = by_query.get(query, [])
        old_match = any(is_exact_entity_match(old_base_name, r.get("title") or r.get("name") or "") for r in raw_results)

        if old_match:
            same_as_before_found += 1
            continue  # already counted as Matched before, not part of the 261

        # This was in the "Not Found" bucket -- recheck with corrected name
        new_matches = [r for r in raw_results if is_exact_entity_match(corrected_base_name, r.get("title") or r.get("name") or "")]
        if new_matches:
            newly_found += 1
            recheck_results.append({
                "old_name": old_base_name,
                "corrected_name": corrected_base_name,
                "changed": old_base_name != corrected_base_name,
                "new_match_title": new_matches[0].get("title") or new_matches[0].get("name"),
            })
        else:
            still_not_found += 1

print(f"{'='*60}")
print(f"Previously Matched (unaffected)      : {same_as_before_found}")
print(f"Previously Not Found, now MATCHED    : {newly_found}")
print(f"Still Not Found                      : {still_not_found}")
print(f"Total (should be 500)                : {same_as_before_found + newly_found + still_not_found}")
print(f"{'='*60}")
print()
print("Sample of newly-recovered matches:")
for r in recheck_results[:20]:
    tag = " [name was corrected]" if r["changed"] else ""
    print(f"  {r['old_name']!r:<45} -> {r['new_match_title']!r}{tag}")
