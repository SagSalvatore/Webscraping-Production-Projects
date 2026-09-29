"""
10_scan_fused_names.py
--------------------------
Scans the FULL Talabat dataset (all 15,768 raw restaurant_name values,
not just the 500 already tested) for the "fused word" data-quality
pattern found in "The Monkindubai Silicon Oasis,uae" -- where a
location/emirate word got concatenated onto the restaurant name with
no space (Monk + in + Dubai -> "Monkindubai").

Detection heuristic: look for any single whitespace-delimited "word"
that is unusually long (>12 chars) AND ends with a recognizable
location keyword (uae, dubai, sharjah, ajman, fujairah, rak, karama,
deira, etc.) -- a real English/Arabic restaurant word is rarely that
long, but a fused "name+location" string is exactly this shape.
"""
import csv
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

CSV_PATH = Path(__file__).parent.parent / "Complete_list_apify.csv"

LOCATION_SUFFIXES = [
    "uae", "dubai", "sharjah", "ajman", "fujairah", "rak",
    "karama", "deira", "barsha", "marina", "oasis", "qusais",
    "muhaisnah", "mizhar", "warqa", "nahda", "silicon",
    "khawaneej", "mirdif", "jumeirah", "satwa", "garhoud",
]

MIN_WORD_LEN = 12

fused_candidates = []

with open(CSV_PATH, encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

seen_names = set()
for r in rows:
    name = r["restaurant_name"].strip()
    if name.lower() in seen_names:
        continue
    seen_names.add(name.lower())

    words = re.split(r"\s+", name)
    for w in words:
        w_clean = re.sub(r"[^a-zA-Z]", "", w).lower()
        if len(w_clean) >= MIN_WORD_LEN:
            for suffix in LOCATION_SUFFIXES:
                if w_clean.endswith(suffix) and len(w_clean) > len(suffix) + 2:
                    fused_candidates.append((name, w, suffix, r["branch_id"], r["map_url"]))
                    break

print(f"Total unique restaurant names scanned: {len(seen_names)}")
print(f"Suspected fused-name entries found   : {len(fused_candidates)}")
print()
print(f"{'Restaurant Name':<55} {'Fused Word':<30} {'Matched Suffix'}")
print("-" * 100)
for name, word, suffix, branch_id, map_url in fused_candidates[:60]:
    print(f"{name:<55} {word:<30} {suffix}")

if len(fused_candidates) > 60:
    print(f"\n... and {len(fused_candidates) - 60} more")

# Save full list for review
import json
out_path = Path(__file__).parent.parent / "output" / "fused_names_scan.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps([
    {"restaurant_name": n, "fused_word": w, "matched_suffix": s, "branch_id": b, "map_url": u}
    for n, w, s, b, u in fused_candidates
], ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nFull list saved -> {out_path}")
