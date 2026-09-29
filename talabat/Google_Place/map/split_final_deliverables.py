"""
split_final_deliverables.py
------------------------------
Splits the master mapping (map/output/Master_Talabat_Google_Mapping.csv,
which already joins apify/Complete_list_apify.csv against every Apify +
DIY result so far) into the client-facing deliverable files:

  1. Confirmed_Matched_Branches.csv/json  -- Status == "Matched"
     (branch_id, restaurant_id kept, plus every Apify-sourced column:
     address, phone, website, google maps url, place id, rating, etc.)
  2. NotFound_Branches.csv/json           -- Status == "Not Found"
     (same column schema as #1, Apify columns empty since no match)
  3. NotYetSearched_Branches.csv/json     -- Status == "Not Yet Searched"
     (the remaining un-tested pool, at branch level, for later recovery)
  4. New_Discovered_Listings.csv/json     -- category-sanity-checked
     "bonus" entities Apify surfaced that are NOT part of the original
     Talabat list (copied from bonus_directory/output, already filtered
     down to genuine food-service categories)

Run after every refresh of map/build_master_mapping.py.
"""
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
MAP_OUT = Path(__file__).parent / "output"
BONUS_OUT = ROOT / "bonus_directory" / "output"

df = pd.read_csv(MAP_OUT / "Master_Talabat_Google_Mapping.csv", encoding="utf-8-sig")
total_branches = df["Branch_ID"].nunique()
print(f"Total Talabat branches in master mapping (excl. KFC/Domino's): {total_branches:,}")

# 1. Confirmed / Matched ------------------------------------------------------
matched = df[df["Status"] == "Matched"].copy()
matched.to_csv(MAP_OUT / "Confirmed_Matched_Branches.csv", index=False, encoding="utf-8-sig")
matched.to_json(MAP_OUT / "Confirmed_Matched_Branches.json", orient="records", indent=2, force_ascii=False)

matched_branches = matched["Branch_ID"].nunique()
has_address = matched["Address"].notna().sum()
print(f"\nCONFIRMED MATCHED: {len(matched):,} rows -> {matched_branches:,} unique branches")
print(f"  With Address populated : {has_address:,} ({has_address/len(matched)*100:.1f}%)")
print(f"  With Phone populated    : {matched['Phone'].notna().sum():,} ({matched['Phone'].notna().sum()/len(matched)*100:.1f}%)")
print(f"  With Website populated  : {matched['Website'].notna().sum():,} ({matched['Website'].notna().sum()/len(matched)*100:.1f}%)")

# 2. Not Found -----------------------------------------------------------------
not_found = df[df["Status"] == "Not Found"].copy()
not_found.to_csv(MAP_OUT / "NotFound_Branches.csv", index=False, encoding="utf-8-sig")
not_found.to_json(MAP_OUT / "NotFound_Branches.json", orient="records", indent=2, force_ascii=False)
print(f"\nNOT FOUND: {len(not_found):,} branches")

# 3. Not Yet Searched (remaining pool, branch level) ---------------------------
not_yet = df[df["Status"] == "Not Yet Searched"].copy()
not_yet.to_csv(MAP_OUT / "NotYetSearched_Branches.csv", index=False, encoding="utf-8-sig")
not_yet.to_json(MAP_OUT / "NotYetSearched_Branches.json", orient="records", indent=2, force_ascii=False)
unique_names_remaining = not_yet["Talabat_Restaurant_Name_Clean"].nunique()
print(f"\nNOT YET SEARCHED: {len(not_yet):,} branches -> {unique_names_remaining:,} unique names (saved for later)")

# 4. New / Bonus discovered listings (category-sanity-checked already) --------
bonus_df = pd.read_csv(BONUS_OUT / "Bonus_Directory_Entities.csv", encoding="utf-8-sig")
bonus_df.to_csv(MAP_OUT / "New_Discovered_Listings.csv", index=False, encoding="utf-8-sig")
bonus_df.to_json(MAP_OUT / "New_Discovered_Listings.json", orient="records", indent=2, force_ascii=False)
print(f"\nNEW DISCOVERED LISTINGS (category-sanity-checked, not in Talabat): {len(bonus_df):,} rows")

print(f"\n{'='*70}")
print("Saved final deliverables ->")
for name in [
    "Confirmed_Matched_Branches.csv", "Confirmed_Matched_Branches.json",
    "NotFound_Branches.csv", "NotFound_Branches.json",
    "NotYetSearched_Branches.csv", "NotYetSearched_Branches.json",
    "New_Discovered_Listings.csv", "New_Discovered_Listings.json",
]:
    print(f"  {MAP_OUT / name}")
print(f"{'='*70}")
