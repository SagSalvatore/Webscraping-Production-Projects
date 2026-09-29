"""
9_merge_retest_strict.py
---------------------------
Re-processes the ALREADY-COLLECTED 500-name retest raw data (no new
Apify spend) with a much stricter entity-matching rule than the old
SequenceMatcher relevance filter.

Problem found by manual review: loose similarity scoring let through
results that share a generic word but are a DIFFERENT business —
e.g. searching "Bao Kitchen" returned the real "BAO Kitchen" AND
unrelated "Korean Kitchen Restaurant" / "Bentley Kitchen" (all just
share the word "Kitchen").

Fix: strip generic filler words + non-Latin script from both the
Talabat name and the Google title, then require the remaining CORE
WORDS of one to be fully contained in the other. "bao" only matches
"bao", not "korean" or "bentley".

Outputs:
  output/retest_500_strict/UAE_Retest_500_Matched.xlsx / .json
      - one row per Talabat name: real match OR "Not Found" placeholder
  output/retest_500_strict/UAE_Other_UAE_Entities.xlsx / .json
      - genuine other businesses discovered but NOT the searched entity
        (kept as bonus data, never merged into the Talabat-linked directory)
"""
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import xlsxwriter

sys.stdout.reconfigure(encoding="utf-8")

APIFY_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = APIFY_ROOT / "output" / "retest_500_strict"
OUT_DIR.mkdir(parents=True, exist_ok=True)

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


def core_token_list(name: str) -> list:
    """Strip non-Latin script, pipe-separated suffixes, punctuation, and
    generic filler words -> the remaining distinctive core words, IN
    ORDER (order matters for the prefix check below)."""
    if not name:
        return []
    name = name.split("|")[0]                      # cut at "Sayf Wa Kayf | صَيْف..."
    name = re.sub(r"[^\x00-\x7f]", "", name)        # drop Arabic/non-ASCII
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    return [w for w in name.split() if w and w not in STOPWORDS]


def is_exact_entity_match(talabat_name: str, google_title: str) -> bool:
    """Google's core title must START WITH the Talabat core phrase, in
    order. This is the key fix over plain set-containment: it correctly
    accepts "Mumbai Masti" -> "Mumbai Masti Juice Center" (real match,
    extra descriptive words appended after) and "Monk" -> "Monk Indo
    Chinese Restaurant - Dubai Silicon Oasis" (real match, cuisine/branch
    info appended), while still rejecting "Bao" -> "Dragon Bao Bao
    Restaurant" (the shared word "bao" is NOT at the start, so it's a
    coincidental overlap with a different restaurant, not the same one)."""
    t = core_token_list(talabat_name)
    g = core_token_list(google_title)
    if not t or not g or len(g) < len(t):
        return False
    return g[:len(t)] == t


matched_rows = []
other_entity_rows = []
stats = {"total_queries": 0, "exact_match_found": 0, "not_found": 0, "other_entities_discovered": 0}

for brand in BRANDS:
    brand_dir = APIFY_ROOT / "brands" / brand
    input_path = brand_dir / "input.json"
    raw_dir = brand_dir / "output" / "raw"
    if not input_path.exists():
        print(f"SKIP {brand}: not found")
        continue

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
        stats["total_queries"] += 1
        base_name = meta.get("restaurant_name_clean", query)
        raw_results = by_query.get(query, [])

        matched_result = None
        for r in raw_results:
            title = r.get("title") or r.get("name") or ""
            if is_exact_entity_match(base_name, title):
                matched_result = r
            else:
                # Genuine different business discovered along the way
                address = r.get("address") or r.get("fullAddress") or ""
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
                    "Rating": r.get("totalScore") or r.get("rating"),
                    "Review_Count": r.get("reviewsCount") or r.get("reviewCount"),
                    "Category": r.get("categoryName") or r.get("category"),
                    "Emirate": emirate_from_address(address),
                })
                stats["other_entities_discovered"] += 1

        if matched_result:
            stats["exact_match_found"] += 1
            r = matched_result
            address = r.get("address") or r.get("fullAddress") or ""
            matched_rows.append({
                "Talabat_Restaurant_Name": base_name,
                "Match_Status": "Matched",
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
                "Emirate_Search_Hint": meta.get("emirate_hint"),
                "Talabat_Source_URL": meta.get("sample_map_url"),
                "Search_Query_Used": query,
            })
        else:
            stats["not_found"] += 1
            matched_rows.append({
                "Talabat_Restaurant_Name": base_name,
                "Match_Status": "Not Found",
                "Talabat_Branch_Count": meta.get("talabat_branch_count"),
                "Google_Business_Name": base_name,   # keep Talabat's own name, not blank
                "Address": "Not Found",
                "Phone": "Not Found",
                "Website": "Not Found",
                "Google_Maps_URL": "Not Found",
                "Place_ID": "Not Found",
                "Latitude": None,
                "Longitude": None,
                "Rating": "Not Found",
                "Review_Count": "Not Found",
                "Category": "Not Found",
                "Business_Status": "Not Found",
                "Emirate": meta.get("emirate_hint", "Not Found"),   # fallback to our lat/lon-derived hint
                "Emirate_Search_Hint": meta.get("emirate_hint"),
                "Talabat_Source_URL": meta.get("sample_map_url"),
                "Search_Query_Used": query,
            })

matched_df = pd.DataFrame(matched_rows)
other_df = pd.DataFrame(other_entity_rows)
if not other_df.empty:
    other_df = other_df.drop_duplicates(subset=["Place_ID"])

print(f"{'='*60}")
print(f"Total Talabat names tested   : {stats['total_queries']}")
print(f"Exact entity match found     : {stats['exact_match_found']}  ({stats['exact_match_found']/stats['total_queries']*100:.1f}%)")
print(f"Not Found (needs review)     : {stats['not_found']}  ({stats['not_found']/stats['total_queries']*100:.1f}%)")
print(f"Other UAE entities discovered: {len(other_df)} unique (bonus data, not merged)")
print(f"{'='*60}")

# -- Export matched/main directory -------------------------------------------
def write_excel(df, path, title, sheet_name="Data"):
    wb = xlsxwriter.Workbook(str(path))
    hdr_fmt = wb.add_format({"bold": True, "font_color": "white", "bg_color": "#2C3E8C",
                             "align": "center", "border": 1, "font_size": 10})
    body_fmt = wb.add_format({"font_size": 9, "border": 1, "valign": "vcenter"})
    body_alt = wb.add_format({"font_size": 9, "border": 1, "valign": "vcenter", "bg_color": "#F7F8FA"})
    notfound_fmt = wb.add_format({"font_size": 9, "border": 1, "valign": "vcenter", "bg_color": "#FFF0F0", "font_color": "#C0392B"})
    title_fmt = wb.add_format({"bold": True, "font_size": 14, "font_color": "white", "bg_color": "#1A1A2E", "align": "center"})

    ws = wb.add_worksheet(sheet_name)
    ws.hide_gridlines(2)
    ncols = max(len(df.columns) - 1, 1) if not df.empty else 10
    ws.merge_range(0, 0, 0, ncols, title, title_fmt)
    ws.set_row(0, 26)

    if not df.empty:
        for ci, col in enumerate(df.columns):
            ws.write(2, ci, col, hdr_fmt)
            ws.set_column(ci, ci, 22)
        match_status_col = list(df.columns).index("Match_Status") if "Match_Status" in df.columns else None
        for ri, row in enumerate(df.itertuples(index=False)):
            is_not_found = match_status_col is not None and row[match_status_col] == "Not Found"
            fmt = notfound_fmt if is_not_found else (body_fmt if ri % 2 == 0 else body_alt)
            for ci, val in enumerate(row):
                ws.write(ri + 3, ci, val if pd.notna(val) else "", fmt)
        ws.autofilter(2, 0, 2 + len(df), len(df.columns) - 1)
        ws.freeze_panes(3, 0)
    wb.close()


write_excel(matched_df, OUT_DIR / "UAE_Retest_500_Matched.xlsx",
            "UAE Restaurant Directory — Strict Entity Match (500 names)")
matched_df.to_json(OUT_DIR / "UAE_Retest_500_Matched.json", orient="records", indent=2, force_ascii=False)

write_excel(other_df, OUT_DIR / "UAE_Other_UAE_Entities.xlsx",
            "Other UAE Entities Discovered (NOT merged into Talabat directory)")
other_df.to_json(OUT_DIR / "UAE_Other_UAE_Entities.json", orient="records", indent=2, force_ascii=False)

print(f"\nSaved -> {OUT_DIR / 'UAE_Retest_500_Matched.xlsx'}")
print(f"Saved -> {OUT_DIR / 'UAE_Retest_500_Matched.json'}")
print(f"Saved -> {OUT_DIR / 'UAE_Other_UAE_Entities.xlsx'}")
print(f"Saved -> {OUT_DIR / 'UAE_Other_UAE_Entities.json'}")
