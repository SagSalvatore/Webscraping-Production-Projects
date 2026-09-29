"""
3_merge_uae_results.py
------------------------
Merge + schema-assembly step for the "many different restaurant names"
scraping mode (as opposed to process_brand.py, which filters ONE brand
name across many area-sweeps — used for KFC/Domino's).

Reads: brands/<brand>/output/raw/dataset_*.json (raw Apify results,
       produced by run_brand_scraper.py) + the same brand's input.json
       (for query_metadata / Talabat traceability lookup)

Produces: brands/<brand>/output/<brand>_processed.csv + .json

Output schema (matches plan.md "Final Output Fields"):
  Talabat_Restaurant_Name, Brand_Type, Talabat_Branch_Count,
  Google_Business_Name, Address, Phone, Website, Google_Maps_URL,
  Place_ID, Latitude, Longitude, Rating, Review_Count, Category,
  All_Categories, Business_Status, Emirate, Talabat_Source_URL

Brand_Type is derived AFTER results return:
  - allowlist tier   -> "Global Chain"
  - 2+ Google results for the same query -> "Local Chain"
  - exactly 1 Google result               -> "Independent"
  - 0 Google results                      -> not in output; logged as unmatched

Usage:
  python 3_merge_uae_results.py --brand uae_validation_default
  python 3_merge_uae_results.py --brand uae_validation_chains
"""

import argparse
import glob
import json
import re
import sys
from collections import defaultdict, Counter
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

# Minimum title-vs-search-term similarity to keep a result. Catches cases
# "only_includes" still lets through — e.g. searching "Over" matching
# "Game Over Plus" (electronics shop) or "Foot over bridge" purely because
# they contain the substring "over". SequenceMatcher ratio penalizes titles
# that are mostly OTHER text beyond the search term, unlike a simple
# substring check which only cares that it's present anywhere.
RELEVANCE_THRESHOLD = 0.5


def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def title_match_score(search_term: str, title: str) -> float:
    a, b = normalize(search_term), normalize(title)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()

sys.stdout.reconfigure(encoding="utf-8")

APIFY_ROOT = Path(__file__).resolve().parent.parent

_UAE_SUFFIX = re.compile(r",?\s*(united arab emirates|u\.a\.e\.?|uae)\s*$", re.IGNORECASE)

_EMIRATE_PATTERNS = [
    ("Al Ain",         "Abu Dhabi"),
    ("Khor Fakkan",    "Sharjah"),
    ("Kalba",          "Sharjah"),
    ("Dubai",          "Dubai"),
    ("Abu Dhabi",      "Abu Dhabi"),
    ("Sharjah",        "Sharjah"),
    ("Ajman",          "Ajman"),
    ("Ras Al Khaimah", "Ras Al Khaimah"),
    ("Fujairah",       "Fujairah"),
    ("Umm Al Quwain",  "Umm Al Quwain"),
]


def emirate_from_address(address: str) -> str:
    """Authoritative emirate — derived from Google's own returned address,
    not our pre-search lat/lon guess."""
    if not address:
        return "Unknown"
    addr = _UAE_SUFFIX.sub("", address)
    for keyword, emirate in _EMIRATE_PATTERNS:
        if keyword.lower() in addr.lower():
            return emirate
    return "Unknown"


def load_raw_items(brand: str) -> list:
    raw_dir = APIFY_ROOT / "brands" / brand / "output" / "raw"
    files = sorted(glob.glob(str(raw_dir / "dataset_*.json")))
    files = [f for f in files if "merged_all" not in f]  # avoid double-counting if a merged file also exists
    if not files:
        # fall back to merged file if per-group files were cleaned up
        merged = sorted(glob.glob(str(raw_dir / "dataset_*merged_all.json")))
        files = merged

    items = []
    for f in files:
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        items.extend(data)
    print(f"Loaded {len(items)} raw items from {len(files)} file(s)")
    return items


def load_query_metadata(brand: str) -> dict:
    input_path = APIFY_ROOT / "brands" / brand / "input.json"
    config = json.loads(input_path.read_text(encoding="utf-8"))
    meta = config.get("query_metadata", [])
    return {m["query"]: m for m in meta}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand", required=True)
    args = parser.parse_args()
    brand = args.brand

    items = load_raw_items(brand)
    if not items:
        print("No raw items found. Has run_brand_scraper.py been run yet?")
        sys.exit(1)

    metadata = load_query_metadata(brand)
    print(f"Loaded metadata for {len(metadata)} search queries")

    # Group results by the search string that produced them
    by_query = defaultdict(list)
    unmatched_query_field_count = 0
    for it in items:
        q = it.get("searchString") or it.get("searchQuery") or it.get("searchTerm")
        if not q:
            unmatched_query_field_count += 1
            continue
        by_query[q].append(it)

    if unmatched_query_field_count:
        print(f"WARNING: {unmatched_query_field_count} items had no identifiable search-query field "
              f"(checked searchString/searchQuery/searchTerm) — check actual field name in raw JSON")

    rows = []
    queries_with_zero_results = []
    queries_all_filtered_out = []
    noise_dropped = 0

    for query, meta in metadata.items():
        raw_results = by_query.get(query, [])

        if len(raw_results) == 0:
            queries_with_zero_results.append(query)
            continue

        # Relevance filter: keep only results whose title genuinely
        # resembles the search term, not just contains it as a substring
        # (catches "Over" -> "Game Over Plus", "Foot over bridge", etc.)
        base_name = meta.get("restaurant_name_clean", query)
        scored = [(r, title_match_score(base_name, r.get("title") or r.get("name") or "")) for r in raw_results]
        results = [r for r, score in scored if score >= RELEVANCE_THRESHOLD]
        noise_dropped += len(raw_results) - len(results)

        if not results:
            queries_all_filtered_out.append(query)
            continue

        n_results = len(results)
        tier = meta.get("tier", "default")
        if tier == "known_chain":
            brand_type = "Global Chain"
        elif n_results >= 2:
            brand_type = "Local Chain"
        else:
            brand_type = "Independent"

        for r in results:
            address = r.get("address") or r.get("fullAddress") or ""
            rows.append({
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
                "Emirate_Search_Hint": meta.get("emirate_hint"),
                "Talabat_Source_URL": meta.get("sample_map_url"),
                "Search_Query_Used": query,
            })

    df = pd.DataFrame(rows)
    if df.empty:
        print("No rows produced — check field-name mapping against actual raw JSON structure.")
        sys.exit(1)

    before_dedup = len(df)
    df = df.drop_duplicates(subset=["Place_ID"], keep="first")
    after_dedup = len(df)

    out_dir = APIFY_ROOT / "brands" / brand / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{brand}_processed.csv"
    json_path = out_dir / f"{brand}_processed.json"

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_json(json_path, orient="records", indent=2, force_ascii=False)

    print(f"\n{'='*60}")
    print(f"Raw items                       : {len(items)}")
    print(f"Queries with metadata            : {len(metadata)}")
    print(f"Queries with 0 Google results     : {len(queries_with_zero_results)}")
    print(f"Queries fully filtered as noise    : {len(queries_all_filtered_out)}  (all results scored below {RELEVANCE_THRESHOLD} similarity)")
    print(f"Noisy result rows dropped         : {noise_dropped}  (relevance filter, e.g. 'Over' -> 'Game Over Plus')")
    print(f"Rows before place_id dedup        : {before_dedup}")
    print(f"Rows after place_id dedup         : {after_dedup}")
    print()
    print("Brand_Type breakdown:")
    for bt, cnt in Counter(df["Brand_Type"]).most_common():
        print(f"  {bt:<15} {cnt}")
    print()
    print("Emirate breakdown (from Google address, authoritative):")
    for em, cnt in Counter(df["Emirate"]).most_common():
        print(f"  {em:<15} {cnt}")
    print()
    print(f"Field completeness:")
    for col in ["Phone", "Website", "Rating", "Review_Count"]:
        pct = df[col].notna().mean() * 100
        print(f"  {col:<15} {pct:.1f}%")
    print(f"{'='*60}")
    print(f"\nSaved -> {csv_path}")
    print(f"Saved -> {json_path}")

    if queries_with_zero_results:
        zero_path = out_dir / f"{brand}_zero_results.json"
        zero_path.write_text(json.dumps(queries_with_zero_results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Zero-result queries logged -> {zero_path}")


if __name__ == "__main__":
    main()
