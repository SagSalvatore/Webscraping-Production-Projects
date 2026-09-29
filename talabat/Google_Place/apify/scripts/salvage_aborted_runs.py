"""
salvage_aborted_runs.py
-------------------------
Downloads the partial datasets from the 7 aborted validation runs
(killed mid-run when the account hit its $29 monthly Apify cap) and
saves them in the same location/format run_brand_scraper.py would have,
so 3_merge_uae_results.py can process them normally.
"""
import json
import sys
from pathlib import Path
from datetime import datetime

import requests
import os
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(Path(r"C:\Users\SagarSingh\Downloads\Google_Place\.env"), override=True)
TOKEN = os.getenv("APIFY_API_TOKEN")

APIFY_ROOT = Path(__file__).resolve().parent.parent

RUNS = [
    ("uae_validation_default", "group_1", "m3DwDH6tllgGX5OFg"),
    ("uae_validation_default", "group_2", "fIF3pjLyFJy9PEFq7"),
    ("uae_validation_default", "group_3", "6ZHbenwAE7PDU4d4C"),
    ("uae_validation_default", "group_4", "tFdF60FVpsBDmFW5K"),
    ("uae_validation_default", "group_5", "X0pFwHiFQNA7rB8z7"),
    ("uae_validation_default", "group_6", "5ragcysZ4cdgv4T9U"),
    ("uae_validation_chains",  "known_chains", "pNkDtUoYZa8i47cMJ"),
]

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
total_items = 0

for brand, group, run_id in RUNS:
    run_resp = requests.get(f"https://api.apify.com/v2/actor-runs/{run_id}", params={"token": TOKEN}, timeout=30)
    run = run_resp.json()["data"]
    dataset_id = run.get("defaultDatasetId")

    items = []
    offset = 0
    limit = 1000
    while True:
        r = requests.get(
            f"https://api.apify.com/v2/datasets/{dataset_id}/items",
            params={"token": TOKEN, "format": "json", "limit": limit, "offset": offset},
            timeout=60,
        )
        batch = r.json()
        if not batch:
            break
        items.extend(batch)
        if len(batch) < limit:
            break
        offset += limit

    raw_dir = APIFY_ROOT / "brands" / brand / "output" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / f"dataset_{ts}_{group}_{run_id[:8]}.json"
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[{brand:<24}] {group:<15} run={run_id[:10]}  {len(items):>5} items -> {out_path.name}")
    total_items += len(items)

print(f"\nTotal salvaged items: {total_items}")
