"""
build_bonus_directory.py
----------------------------
Combines all "bonus" entities discovered as a byproduct of searching
for Talabat restaurants -- real UAE businesses that came up in search
results but weren't the entity we were looking for, so they were never
merged into the main Talabat-linked directory (map/output/).

This is a genuinely useful separate deliverable: extra UAE business
listings for the directory, beyond what Talabat itself lists.

Output columns (as requested): Name, Address, Phone, Website,
Google_Maps_URL, Category, Latitude, Longitude.

Sources combined:
  - Apify cumulative other-entities (has full detail: address, phone,
    website, category, geocoords)
  - DIY Playwright other-entities (name + URL only -- the DIY scraper
    deliberately skips detail-page visits for non-matches to save
    bandwidth, so those fields are blank for DIY-sourced rows)

Deduplicated by Place_ID where available (Apify rows); DIY rows have
no Place_ID so are deduplicated by (Google_Business_Name, Google_Maps_URL).
"""
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
APIFY_DIR = ROOT / "apify"
GOOGLE_MAP_DIR = ROOT / "Google_map"
OUT_DIR = Path(__file__).parent / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

COLUMNS = ["Name", "Address", "Phone", "Website", "Google_Maps_URL", "Category", "Latitude", "Longitude", "Source"]

rows = []

# -- Apify cumulative other-entities (full detail) ---------------------------
apify_other = pd.read_csv(APIFY_DIR / "output" / "full_summary" / "All_OtherEntities_So_Far.csv")
print(f"Apify other-entities loaded: {len(apify_other)} rows")

for _, r in apify_other.iterrows():
    rows.append({
        "Name": r.get("Google_Business_Name"),
        "Address": r.get("Address"),
        "Phone": r.get("Phone"),
        "Website": r.get("Website"),
        "Google_Maps_URL": r.get("Google_Maps_URL"),
        "Category": r.get("Category"),
        "Latitude": r.get("Latitude"),
        "Longitude": r.get("Longitude"),
        "Source": "apify",
        "_place_id": r.get("Place_ID"),
    })

# -- DIY other-entities (name + URL only, no detail-page data) --------------
diy_files = [
    GOOGLE_MAP_DIR / "output" / "results" / "DIY_Batch01_OtherEntities_20260702_222334.csv",
    GOOGLE_MAP_DIR / "output" / "results" / "DIY_Batch02_OtherEntities_20260703_102756.csv",
]
diy_count = 0
for path in diy_files:
    if not path.exists():
        print(f"  SKIP (not found): {path.name}")
        continue
    df = pd.read_csv(path)
    diy_count += len(df)
    for _, r in df.iterrows():
        rows.append({
            "Name": r.get("Google_Business_Name"),
            "Address": None,
            "Phone": None,
            "Website": None,
            "Google_Maps_URL": r.get("Google_Maps_URL"),
            "Category": None,
            "Latitude": None,
            "Longitude": None,
            "Source": "diy_playwright",
            "_place_id": None,
        })
print(f"DIY other-entities loaded: {diy_count} rows")

df_out = pd.DataFrame(rows)
before = len(df_out)

# Dedup: Apify rows by Place_ID (reliable unique key); DIY rows (no
# Place_ID) by Name+URL since that's all we have for them.
has_place_id = df_out["_place_id"].notna()
apify_part = df_out[has_place_id].drop_duplicates(subset=["_place_id"])
diy_part = df_out[~has_place_id].drop_duplicates(subset=["Name", "Google_Maps_URL"])
df_out = pd.concat([apify_part, diy_part], ignore_index=True)
df_out = df_out.drop(columns=["_place_id"])

after = len(df_out)

csv_path = OUT_DIR / "Bonus_Directory_Entities.csv"
json_path = OUT_DIR / "Bonus_Directory_Entities.json"
df_out.to_csv(csv_path, index=False, encoding="utf-8-sig")
df_out.to_json(json_path, orient="records", indent=2, force_ascii=False)

print(f"\n{'='*60}")
print(f"Rows before dedup : {before}")
print(f"Rows after dedup  : {after}  (unique bonus businesses)")
print()
print("By source:")
print(df_out["Source"].value_counts())
print()
print("Field completeness:")
for col in ["Address", "Phone", "Website", "Category", "Latitude"]:
    pct = df_out[col].notna().mean() * 100
    print(f"  {col:<15} {pct:.1f}%")
print(f"{'='*60}")
print(f"\nSaved -> {csv_path}")
print(f"Saved -> {json_path}")
