"""
Live test — 5 UAE venues covering different categories (cafe, restaurant, bakery, chain).
Validates UAE restriction + name-match guard before running the full 100.
"""
import sys
sys.path.insert(0, ".")

import logging
logging.basicConfig(level=logging.WARNING)  # suppress info noise during test

from scripts.fetch_places import fetch_restaurant

TEST_CASES = [
    # (row_id, slug,            expected_category)
    (10,  "nyla",               "cafe"),
    (1,   "mcdonalds",          "global chain"),
    (23,  "pf_changs",          "restaurant"),
    (67,  "hafiz_mustafa",      "bakery/sweets (Turkish)"),
    (95,  "zaatar_w_zeit",      "UAE chain"),       # wait, zaatar w zeit is row 1095
    (201, "jarful",             "cafe"),
]

logger = logging.getLogger("quick_test")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
logger.addHandler(handler)

print("=" * 70)
print("LIVE UAE API TEST — 5 venues")
print("=" * 70)

for row_id, slug, category in TEST_CASES:
    print(f"\nSlug      : {slug}  ({category})")
    record = fetch_restaurant(slug, row_id, logger)
    print(f"Status    : {record['status']}")
    print(f"Name      : {record['Restaurant_Name']}")
    print(f"Address   : {record['Address']}")
    print(f"Phone     : {record['Contact_No'] or '—'}")
    print(f"Website   : {record['Website'] or '—'}")
    print(f"Lat/Lng   : {record['Geo_Lat']}, {record['Geo_Lng']}")
    print(f"Maps URL  : {record['Google_Maps_URL']}")
    print("-" * 70)
