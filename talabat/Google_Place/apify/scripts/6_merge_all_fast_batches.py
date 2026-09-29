"""
6_merge_all_fast_batches.py
------------------------------
Consolidates all 20 brand folders (2 confirmation + 18 fast-distributed
batches) into ONE final master CSV, reusing the same relevance-filter
and schema logic as 3_merge_uae_results.py.
"""
import glob
import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

APIFY_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = APIFY_ROOT / "output" / "master"
OUT_DIR.mkdir(parents=True, exist_ok=True)

RELEVANCE_THRESHOLD = 0.5

BRANDS = [
    "uae_confirm_default", "uae_confirm_chains",
    "uae_fast_chain_01", "uae_fast_chain_02", "uae_fast_chain_03",
    "uae_fast_chain_04", "uae_fast_chain_05", "uae_fast_chain_06",
    "uae_fast_default_07", "uae_fast_default_08", "uae_fast_default_09",
    "uae_fast_default_10", "uae_fast_default_11", "uae_fast_default_12",
    "uae_fast_default_13", "uae_fast_default_14", "uae_fast_default_15",
    "uae_fast_default_16", "uae_fast_default_17", "uae_fast_default_18",
]

_UAE_SUFFIX = re.compile(r",?\s*(united arab emirates|u\.a\.e\.?|uae)\s*$", re.IGNORECASE)
_EMIRATE_PATTERNS = [
    ("Al Ain", "Abu Dhabi"), ("Khor Fakkan", "Sharjah"), ("Kalba", "Sharjah"),
    ("Dubai", "Dubai"), ("Abu Dhabi", "Abu Dhabi"), ("Sharjah", "Sharjah"),
    ("Ajman", "Ajman"), ("Ras Al Khaimah", "Ras Al Khaimah"),
    ("Fujairah", "Fujairah"), ("Umm Al Quwain", "Umm Al Quwain"),
]


def emirate_from_address(address):
    if not address:
        return "Unknown"
    addr = _UAE_SUFFIX.sub("", address)
    for kw, em in _EMIRATE_PATTERNS:
        if kw.lower() in addr.lower():
            return em
    return "Unknown"


def normalize(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def title_match_score(search_term, title):
    a, b = normalize(search_term), normalize(title)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


all_rows = []
per_brand_stats = []

for brand in BRANDS:
    brand_dir = APIFY_ROOT / "brands" / brand
    input_path = brand_dir / "input.json"
    raw_dir = brand_dir / "output" / "raw"
    if not input_path.exists():
        print(f"SKIP {brand}: no input.json")
        continue

    config = json.loads(input_path.read_text(encoding="utf-8"))
    metadata = {m["query"]: m for m in config.get("query_metadata", [])}

    files = sorted(glob.glob(str(raw_dir / "dataset_*.json")))
    files = [f for f in files if "merged_all" not in f and "snapshot" not in f]
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

    brand_rows = 0
    for query, meta in metadata.items():
        raw_results = by_query.get(query, [])
        if not raw_results:
            continue
        base_name = meta.get("restaurant_name_clean", query)
        scored = [(r, title_match_score(base_name, r.get("title") or r.get("name") or "")) for r in raw_results]
        results = [r for r, score in scored if score >= RELEVANCE_THRESHOLD]
        if not results:
            continue

        tier = meta.get("tier", "default")
        if tier == "known_chain":
            brand_type = "Global Chain"
        elif len(results) >= 2:
            brand_type = "Local Chain"
        else:
            brand_type = "Independent"

        for r in results:
            address = r.get("address") or r.get("fullAddress") or ""
            all_rows.append({
                "Talabat_Restaurant_Name": meta.get("restaurant_name_clean", query),
                "Brand_Type": brand_type,
                "Talabat_Branch_Count": meta.get("talabat_branch_count"),
                "Google_Business_Name": r.get("title") or r.get("name"),
                "Address": address,
                "Phone": r.get("phone") or r.get("phoneUnformatted"),
                "Website": r.get("website"),
                "Google_Maps_URL": r.get("url") or (
                    f"https://www.google.com/maps/place/?q=place_id:{r.get('placeId')}" if r.get("placeId") else None
                ),
                "Place_ID": r.get("placeId"),
                "Latitude": r.get("location", {}).get("lat") if isinstance(r.get("location"), dict) else r.get("latitude"),
                "Longitude": r.get("location", {}).get("lng") if isinstance(r.get("location"), dict) else r.get("longitude"),
                "Rating": r.get("totalScore") or r.get("rating"),
                "Review_Count": r.get("reviewsCount") or r.get("reviewCount"),
                "Category": r.get("categoryName") or r.get("category"),
                "All_Categories": ", ".join(r.get("categories", [])) if r.get("categories") else None,
                "Business_Status": r.get("permanentlyClosed") and "Closed" or "Active",
                "Emirate": emirate_from_address(address),
                "Talabat_Source_URL": meta.get("sample_map_url"),
                "Source_Batch": brand,
            })
            brand_rows += 1

    per_brand_stats.append((brand, len(items), brand_rows))
    print(f"{brand:<28} raw={len(items):<6} kept={brand_rows}")

df = pd.DataFrame(all_rows)
before_dedup = len(df)
df = df.drop_duplicates(subset=["Place_ID"])
after_dedup = len(df)

csv_path = OUT_DIR / "UAE_Restaurant_Directory_MASTER.csv"
df.to_csv(csv_path, index=False, encoding="utf-8-sig")

print(f"\n{'='*60}")
print(f"Rows before place_id dedup : {before_dedup}")
print(f"Rows after place_id dedup  : {after_dedup}")
print()
print("Brand_Type breakdown:")
for bt, cnt in Counter(df["Brand_Type"]).most_common():
    print(f"  {bt:<15} {cnt}")
print()
print("Emirate breakdown:")
for em, cnt in Counter(df["Emirate"]).most_common():
    print(f"  {em:<15} {cnt}")
print()
print("Field completeness:")
for col in ["Phone", "Website", "Rating", "Review_Count"]:
    pct = df[col].notna().mean() * 100
    print(f"  {col:<15} {pct:.1f}%")
print(f"{'='*60}")
print(f"\nSaved -> {csv_path}")
