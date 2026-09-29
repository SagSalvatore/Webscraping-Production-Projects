"""
13_build_main_account_checkpointed_batch.py
------------------------------------------------
Builds the next batch from the 8,200 untested names, for the MAIN
Apify account (BRONZE tier, now on pay-as-you-go with a $100 cap).

Cautious rollout per user request: only 500 names this time (not the
full 8,200), with a checkpoint file recording exactly which names were
included so future runs can resume cleanly from where this one ends,
with zero ambiguity about what's been covered.

Checkpoint file: output/checkpoints/main_account_checkpoints.json
  Each entry: batch_number, names_count, start_offset, end_offset,
  branch_ids covered, timestamp, status (pending/completed), cost.
"""
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

APIFY_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = APIFY_ROOT / "Complete_list_apify.csv"
CHECKPOINT_DIR = APIFY_ROOT / "output" / "checkpoints"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
CHECKPOINT_FILE = CHECKPOINT_DIR / "main_account_checkpoints.json"

BATCH_SIZE = 500
N_GROUPS = 30
DIY_CHECKPOINT_FILE = Path(__file__).parent.parent.parent / "Google_map" / "output" / "checkpoints" / "diy_scraper_checkpoints.json"

STOPWORDS_UNUSED = None  # (kept for parity; matching logic lives in merge scripts)
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


def emirate_from_latlon(lat, lon):
    if lat == 0.0 or lon == 0.0:
        return "Unknown"
    if lat < 24.62:
        return "Abu Dhabi"
    if lat > 25.70:
        return "Ras Al Khaimah"
    if lat > 25.50:
        return "Umm Al Quwain" if lon < 55.65 else "Fujairah"
    if lat > 25.40:
        return "Ajman"
    if lat > 25.30 and lon > 55.40:
        return "Sharjah"
    return "Dubai"


def load_all_tested_names():
    """Same logic as script 12: everything ever searched across all batches."""
    tested = set()
    brand_dirs = sorted((APIFY_ROOT / "brands").glob("uae_*"))
    for d in brand_dirs:
        input_path = d / "input.json"
        if not input_path.exists():
            continue
        config = json.loads(input_path.read_text(encoding="utf-8"))
        for m in config.get("query_metadata", []):
            tested.add(m.get("restaurant_name_clean"))
    return tested


def load_checkpoints():
    if CHECKPOINT_FILE.exists():
        return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
    return {"batches": []}


def load_diy_tested_names():
    """Names already covered by the DIY Playwright scraper (Google_map/) --
    must be excluded here too so the two approaches never overlap/double-spend."""
    tested = set()
    if DIY_CHECKPOINT_FILE.exists():
        data = json.loads(DIY_CHECKPOINT_FILE.read_text(encoding="utf-8"))
        for b in data.get("batches", []):
            tested.update(b.get("names", []))
    return tested


def save_checkpoints(data):
    CHECKPOINT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    EXCLUDE_DONE = {"kfc", "domino's pizza"}

    from collections import defaultdict
    grouped = defaultdict(list)
    for r in rows:
        cn = clean_name(r["restaurant_name"])
        if cn in EXCLUDE_DONE:
            continue
        grouped[cn].append(r)

    checkpoints = load_checkpoints()
    already_batched_names = set()
    for b in checkpoints["batches"]:
        already_batched_names.update(b["names"])

    tested_via_apify = load_all_tested_names()
    tested_via_diy = load_diy_tested_names()
    already_covered = tested_via_apify | already_batched_names | tested_via_diy

    # Deterministic order: sort by name so re-running this script is stable
    remaining_names = sorted(n for n in grouped if n not in already_covered)
    print(f"Total unique names (excl. KFC/Domino's): {len(grouped)}")
    print(f"Already covered (Apify: {len(tested_via_apify)}, DIY: {len(tested_via_diy)}, checkpointed: {len(already_batched_names)}): {len(already_covered)}")
    print(f"Remaining pool                          : {len(remaining_names)}")

    batch_names = remaining_names[:BATCH_SIZE]
    print(f"\nSelected batch: {len(batch_names)} names (batch #{len(checkpoints['batches']) + 1})")

    # Build query terms
    prepared = []
    for name_clean in batch_names:
        branches = grouped[name_clean]
        emirates = [
            emirate_from_latlon(float(b["ld_lat"]), float(b["ld_lon"]))
            for b in branches if float(b["ld_lat"]) != 0.0
        ]
        emirate = max(set(emirates), key=emirates.count) if emirates else "Dubai"
        sample = branches[0]
        disp = display_name(name_clean)
        query_string = f"{disp} UAE Restaurant"
        prepared.append({
            "restaurant_name_clean": name_clean,
            "query_string": query_string,
            "tier": "default",
            "emirate": emirate,
            "talabat_branch_count": len(branches),
            "sample_branch_id": sample["branch_id"],
            "sample_map_url": sample["map_url"],
        })

    # Split into N_GROUPS for the main account run
    def chunk(lst, n):
        k, m = divmod(len(lst), n)
        return [lst[i*k+min(i,m):(i+1)*k+min(i+1,m)] for i in range(n)]

    groups_terms = chunk(prepared, N_GROUPS)
    groups = {f"group_{i+1}": [t["query_string"] for t in g] for i, g in enumerate(groups_terms)}

    batch_num = len(checkpoints["batches"]) + 1
    brand_name = f"uae_main_batch_{batch_num:02d}"
    brand_dir = APIFY_ROOT / "brands" / brand_name
    brand_dir.mkdir(parents=True, exist_ok=True)

    input_json = {
        "brand_name": brand_name,
        "brand_display": f"UAE Main Account Batch 1 (500 names, cautious rollout)",
        "output_filename": brand_name.upper(),
        "maxCrawledPlacesPerSearch": 10,
        "locationQuery": "United Arab Emirates",
        "searchMatching": "all",
        "language": "en",
        "countryCode": "ae",
        "maxReviews": 0,
        "maxImages": 0,
        "includeOpeningHours": False,
        "scrapeDirectories": False,
        "additionalInfo": False,
        "proxyConfig": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        "query_metadata": [
            {
                "query": t["query_string"],
                "restaurant_name_clean": t["restaurant_name_clean"],
                "tier": t["tier"],
                "emirate_hint": t["emirate"],
                "talabat_branch_count": t["talabat_branch_count"],
                "sample_branch_id": t["sample_branch_id"],
                "sample_map_url": t["sample_map_url"],
            }
            for t in prepared
        ],
        "groups": groups,
    }

    input_path = brand_dir / "input.json"
    input_path.write_text(json.dumps(input_json, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved -> {input_path}  ({len(prepared)} queries, {N_GROUPS} groups)")

    # Record checkpoint as "pending"
    checkpoint_entry = {
        "batch_number": len(checkpoints["batches"]) + 1,
        "brand_folder": brand_name,
        "account": "main (BRONZE)",
        "names_count": len(batch_names),
        "names": batch_names,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "cost_usd": None,
        "completed_at": None,
    }
    checkpoints["batches"].append(checkpoint_entry)
    save_checkpoints(checkpoints)
    print(f"Checkpoint recorded -> {CHECKPOINT_FILE}  (batch #{checkpoint_entry['batch_number']}, status=pending)")

    print(f"\nRemaining after this batch: {len(remaining_names) - len(batch_names)}")
    print(f"\nNext step:")
    print(f"  cd .. && python run_brand_scraper.py --brand {brand_name} --cost-per-place 0.004 --workers {N_GROUPS}")


if __name__ == "__main__":
    main()
