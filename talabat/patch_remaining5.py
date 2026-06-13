import json, re, os, pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN_JSONL = ROOT / "data/urls/talabat_restaurant_urls.jsonl"
MAIN_CSV   = ROOT / "data/urls/talabat_restaurant_urls.csv"
OUT_JSON   = ROOT / "data/urls/left_enriched.json"
OUT_CSV    = ROOT / "data/urls/left_enriched.csv"

still_missing = {
    22320:  "https://www.talabat.com/uae/restaurant/22320/dominos-pizza-jumeirah?aid=1280",
    626019: "https://www.talabat.com/uae/restaurant/626019/zaatar-w-zeit-sheikh-zayed-road-jumeirah-1?aid=1280",
    721990: "https://www.talabat.com/uae/restaurant/721990/wakha-restaurant-umm-suqeim-2?aid=1280",
    736873: "https://www.talabat.com/uae/restaurant/736873/french-bakery-difc?aid=1280",
    774223: "https://www.talabat.com/uae/restaurant/774223/b60-burgers-al-rigga?aid=1280",
}

def slug_to_name(url):
    m = re.search(r"/restaurant/\d+/([^?]+)", url)
    if not m:
        return ""
    slug = m.group(1)
    return " ".join(w.capitalize() for w in slug.replace("-", " ").split())

# Patch JSONL
rows = []
patched = 0
with open(MAIN_JSONL, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        bid = rec.get("branch_id")
        if bid in still_missing and not rec.get("name"):
            rec["name"] = slug_to_name(still_missing[bid])
            rec["note"] = "name_derived_from_slug_delisted"
            patched += 1
        rows.append(rec)

tmp = str(MAIN_JSONL) + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    for rec in rows:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
os.replace(tmp, MAIN_JSONL)
print(f"Patched {patched} rows in JSONL with slug-derived names")

# Regenerate main CSV
COLS = ["name","branch_id","restaurant_id","branch_slug","area_name","area_id","lat","lon","url","page_found","scraped_at"]
df = pd.read_json(MAIN_JSONL, lines=True)
df = df[[c for c in COLS if c in df.columns]]
df.to_csv(MAIN_CSV, index=False, encoding="utf-8-sig")
print(f"Main CSV regenerated: {len(df)} rows")

# Update left_enriched
enriched = pd.read_json(OUT_JSON)
for bid, url in still_missing.items():
    mask = enriched["branch_id"] == bid
    if mask.any():
        enriched.loc[mask, "name"] = slug_to_name(url)
enriched.to_json(OUT_JSON, orient="records", indent=2, force_ascii=False)
enriched.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
print(f"left_enriched updated: {len(enriched)} rows")

# Final quality report
print()
print("=== FINAL QUALITY CHECK ===")
df2 = pd.read_json(MAIN_JSONL, lines=True)
missing_name = df2["name"].isna().sum() + (df2["name"] == "").sum()
missing_geo  = df2["lat"].isna().sum()
print(f"Main JSONL total rows : {len(df2)}")
print(f"Missing name          : {missing_name}")
print(f"Missing lat/lon       : {missing_geo} (5 delisted restaurants, expected)")

e2 = pd.read_json(OUT_JSON)
e_missing_name = (e2["name"].isna() | (e2["name"] == "")).sum()
e_missing_geo  = e2["lat"].isna().sum()
print(f"left_enriched rows    : {len(e2)}")
print(f"  missing name        : {e_missing_name}")
print(f"  missing lat/lon     : {e_missing_geo}")

# Show the 5 delisted entries
print()
print("The 5 delisted entries (name from slug, no lat/lon):")
delisted = df2[df2["branch_id"].isin(still_missing.keys())][["name","branch_id","url"]]
print(delisted.to_string(index=False))
