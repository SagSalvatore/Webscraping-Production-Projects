"""
Enriches LEFT.xlsx with missing fields (name, branch_id, branch_slug, lat, lon).

- 230 rows already in main JSONL → looked up instantly
- 19 rows missing → fetched from Talabat restaurant pages
- Output:
    data/urls/left_enriched.json   — all 249 rows as JSON
    data/urls/left_enriched.csv    — all 249 rows as CSV
    data/urls/talabat_restaurant_urls.csv  — main CSV with 19 new rows appended
    data/urls/talabat_restaurant_urls.jsonl — main JSONL with 19 new rows appended
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
import os

load_dotenv(dotenv_path=ROOT / ".env")

# ── paths ──────────────────────────────────────────────────────────────────
LEFT_XLSX    = ROOT / "LEFT.xlsx"
MAIN_JSONL   = ROOT / "data" / "urls" / "talabat_restaurant_urls.jsonl"
MAIN_CSV     = ROOT / "data" / "urls" / "talabat_restaurant_urls.csv"
OUT_JSON     = ROOT / "data" / "urls" / "left_enriched.json"
OUT_CSV      = ROOT / "data" / "urls" / "left_enriched.csv"

COLS = ["name", "branch_id", "restaurant_id", "branch_slug",
        "area_name", "area_id", "lat", "lon", "url", "page_found", "scraped_at"]


# ── helpers ────────────────────────────────────────────────────────────────

def extract_branch_id(url: str) -> int | None:
    m = re.search(r"/restaurant/(\d+)/", str(url))
    return int(m.group(1)) if m else None


def extract_slug(url: str) -> str | None:
    m = re.search(r"/restaurant/\d+/([^?]+)", str(url))
    return m.group(1) if m else None


def parse_next_data(html: str) -> dict:
    """Extract name, lat, lon from __NEXT_DATA__ on a restaurant detail page."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except Exception:
        return {}

    props = data.get("props", {}).get("pageProps", {})

    # Try common keys for restaurant detail page
    for key in ("restaurantData", "restaurant", "data"):
        r = props.get(key)
        if isinstance(r, dict):
            name = r.get("name") or r.get("nameEn") or ""
            lat  = r.get("latitude")  or r.get("lat")
            lon  = r.get("longitude") or r.get("lng") or r.get("lon")
            if name or lat:
                return {"name": name, "lat": lat, "lon": lon}

    return {}


def fetch_restaurant(url: str, proxy_url: str | None) -> dict:
    """Fetch a Talabat restaurant page and parse __NEXT_DATA__."""
    try:
        from curl_cffi.requests import Session
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        with Session(impersonate="chrome124") as s:
            resp = s.get(
                url.split("?")[0],   # detail page — no ?aid= needed
                proxies=proxies,
                timeout=20,
                headers={
                    "referer": "https://www.talabat.com/uae/restaurants",
                    "accept-language": "en-US,en;q=0.9",
                }
            )
        if resp.status_code == 200:
            return parse_next_data(resp.text)
        else:
            print(f"  HTTP {resp.status_code} for {url}")
            return {}
    except Exception as e:
        print(f"  Error fetching {url}: {e}")
        return {}


# ── main ───────────────────────────────────────────────────────────────────

def main():
    # Proxy setup
    oxy_user    = os.getenv("OXYLABS_USERNAME", "")
    oxy_pass    = os.getenv("OXYLABS_PASSWORD", "")
    oxy_country = os.getenv("OXYLABS_COUNTRY", "ae")
    proxy_url   = None
    if oxy_user and oxy_pass:
        session_id = "leftenrich01"
        proxy_url  = (
            f"http://customer-{oxy_user}-cc-{oxy_country}-sessid-{session_id}"
            f":{oxy_pass}@pr.oxylabs.io:7777"
        )
        print("Proxy: Oxylabs UAE OK")
    else:
        print("Proxy: not configured — running direct")

    # ── load files ──────────────────────────────────────────────────────────
    print("\nLoading LEFT.xlsx …")
    left = pd.read_excel(LEFT_XLSX)
    left["branch_id"]   = left["url"].apply(extract_branch_id)
    left["branch_slug"] = left["url"].apply(extract_slug)
    print(f"  {len(left)} rows loaded")

    print("Loading main JSONL …")
    main_df = pd.read_json(MAIN_JSONL, lines=True)
    print(f"  {len(main_df)} rows loaded")

    # ── split: in main vs not in main ───────────────────────────────────────
    main_bids = set(main_df["branch_id"].dropna().astype(int))
    left_in   = left[left["branch_id"].isin(main_bids)].copy()
    left_out  = left[~left["branch_id"].isin(main_bids)].copy()
    print(f"\nAlready in main JSONL : {len(left_in)}")
    print(f"Need to fetch         : {len(left_out)}")

    # ── enrich the 230 from main JSONL ──────────────────────────────────────
    main_lookup = main_df.set_index("branch_id")[
        ["name", "restaurant_id", "branch_slug", "lat", "lon", "page_found", "scraped_at"]
    ]
    left_in = left_in.join(main_lookup, on="branch_id", rsuffix="_main")

    # keep main values where they exist
    for col in ["name", "branch_slug", "lat", "lon", "page_found", "scraped_at"]:
        if col + "_main" in left_in.columns:
            left_in[col] = left_in[col + "_main"]
            left_in.drop(columns=[col + "_main"], inplace=True)

    if "restaurant_id_main" in left_in.columns:
        left_in["restaurant_id"] = left_in["restaurant_id_main"]
        left_in.drop(columns=["restaurant_id_main"], inplace=True)

    # ── fetch the 19 missing ────────────────────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    fetched_rows = []

    print(f"\nFetching {len(left_out)} restaurant pages …")
    for i, (_, row) in enumerate(left_out.iterrows(), 1):
        url = row["url"]
        print(f"  [{i}/{len(left_out)}] {url.split('?')[0].split('/')[-1]}")
        info = fetch_restaurant(url, proxy_url)

        fetched_rows.append({
            "name":          info.get("name") or "",
            "branch_id":     row["branch_id"],
            "restaurant_id": row.get("restaurant_id"),
            "branch_slug":   row["branch_slug"],
            "area_name":     row["area_name"],
            "area_id":       row["area_id"],
            "lat":           info.get("lat"),
            "lon":           info.get("lon"),
            "url":           url,
            "page_found":    None,
            "scraped_at":    now,
        })
        time.sleep(1.5)   # polite delay

    fetched_df = pd.DataFrame(fetched_rows) if fetched_rows else pd.DataFrame(columns=COLS)

    # ── combine all 249 ─────────────────────────────────────────────────────
    combined = pd.concat([left_in, fetched_df], ignore_index=True)

    # ensure column order
    for c in COLS:
        if c not in combined.columns:
            combined[c] = None
    combined = combined[COLS]

    # ── save left_enriched.json & .csv ──────────────────────────────────────
    combined.to_json(OUT_JSON, orient="records", indent=2, force_ascii=False)
    combined.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\nSaved left_enriched.json: {OUT_JSON}")
    print(f"Saved left_enriched.csv : {OUT_CSV}")

    # ── append 19 new rows to main CSV & JSONL ──────────────────────────────
    if len(fetched_df):
        # Filter only rows with branch_id NOT already in main
        truly_new = fetched_df[~fetched_df["branch_id"].isin(main_bids)]
        if len(truly_new):
            # Append to JSONL
            with open(MAIN_JSONL, "a", encoding="utf-8") as f:
                for _, r in truly_new.iterrows():
                    f.write(json.dumps(r.dropna().to_dict(), ensure_ascii=False) + "\n")

            # Append to CSV
            truly_new.to_csv(MAIN_CSV, mode="a", header=False, index=False, encoding="utf-8-sig")

            print(f"\nAppended {len(truly_new)} new rows to main JSONL + CSV")
        else:
            print("\nAll fetched rows were already in main — nothing appended")

    # ── summary ─────────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"Total in left_enriched : {len(combined)}")
    print(f"  - from main JSONL    : {len(left_in)}")
    print(f"  - freshly fetched    : {len(fetched_df)}")
    missing_name = combined["name"].isna().sum() + (combined["name"] == "").sum()
    missing_geo  = combined["lat"].isna().sum()
    print(f"Missing name           : {missing_name}")
    print(f"Missing lat/lon        : {missing_geo}")
    new_main_total = len(main_df) + len(truly_new if len(fetched_df) else pd.DataFrame())
    print(f"Main JSONL total now   : {len(main_df) + (len(truly_new) if len(fetched_df) else 0)}")


if __name__ == "__main__":
    main()
