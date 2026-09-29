"""
1_prepare_search_terms.py
--------------------------
Phase 1 + 1.5 + 2 combined: cleaning, known-chain classification,
emirate derivation, and deduplication.

Reads:  apify/Complete_list_apify.csv (15,768 Talabat branches)
        apify/brands/known_global_chains.json (46-name allowlist)

Excludes: KFC ("kfc") and Domino's ("domino's pizza") — already done.

Produces: apify/output/prepared/search_terms.json
  One row per unique restaurant name (10,281 total) with:
    - restaurant_name_clean
    - query_string        ("{Name}, {Emirate}, UAE")
    - tier                 known_chain | default
    - emirate               derived from lat/lon bounding boxes
    - emirate_zone_check    whether area_id-derived region agrees
    - talabat_branch_count
    - sample_branch_id / sample_map_url  (for traceability)
"""

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

APIFY_DIR = Path(__file__).parent.parent
SRC_CSV   = APIFY_DIR / "Complete_list_apify.csv"
ALLOWLIST = APIFY_DIR / "brands" / "known_global_chains.json"
OUT_DIR   = APIFY_DIR / "output" / "prepared"
OUT_DIR.mkdir(parents=True, exist_ok=True)

EXCLUDE_DONE = {"kfc", "domino's pizza"}

# BOM / encoding artifact cleanup for area_name (Dubai World Trade Center variants)
_BOM_CHARS = re.compile(r"[﻿​‎‏]")

# Rare (0.18%, 19/10,283) Talabat export artifact: "in" gets fused with no
# spaces between the name and area, e.g. "Spice HutinMirdif,UAE" or
# "The MonkinMeadows,UAE" -- always ends in "in{CapitalizedArea},UAE".
# Strip the fused area+UAE suffix so the real name ("Spice Hut", "The Monk")
# is recovered before matching/search. Confirmed via output/fused_names_scan.json.
_FUSED_AREA_SUFFIX = re.compile(r"^(.*?)in([A-Z][a-zA-Z0-9\s\-]*?)\s*,\s*UAE$")


def fix_fused_name(raw: str) -> str:
    m = _FUSED_AREA_SUFFIX.match(raw.strip())
    return m.group(1).strip() if m else raw


def clean_name(raw: str) -> str:
    raw = fix_fused_name(raw)
    n = raw.strip().lower()
    n = _BOM_CHARS.sub("", n)
    n = re.sub(r"\s+", " ", n)
    n = re.sub(r"[™®©]", "", n)
    n = n.replace(" & ", " and ")
    return n.strip()


def emirate_from_latlon(lat: float, lon: float) -> str:
    """Bounding-box reverse geocode. Coarse — used only to build a useful
    search-query string (e.g. 'Name, Dubai, UAE'). The AUTHORITATIVE
    Emirate in the final output comes from Google's own returned address
    post-search, same as the existing process_brand.py pattern."""
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


def display_name(clean: str) -> str:
    """Title-case for use inside the actual Google search query string."""
    return " ".join(w.capitalize() for w in clean.split())


def main():
    with open(SRC_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    with open(ALLOWLIST, encoding="utf-8") as f:
        allow = json.load(f)
    known_chains = set(n.lower() for n in allow["known_global_chains"])

    print(f"Loaded {len(rows)} Talabat branches")

    grouped = defaultdict(list)
    for r in rows:
        name_clean = clean_name(r["restaurant_name"])
        if name_clean in EXCLUDE_DONE:
            continue
        grouped[name_clean].append(r)

    print(f"Unique names after exclusions: {len(grouped)}")

    prepared = []
    emirate_mismatch = 0

    for name_clean, branches in grouped.items():
        # Majority-vote emirate across this name's branches (usually 1 branch anyway)
        emirates = [
            emirate_from_latlon(float(b["ld_lat"]), float(b["ld_lon"]))
            for b in branches
            if float(b["ld_lat"]) != 0.0
        ]
        emirate = max(set(emirates), key=emirates.count) if emirates else "Dubai"

        tier = "known_chain" if name_clean in known_chains else "default"

        sample = branches[0]
        disp = display_name(name_clean)
        # Search term = bare name + "UAE Restaurant" suffix. User found that
        # a bare name (e.g. "Wanderlust") often returns 0 Google results —
        # too generic/short for Google's own ranking to disambiguate as a
        # restaurant. Appending "UAE Restaurant" (verified manually: "wyld
        # uae restaurants" and "wanderlust uae restaurant" both surfaced the
        # correct listing on Google) biases matching toward food places
        # without breaking searchMatching="only_includes", since we check
        # the RETURNED TITLE against just the base name in the merge step's
        # relevance filter, not against this full query string.
        query_string = f"{disp} UAE Restaurant"

        prepared.append({
            "restaurant_name_clean": name_clean,
            "query_string": query_string,
            "tier": tier,
            "emirate": emirate,
            "talabat_branch_count": len(branches),
            "sample_branch_id": sample["branch_id"],
            "sample_map_url": sample["map_url"],
        })

    known_count   = sum(1 for p in prepared if p["tier"] == "known_chain")
    default_count = sum(1 for p in prepared if p["tier"] == "default")

    print(f"\nTier breakdown:")
    print(f"  known_chain : {known_count}")
    print(f"  default     : {default_count}")
    print(f"  total       : {len(prepared)}")

    out_path = OUT_DIR / "search_terms.json"
    out_path.write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved -> {out_path}")

    # Emirate distribution sanity check
    from collections import Counter
    dist = Counter(p["emirate"] for p in prepared)
    print("\nEmirate distribution:")
    for em, cnt in dist.most_common():
        print(f"  {em:<15} {cnt}")


if __name__ == "__main__":
    main()
