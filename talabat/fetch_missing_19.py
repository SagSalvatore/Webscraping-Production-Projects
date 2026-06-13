"""
Finds the 19 branches that were appended with blank name/lat/lon.
Scans DWTC listing pages to get their actual data and patches the JSONL + CSV.
"""
import json, re, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import pandas as pd
from url_collector.parser import parse_listing_page

MAIN_JSONL = ROOT / "data" / "urls" / "talabat_restaurant_urls.jsonl"
MAIN_CSV   = ROOT / "data" / "urls" / "talabat_restaurant_urls.csv"
OUT_JSON   = ROOT / "data" / "urls" / "left_enriched.json"
OUT_CSV    = ROOT / "data" / "urls" / "left_enriched.csv"
AREA_URL   = "https://www.talabat.com/uae/restaurants/1280/dubai-world-trade-center-dwtc"
MAX_PAGES  = 467

# Find branch_ids that are in JSONL but have no name or no lat
main_df = pd.read_json(MAIN_JSONL, lines=True)
missing_mask = main_df["name"].isna() | (main_df["name"] == "") | main_df["lat"].isna()
missing_df = main_df[missing_mask]
targets = set(missing_df["branch_id"].dropna().astype(int))
print(f"Rows with missing name/lat in JSONL: {len(missing_df)}")
print(f"Hunting branch_ids: {sorted(targets)}")

if not targets:
    print("Nothing to fetch — all rows are complete!")
    sys.exit(0)

# proxy
oxy_user = os.getenv("OXYLABS_USERNAME", "")
oxy_pass = os.getenv("OXYLABS_PASSWORD", "")
proxy_url = None
if oxy_user and oxy_pass:
    proxy_url = f"http://customer-{oxy_user}-cc-ae-sessid-miss02:{oxy_pass}@pr.oxylabs.io:7777"
    print("Proxy: Oxylabs UAE")

from curl_cffi.requests import Session

found = {}

def fetch_page(session, page_num):
    url = f"{AREA_URL}?page={page_num}"
    try:
        r = session.get(
            url,
            proxies={"http": proxy_url, "https": proxy_url} if proxy_url else None,
            timeout=20,
            headers={"referer": "https://www.talabat.com/uae/restaurants",
                     "accept-language": "en-US,en;q=0.9"}
        )
        if r.status_code == 200:
            return r.text
        print(f"  page {page_num}: HTTP {r.status_code}")
        return None
    except Exception as e:
        print(f"  page {page_num}: {e}")
        return None

with Session(impersonate="chrome124") as s:
    for page in range(1, MAX_PAGES + 1):
        if not targets:
            print("All found — stopping early!")
            break

        html = fetch_page(s, page)
        if not html:
            time.sleep(2)
            continue

        result = parse_listing_page(html, page_num=page)
        if not result.vendors:
            print(f"  page {page}: empty — end of listing")
            break

        for v in result.vendors:
            if v.branch_id in targets:
                found[v.branch_id] = v
                targets.discard(v.branch_id)
                print(f"  [page {page}] FOUND {v.branch_id}: {v.name!r} lat={v.lat} lon={v.lon}")

        if page % 10 == 0:
            print(f"  page {page}/{MAX_PAGES} | found: {len(found)} | still need: {len(targets)}")

        time.sleep(0.8)

print(f"\nTotal found: {len(found)}")
if targets:
    print(f"Still missing after full scan: {sorted(targets)}")

if not found:
    sys.exit(0)

# ── Patch JSONL: rewrite the file updating the found rows ─────────────────
print("Patching JSONL ...")
rows = []
patched = 0
with open(MAIN_JSONL, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        bid = rec.get("branch_id")
        if bid in found:
            v = found[bid]
            rec["name"]          = v.name
            rec["lat"]           = v.lat
            rec["lon"]           = v.lon
            rec["branch_slug"]   = v.branch_slug
            rec["restaurant_id"] = v.restaurant_id
            patched += 1
        rows.append(rec)

# Write back atomically
tmp = str(MAIN_JSONL) + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    for rec in rows:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
import os as _os
_os.replace(tmp, MAIN_JSONL)
print(f"Patched {patched} rows in JSONL | total rows: {len(rows)}")

# ── Regenerate main CSV from patched JSONL ────────────────────────────────
print("Regenerating main CSV ...")
COLS = ["name","branch_id","restaurant_id","branch_slug","area_name","area_id",
        "lat","lon","url","page_found","scraped_at"]
df = pd.read_json(MAIN_JSONL, lines=True)
df = df[[c for c in COLS if c in df.columns]]
df.to_csv(MAIN_CSV, index=False, encoding="utf-8-sig")
print(f"Main CSV saved: {len(df)} rows")

# ── Update left_enriched files ────────────────────────────────────────────
if OUT_JSON.exists():
    print("Updating left_enriched files ...")
    enriched = pd.read_json(OUT_JSON)
    for bid, v in found.items():
        mask = enriched["branch_id"] == bid
        enriched.loc[mask, "name"]          = v.name
        enriched.loc[mask, "lat"]           = v.lat
        enriched.loc[mask, "lon"]           = v.lon
        enriched.loc[mask, "branch_slug"]   = v.branch_slug
        enriched.loc[mask, "restaurant_id"] = v.restaurant_id
    enriched.to_json(OUT_JSON, orient="records", indent=2, force_ascii=False)
    enriched.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    still_missing = (enriched["name"].isna() | (enriched["name"] == "")).sum()
    print(f"left_enriched updated | still missing name: {still_missing}")

print("\nDone!")
