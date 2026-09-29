"""
14_export_remaining_list.py
------------------------------
Exports the current remaining (never-searched) unique restaurant names
as a CSV, with branch_id/restaurant_id/area context for each -- same
computation used by 13_build_main_account_checkpointed_batch.py to
select the next batch, just exported for review instead of building
an actual batch.
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

APIFY_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = APIFY_ROOT / "Complete_list_apify.csv"
DIY_CHECKPOINT_FILE = APIFY_ROOT.parent / "Google_map" / "output" / "checkpoints" / "diy_scraper_checkpoints.json"
OUT_DIR = APIFY_ROOT / "output"

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


with open(CSV_PATH, encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

EXCLUDE_DONE = {"kfc", "domino's pizza"}
grouped = defaultdict(list)
for r in rows:
    cn = clean_name(r["restaurant_name"])
    if cn in EXCLUDE_DONE:
        continue
    grouped[cn].append(r)

tested_apify = set()
for d in sorted((APIFY_ROOT / "brands").glob("uae_*")):
    input_path = d / "input.json"
    if input_path.exists():
        config = json.loads(input_path.read_text(encoding="utf-8"))
        for m in config.get("query_metadata", []):
            tested_apify.add(m.get("restaurant_name_clean"))

tested_diy = set()
if DIY_CHECKPOINT_FILE.exists():
    data = json.loads(DIY_CHECKPOINT_FILE.read_text(encoding="utf-8"))
    for b in data.get("batches", []):
        tested_diy.update(b.get("names", []))

already_covered = tested_apify | tested_diy
remaining_names = sorted(n for n in grouped if n not in already_covered)

rows_out = []
for name in remaining_names:
    branches = grouped[name]
    sample = branches[0]
    rows_out.append({
        "Talabat_Restaurant_Name_Clean": name,
        "Query_String_To_Be_Searched": f"{display_name(name)} UAE Restaurant",
        "Talabat_Branch_Count": len(branches),
        "Sample_Branch_ID": sample["branch_id"],
        "Sample_Restaurant_ID": sample["restaurant_id"],
        "All_Branch_IDs": ", ".join(b["branch_id"] for b in branches),
        "Area_ID": sample.get("area_id"),
        "Area_Name": sample.get("area_name"),
        "Sample_Talabat_Map_URL": sample["map_url"],
    })

import pandas as pd
df = pd.DataFrame(rows_out)
csv_path = OUT_DIR / "Remaining_4447_Unsearched_Names.csv"
json_path = OUT_DIR / "Remaining_4447_Unsearched_Names.json"
df.to_csv(csv_path, index=False, encoding="utf-8-sig")
df.to_json(json_path, orient="records", indent=2, force_ascii=False)

print(f"Total remaining unique names: {len(remaining_names)}")
print(f"Saved -> {csv_path}")
print(f"Saved -> {json_path}")
