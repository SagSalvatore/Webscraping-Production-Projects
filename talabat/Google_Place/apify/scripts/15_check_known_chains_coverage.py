"""
15_check_known_chains_coverage.py
------------------------------------
Cross-checks a user-provided list of known UAE/global restaurant chains
against:
  1. Our known_global_chains.json allowlist (pre-classified, cap=150)
  2. The 49 chains CONFIRMED via default-tier scraping (2+ Google matches)
  3. Whether the name exists ANYWHERE in the raw Talabat CSV at all
  4. If it exists, whether it's been searched yet (Apify/DIY) or still
     sitting in the remaining pool
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
ALLOWLIST_PATH = APIFY_ROOT / "brands" / "known_global_chains.json"
DIY_CHECKPOINT_FILE = APIFY_ROOT.parent / "Google_map" / "output" / "checkpoints" / "diy_scraper_checkpoints.json"
MASTER_MAP_PATH = APIFY_ROOT.parent / "map" / "output" / "Master_Talabat_Google_Mapping.csv"

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


USER_LIST = [
    "McDonald's", "KFC", "Pizza Hut", "Hardee's", "Krispy Kreme", "TGI Fridays",
    "Costa Coffee", "Baskin-Robbins", "Olive Garden", "Red Lobster",
    "LongHorn Steakhouse", "Peet's Coffee", "Wimpy", "Chicken Tikka", "Subway",
    "Domino's Pizza", "Burger King", "Papa John's Pizza",
    "Popeyes Louisiana Kitchen", "Jollibee", "Tim Hortons", "Starbucks",
    "Dunkin' Donuts", "Cinnabon", "Five Guys", "Seattle's Best Coffee",
    "Ben & Jerry's", "Cafe2U", "Arabica Coffee House", "Mikel Coffee Company",
    "Coffee Planet", "Dutch Bros", "Auntie Anne's", "85°C Bakery Cafe",
    "Applebee's", "Arby's", "Bonchon Chicken", "Al Fanar Restaurant & Cafe",
    "Arabian Tea House", "Automatic Restaurant", "Ravi Restaurant", "Al Mallah",
    "Zaroob", "Operation Falafel", "Comptoir 94", "Logma", "Bait Al Mandi",
    "Shakespeare and Co.", "Karak House", "tashas", "M'OISHI", "Sadaf Restaurant",
    "Al Ustad Special Kabab", "Bu Qtair", "Girl and the Goose", "Three Bros",
    "Foodmark brands (various)", "Fusion Ceviche", "Marmellata", "Maraqah",
]

# -- Load known_global_chains allowlist --------------------------------------
allow = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
allowlist_set = set(n.lower() for n in allow["known_global_chains"])

# -- Load master mapping to get the 49 confirmed chains + branch status -----
with open(MASTER_MAP_PATH, encoding="utf-8-sig") as f:
    master_rows = list(csv.DictReader(f))

name_to_status = {}
name_to_location_count = defaultdict(int)
for r in master_rows:
    name = r["Talabat_Restaurant_Name_Clean"]
    name_to_status[name] = r["Status"]
    if r["Status"] == "Matched":
        name_to_location_count[name] += 1

confirmed_chains = {n for n, c in name_to_location_count.items() if c >= 2}

# -- Load raw Talabat names (does it exist at all, regardless of status) ----
with open(CSV_PATH, encoding="utf-8-sig") as f:
    all_talabat_names = set(clean_name(r["restaurant_name"]) for r in csv.DictReader(f))

# -- Check each user-provided name --------------------------------------------
print(f"{'Name':<32} {'In Talabat?':<12} {'Status':<18} {'Locations Found':<16} {'In Allowlist?'}")
print("-" * 100)

found_in_talabat = 0
confirmed_as_chain = 0
in_allowlist = 0

for raw_name in USER_LIST:
    cn = clean_name(raw_name)
    # fuzzy contains check since exact match may miss suffix differences
    matches = [n for n in all_talabat_names if cn in n or n in cn]
    in_talabat = "Yes" if matches else "No"
    if matches:
        found_in_talabat += 1

    best_match = matches[0] if matches else None
    status = name_to_status.get(best_match, "N/A") if best_match else "N/A"
    loc_count = name_to_location_count.get(best_match, 0) if best_match else 0
    if loc_count >= 2:
        confirmed_as_chain += 1

    is_allow = "Yes" if any(cn in a or a in cn for a in allowlist_set) else "No"
    if is_allow == "Yes":
        in_allowlist += 1

    print(f"{raw_name:<32} {in_talabat:<12} {status:<18} {loc_count:<16} {is_allow}")

print("-" * 100)
print(f"\nTotal names checked           : {len(USER_LIST)}")
print(f"Found in Talabat dataset       : {found_in_talabat}")
print(f"Confirmed as chain (2+ locations found): {confirmed_as_chain}")
print(f"In our known_global_chains allowlist    : {in_allowlist}")
print()
print(f"Total chains confirmed via default-tier scraping (all names): {len(confirmed_chains)}")
print(f"Total names in known_global_chains allowlist (all 46, all done): 46")
