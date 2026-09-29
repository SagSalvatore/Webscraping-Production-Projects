"""
build_master_mapping.py
--------------------------
Joins the ORIGINAL Talabat source file (apify/Complete_list_apify.csv,
15,768 branches) against EVERYTHING scraped so far across BOTH methods
(Apify main-account batches + DIY Playwright scraper), keyed by
branch_id / restaurant_id / restaurant_name.

Every Talabat BRANCH gets at least one row:
  - If its (cleaned) name was matched to Google, one row PER Google
    location found for that name -- so a name matched to 3 real UAE
    locations produces 3 rows for that branch, each with full address/
    phone/website/etc. This is exactly the "does this Talabat entity
    have multiple locations" signal the whole project is for.
  - If searched but not matched: one row, status = "Not Found".
  - If never searched yet: one row, status = "Not Yet Searched".

KFC and Domino's excluded per user request (handled separately,
already delivered as their own files).

Output: map/output/Master_Talabat_Google_Mapping.csv + .json
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
APIFY_DIR = ROOT / "apify"
GOOGLE_MAP_DIR = ROOT / "Google_map"
OUT_DIR = Path(__file__).parent / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CSV_PATH = APIFY_DIR / "Complete_list_apify.csv"
EXCLUDE_DONE = {"kfc", "domino's pizza"}  # handled separately, not part of this mapping

_FUSED_AREA_SUFFIX = re.compile(r"^(.*?)in([A-Z][a-zA-Z0-9\s\-]*?)\s*,\s*UAE$")
_BOM_CHARS = re.compile(r"[﻿​‎‏]")


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


# -- Load the original Talabat source (base table) ---------------------------
with open(CSV_PATH, encoding="utf-8-sig") as f:
    talabat_rows = list(csv.DictReader(f))

for r in talabat_rows:
    r["_clean_name"] = clean_name(r["restaurant_name"])

print(f"Loaded {len(talabat_rows)} Talabat branches from {CSV_PATH.name}")

# -- Load ALL matched / not-found data, keyed by cleaned name -----------------
# name -> list of Google match dicts
matches_by_name = defaultdict(list)
# name -> True if searched but confirmed no match
not_found_names = set()

# Apify cumulative summary (already merges every Apify batch so far)
apify_matched = pd.read_csv(APIFY_DIR / "output" / "full_summary" / "All_Matched_So_Far.csv")
for _, row in apify_matched.iterrows():
    name = row["Talabat_Restaurant_Name"]
    matches_by_name[name].append({
        "Google_Business_Name": row.get("Google_Business_Name"),
        "Address": row.get("Address"),
        "Phone": row.get("Phone"),
        "Website": row.get("Website"),
        "Google_Maps_URL": row.get("Google_Maps_URL"),
        "Place_ID": row.get("Place_ID"),
        "Rating": row.get("Rating"),
        "Review_Count": row.get("Review_Count"),
        "Emirate": row.get("Emirate"),
        "Source": row.get("Source_Batch", "apify"),
    })

apify_notfound = pd.read_csv(APIFY_DIR / "output" / "full_summary" / "All_NotFound_So_Far.csv")
for name in apify_notfound["Talabat_Restaurant_Name"]:
    not_found_names.add(name)

print(f"Apify matched rows loaded  : {len(apify_matched)}  (unique names: {apify_matched['Talabat_Restaurant_Name'].nunique()})")
print(f"Apify not-found names loaded: {len(apify_notfound)}")

# DIY scraper batches (use only the FINAL/correct timestamped files, not
# the earlier 5-entry sanity-test duplicate for batch 1)
DIY_FILES = [
    ("Matched", GOOGLE_MAP_DIR / "output" / "results" / "DIY_Batch01_Matched_20260702_222334.csv", "diy_batch_01"),
    ("Matched", GOOGLE_MAP_DIR / "output" / "results" / "DIY_Batch02_Matched_20260703_102756.csv", "diy_batch_02"),
    ("NotFound", GOOGLE_MAP_DIR / "output" / "results" / "DIY_Batch01_NotFound_20260702_222334.csv", "diy_batch_01"),
    ("NotFound", GOOGLE_MAP_DIR / "output" / "results" / "DIY_Batch02_NotFound_20260703_102756.csv", "diy_batch_02"),
]

for kind, path, source_label in DIY_FILES:
    if not path.exists():
        print(f"  SKIP (not found): {path.name}")
        continue
    df = pd.read_csv(path)
    if kind == "Matched":
        for _, row in df.iterrows():
            name = row["Talabat_Restaurant_Name"]
            matches_by_name[name].append({
                "Google_Business_Name": row.get("Google_Business_Name"),
                "Address": row.get("Address"),
                "Phone": row.get("Phone"),
                "Website": row.get("Website"),
                "Google_Maps_URL": row.get("Google_Maps_URL"),
                "Place_ID": None,
                "Rating": None,
                "Review_Count": None,
                "Emirate": None,
                "Source": source_label,
            })
    else:
        for name in df["Talabat_Restaurant_Name"]:
            not_found_names.add(name)
    print(f"  Loaded {kind}: {path.name} ({len(df)} rows)")

# A name could appear in both matched (from one batch) and not-found (from
# a different/earlier batch attempt) -- matched always wins.
not_found_names -= set(matches_by_name.keys())

total_matched_names = len(matches_by_name)
total_notfound_names = len(not_found_names)
print(f"\nTotal unique names with a real Google match : {total_matched_names}")
print(f"Total unique names confirmed not found       : {total_notfound_names}")

# -- Build the master mapping, one row per Talabat branch (or more, if a --
# -- name has multiple Google locations) -------------------------------------
output_rows = []
searched_names = set(matches_by_name.keys()) | not_found_names

for r in talabat_rows:
    name = r["_clean_name"]
    if name in EXCLUDE_DONE:
        continue  # KFC/Domino's excluded from this mapping per user request

    base = {
        "Branch_ID": r["branch_id"],
        "Restaurant_ID": r["restaurant_id"],
        "Talabat_Restaurant_Name_Raw": r["restaurant_name"],
        "Talabat_Restaurant_Name_Clean": name,
        "Talabat_Map_URL": r["map_url"],
        "Area_ID": r.get("area_id"),
        "Area_Name": r.get("area_name"),
    }

    if name in matches_by_name:
        google_matches = matches_by_name[name]
        total_locations = len(google_matches)
        brand_type = "Independent" if total_locations == 1 else "Chain (Multiple Locations)"
        for gm in google_matches:
            output_rows.append({
                **base,
                "Status": "Matched",
                "Brand_Type": brand_type,
                "Total_Google_Locations_Found_For_This_Name": total_locations,
                **gm,
            })
    elif name in not_found_names:
        output_rows.append({
            **base,
            "Status": "Not Found",
            "Brand_Type": None,
            "Total_Google_Locations_Found_For_This_Name": 0,
            "Google_Business_Name": None, "Address": None, "Phone": None,
            "Website": None, "Google_Maps_URL": None, "Place_ID": None,
            "Rating": None, "Review_Count": None, "Emirate": None, "Source": None,
        })
    else:
        output_rows.append({
            **base,
            "Status": "Not Yet Searched",
            "Brand_Type": None,
            "Total_Google_Locations_Found_For_This_Name": None,
            "Google_Business_Name": None, "Address": None, "Phone": None,
            "Website": None, "Google_Maps_URL": None, "Place_ID": None,
            "Rating": None, "Review_Count": None, "Emirate": None, "Source": None,
        })

df_out = pd.DataFrame(output_rows)

csv_path = OUT_DIR / "Master_Talabat_Google_Mapping.csv"
json_path = OUT_DIR / "Master_Talabat_Google_Mapping.json"
df_out.to_csv(csv_path, index=False, encoding="utf-8-sig")
df_out.to_json(json_path, orient="records", indent=2, force_ascii=False)

# -- Summary -------------------------------------------------------------------
print(f"\n{'='*70}")
print(f"Total output rows                : {len(df_out):,}  (branches x their Google matches)")
print()
status_counts = df_out.drop_duplicates(subset=["Branch_ID"])["Status"].value_counts()
print("Unique BRANCHES by status:")
for status, cnt in status_counts.items():
    print(f"  {status:<20} {cnt:,}")
print()
chain_names = {n: len(v) for n, v in matches_by_name.items() if len(v) > 1}
print(f"Talabat names confirmed as CHAINS (2+ Google locations): {len(chain_names)}")
top_chains = sorted(chain_names.items(), key=lambda x: -x[1])[:10]
print("Top 10 by location count:")
for name, cnt in top_chains:
    print(f"  {cnt:>3}  {name}")
print(f"{'='*70}")
print(f"\nSaved -> {csv_path}")
print(f"Saved -> {json_path}")
