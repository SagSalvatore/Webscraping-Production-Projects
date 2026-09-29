"""
1_select_next_100.py
-----------------------
Selects the next 100 untested Talabat restaurant names for the DIY
Playwright scraper, continuing from wherever the Apify pipeline left
off (shares the same exclusion set, so no name gets searched twice
across either approach). Includes branch_id + area_id for mapping
back to the original Talabat file, per user request.
"""
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

GOOGLE_MAP_DIR = Path(__file__).parent
APIFY_DIR = Path(__file__).parent.parent / "apify"
CSV_PATH = APIFY_DIR / "Complete_list_apify.csv"
CHECKPOINT_DIR = GOOGLE_MAP_DIR / "output" / "checkpoints"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
DIY_CHECKPOINT_FILE = CHECKPOINT_DIR / "diy_scraper_checkpoints.json"
APIFY_CHECKPOINT_FILE = APIFY_DIR / "output" / "checkpoints" / "main_account_checkpoints.json"

BATCH_SIZE = 100

_FUSED_AREA_SUFFIX = re.compile(r"^(.*?)in([A-Z][a-zA-Z0-9\s\-]*?)\s*,\s*UAE$")
_BOM_CHARS = re.compile(r"[﻿​‎‏]")


def fix_fused_name(raw):
    m = _FUSED_AREA_SUFFIX.match(raw.strip())
    return m.group(1).strip() if m else raw


def clean_name(raw):
    raw = fix_fused_name(raw)
    n = raw.strip().lower()
    n = _BOM_CHARS.sub("", n)
    n = re.sub(r"\s+", " ", n)
    n = re.sub(r"[™®©]", "", n)
    n = n.replace(" & ", " and ")
    return n.strip()


def display_name(clean):
    return " ".join(w.capitalize() for w in clean.split())


def load_all_tested_names_from_apify():
    tested = set()
    brand_dirs = sorted((APIFY_DIR / "brands").glob("uae_*"))
    for d in brand_dirs:
        input_path = d / "input.json"
        if not input_path.exists():
            continue
        config = json.loads(input_path.read_text(encoding="utf-8"))
        for m in config.get("query_metadata", []):
            tested.add(m.get("restaurant_name_clean"))
    return tested


def load_diy_tested_names():
    tested = set()
    if DIY_CHECKPOINT_FILE.exists():
        data = json.loads(DIY_CHECKPOINT_FILE.read_text(encoding="utf-8"))
        for b in data.get("batches", []):
            tested.update(b.get("names", []))
    return tested


def main():
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    EXCLUDE_DONE = {"kfc", "domino's pizza"}
    grouped = defaultdict(list)
    for r in rows:
        cn = clean_name(r["restaurant_name"])
        if cn in EXCLUDE_DONE:
            continue
        grouped[cn].append(r)

    tested_apify = load_all_tested_names_from_apify()
    tested_diy = load_diy_tested_names()
    already_covered = tested_apify | tested_diy

    remaining = sorted(n for n in grouped if n not in already_covered)
    print(f"Total unique names (excl. KFC/Domino's): {len(grouped)}")
    print(f"Already covered (Apify: {len(tested_apify)}, DIY: {len(tested_diy)}): {len(already_covered)}")
    print(f"Remaining pool: {len(remaining)}")

    batch_names = remaining[:BATCH_SIZE]
    print(f"Selected batch: {len(batch_names)} names")

    prepared = []
    for name_clean in batch_names:
        branches = grouped[name_clean]
        sample = branches[0]
        disp = display_name(name_clean)
        prepared.append({
            "restaurant_name_clean": name_clean,
            "query_string": f"{disp} UAE Restaurant",
            "branch_id": sample["branch_id"],
            "restaurant_id": sample["restaurant_id"],
            "area_id": sample.get("area_id"),
            "area_name": sample.get("area_name"),
            "talabat_branch_count": len(branches),
            "sample_map_url": sample["map_url"],
            "all_branch_ids": [b["branch_id"] for b in branches],
        })

    out_path = GOOGLE_MAP_DIR / "output" / "batch_input"
    out_path.mkdir(parents=True, exist_ok=True)
    batch_file = out_path / "diy_batch_01_input.json"
    batch_file.write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved -> {batch_file}")

    # Record pending checkpoint
    checkpoints = {"batches": []}
    if DIY_CHECKPOINT_FILE.exists():
        checkpoints = json.loads(DIY_CHECKPOINT_FILE.read_text(encoding="utf-8"))
    checkpoints["batches"].append({
        "batch_number": len(checkpoints["batches"]) + 1,
        "method": "diy_playwright",
        "names_count": len(batch_names),
        "names": batch_names,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "completed_at": None,
    })
    DIY_CHECKPOINT_FILE.write_text(json.dumps(checkpoints, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Checkpoint recorded -> {DIY_CHECKPOINT_FILE} (batch #{checkpoints['batches'][-1]['batch_number']}, pending)")
    print(f"\nRemaining after this batch: {len(remaining) - len(batch_names)}")


if __name__ == "__main__":
    main()
