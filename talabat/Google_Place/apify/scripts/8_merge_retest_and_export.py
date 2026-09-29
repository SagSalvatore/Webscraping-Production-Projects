"""
8_merge_retest_and_export.py
--------------------------------
Merges the 500-name "UAE Restaurant" suffix retest across the 4 keys,
applies the same relevance filter, and exports BOTH:
  - output/retest_500/UAE_Retest_500.xlsx  (formatted, for review)
  - output/retest_500/UAE_Retest_500.json  (raw structured data)

Also reports zero-result rate vs the earlier confirmation test (49/100
= 49% zero-result) so the user can judge whether the "UAE Restaurant"
suffix fix actually reduced the zero-result problem.
"""
import glob
import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import xlsxwriter

sys.stdout.reconfigure(encoding="utf-8")

APIFY_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = APIFY_ROOT / "output" / "retest_500"
OUT_DIR.mkdir(parents=True, exist_ok=True)

RELEVANCE_THRESHOLD = 0.5

BRANDS = [
    "uae_retest_elakiya_chandrasekhar",
    "uae_retest_venkatesh_bestha",
    "uae_retest_ravi_chandra",
    "uae_retest_thejas_k_sabu",
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
total_queries = 0
zero_result_queries = []

for brand in BRANDS:
    brand_dir = APIFY_ROOT / "brands" / brand
    input_path = brand_dir / "input.json"
    raw_dir = brand_dir / "output" / "raw"
    if not input_path.exists():
        print(f"SKIP {brand}: not found (still running?)")
        continue

    config = json.loads(input_path.read_text(encoding="utf-8"))
    metadata = {m["query"]: m for m in config.get("query_metadata", [])}
    total_queries += len(metadata)

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

    kept = 0
    for query, meta in metadata.items():
        raw_results = by_query.get(query, [])
        if not raw_results:
            zero_result_queries.append(meta.get("restaurant_name_clean", query))
            continue

        base_name = meta.get("restaurant_name_clean", query)
        scored = [(r, title_match_score(base_name, r.get("title") or r.get("name") or "")) for r in raw_results]
        results = [r for r, score in scored if score >= RELEVANCE_THRESHOLD]
        if not results:
            zero_result_queries.append(base_name)
            continue

        brand_type = "Local Chain" if len(results) >= 2 else "Independent"

        for r in results:
            address = r.get("address") or r.get("fullAddress") or ""
            all_rows.append({
                "Talabat_Restaurant_Name": base_name,
                "Search_Query_Used": query,
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
                "Business_Status": r.get("permanentlyClosed") and "Closed" or "Active",
                "Emirate": emirate_from_address(address),
                "Talabat_Source_URL": meta.get("sample_map_url"),
            })
            kept += 1
    print(f"{brand:<38} queries={len(metadata):<5} kept_rows={kept}")

df = pd.DataFrame(all_rows)
before_dedup = len(df)
if not df.empty:
    df = df.drop_duplicates(subset=["Place_ID"])
after_dedup = len(df)

matched_queries = total_queries - len(zero_result_queries)
zero_result_rate = len(zero_result_queries) / total_queries * 100 if total_queries else 0

print(f"\n{'='*60}")
print(f"Total queries tested        : {total_queries}")
print(f"Queries with 0 result       : {len(zero_result_queries)}  ({zero_result_rate:.1f}%)")
print(f"Queries with a match        : {matched_queries}  ({100-zero_result_rate:.1f}%)")
print(f"Rows before place_id dedup  : {before_dedup}")
print(f"Rows after place_id dedup   : {after_dedup}")
print()
print("COMPARISON vs earlier confirmation test (bare-name query, no suffix):")
print(f"  Earlier test zero-result rate : 49%  (49 of 100 default-tier names)")
print(f"  This retest zero-result rate  : {zero_result_rate:.1f}%  ({len(zero_result_queries)} of {total_queries})")
if not df.empty:
    print()
    print("Brand_Type breakdown:")
    for bt, cnt in Counter(df["Brand_Type"]).most_common():
        print(f"  {bt:<15} {cnt}")
    print()
    print("Field completeness:")
    for col in ["Phone", "Website", "Rating", "Review_Count"]:
        pct = df[col].notna().mean() * 100
        print(f"  {col:<15} {pct:.1f}%")
print(f"{'='*60}")

# -- Save JSON --------------------------------------------------------------
json_path = OUT_DIR / "UAE_Retest_500.json"
df.to_json(json_path, orient="records", indent=2, force_ascii=False)
print(f"\nSaved JSON -> {json_path}")

zero_path = OUT_DIR / "UAE_Retest_500_zero_results.json"
zero_path.write_text(json.dumps(zero_result_queries, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Saved zero-result list -> {zero_path}")

# -- Save formatted Excel ----------------------------------------------------
xlsx_path = OUT_DIR / "UAE_Retest_500.xlsx"
wb = xlsxwriter.Workbook(str(xlsx_path))

hdr_fmt = wb.add_format({"bold": True, "font_color": "white", "bg_color": "#2C3E8C",
                          "align": "center", "border": 1, "font_size": 10})
body_fmt = wb.add_format({"font_size": 9, "border": 1, "valign": "vcenter"})
body_alt = wb.add_format({"font_size": 9, "border": 1, "valign": "vcenter", "bg_color": "#F7F8FA"})
title_fmt = wb.add_format({"bold": True, "font_size": 14, "font_color": "white", "bg_color": "#1A1A2E", "align": "center"})

ws = wb.add_worksheet("Retest Results")
ws.hide_gridlines(2)
ws.merge_range(0, 0, 0, len(df.columns) - 1 if not df.empty else 10,
               f"UAE Restaurant Retest — 'UAE Restaurant' Suffix Fix (500 names)", title_fmt)
ws.set_row(0, 26)

if not df.empty:
    for ci, col in enumerate(df.columns):
        ws.write(2, ci, col, hdr_fmt)
        ws.set_column(ci, ci, 22)
    for ri, row in enumerate(df.itertuples(index=False)):
        fmt = body_fmt if ri % 2 == 0 else body_alt
        for ci, val in enumerate(row):
            ws.write(ri + 3, ci, val if pd.notna(val) else "", fmt)
    ws.autofilter(2, 0, 2 + len(df), len(df.columns) - 1)
    ws.freeze_panes(3, 0)

# Summary sheet
ws2 = wb.add_worksheet("Summary")
ws2.set_column(0, 1, 35)
rows_summary = [
    ("Total queries tested", total_queries),
    ("Queries with 0 result", len(zero_result_queries)),
    ("Zero-result rate", f"{zero_result_rate:.1f}%"),
    ("Earlier test zero-result rate (no suffix)", "49.0%"),
    ("Rows after dedup (final records)", after_dedup),
]
ws2.write(0, 0, "Metric", hdr_fmt)
ws2.write(0, 1, "Value", hdr_fmt)
for i, (k, v) in enumerate(rows_summary, start=1):
    ws2.write(i, 0, k, body_fmt)
    ws2.write(i, 1, v, body_fmt)

wb.close()
print(f"Saved Excel -> {xlsx_path}")
