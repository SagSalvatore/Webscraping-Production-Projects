"""Shared configuration for the area classification pipeline.

INPUT   ri-db.restaurants_full.json   24,689 records from Tech
OUTPUT  same file, same schema, location.area cleaned in place

ONLY `location.area` IS WRITTEN. _id, mordor_restaurant_id, source_id, name,
chain_id, location.country, location.city and location.sublocality are passed
through byte-identical, and the validation gate asserts that.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # talabat/
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

INPUT = HERE / "ri-db.restaurants_full.json"
OUTPUT = HERE / "ri-db.restaurants_full.cleaned.json"

# ---- July's cleaned deliverable + its canonical vocabulary ---------------
# A local old/ copy is used when present; otherwise the original in
# export/IT_Team_Location/output/. The two were verified byte-identical
# (17,164 records, 716 areas), so either resolves to the same data - this just
# means the pipeline does not break if the local copy is moved away.
def _first_existing(*paths):
    for p in paths:
        if p.exists():
            return p
    return paths[-1]                    # let the caller raise a clear error


_IT_OUT = ROOT / "export" / "IT_Team_Location" / "output"
JULY_CLEAN = _first_existing(
    HERE / "old" / "ri-db.restaurants_id.final_2.json",
    _IT_OUT / "ri-db.restaurants_id.final.json",
)
AREAS_LIST = _first_existing(          # 716 areas + city
    HERE / "old" / "areas_list.json",
    _IT_OUT / "areas_list.json",
)

# ---- July's mapping tables and caches ------------------------------------
IT = ROOT / "export" / "IT_Team_Location"
IT_CACHE = IT / "cache"
IT_OUT = IT / "output"

MAP_TABLES = [                      # from -> to
    "al_prefix_map", "area_canonicalization_map", "canonicalization_map",
    "final_normalization_map", "geo_resolution_map", "khalidiyah_merge_map",
    "non_area_fix_map", "spelling_normalization_map", "websearch_resolution_map",
]
PROVENANCE = IT_OUT / "manual_stage_provenance.csv"     # area_before -> area_after
COORD_CORRECTIONS = IT_OUT / "coordinate_correction_map.csv"

# Talabat's OWN area label per restaurant, read from each restaurant page.
# Outranks reverse geocoding: it is Talabat's vocabulary, which is what the rest
# of the dataset uses. NOT to be confused with the crawl-time area_name, which
# is the ?aid= delivery zone we searched FROM and was wrong on ~86% of rows.
AREANAME_CACHE = ROOT / "listing_comparison" / "output" / "areaname_cache.json"

# Coordinates, for the rows the text cannot resolve.
UNIFIED = ROOT / "unified" / "data" / "talabat_unified_202608.jsonl"
GEOCODE_CACHE = IT_CACHE / "geocode_cache.json"          # 4,643 coord -> address

# ---- outputs -------------------------------------------------------------
LOOKUP = DATA / "raw_to_clean_lookup.json"
RESOLUTION_MAP = DATA / "area_resolution_map.csv"        # raw -> clean -> method
NEW_AREAS = DATA / "new_areas_for_approval.csv"          # D6
CITY_DISCREPANCIES = DATA / "city_discrepancies_for_tech.csv"   # D2
REVIEW = DATA / "needs_review.csv"
REPORT = DATA / "cleaning_report.json"

# ---- caches owned by this pipeline ---------------------------------------
CACHE = DATA / "cache"
CACHE.mkdir(exist_ok=True)
LLM_CACHE = CACHE / "llm_area_cache.json"
SERPER_CACHE = CACHE / "serper_area_cache.json"
NOMINATIM_CACHE = CACHE / "nominatim_cache.json"

# ---- vocabulary ----------------------------------------------------------
# The city values July shipped. A row's city must be one of these; the dataset
# is UAE-only and anything outside it is rejected, not flagged (D6).
UAE_CITIES = {"Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Ras Al Khaimah",
              "Fujairah", "Umm Al Quwain", "Al Ain", "Khor Fakkan"}

# Names that are ONLY ever a city, never an area. A value resolving to one of
# these is rejected.
#
# Khor Fakkan is deliberately EXCLUDED, per Sagar: July's vocabulary listed it
# as a city, but it is a Sharjah exclave and Tech's file uses it as an area
# inside city="Sharjah" (91 rows). It is treated as an area here. Kalba is the
# same shape - July canonicalised it to Sharjah - so it is excluded too.
CITY_ONLY = UAE_CITIES - {"Khor Fakkan"}
CITY_TOKENS = {c.lower() for c in CITY_ONLY} | {
    "uae", "united arab emirates", "emirates", "u.a.e", "u.a.e."}

# ---- Serper (D4, D5) -----------------------------------------------------
# The query template measured 15/15 in the classification pipeline. Reused
# verbatim so both stages describe the same entity.
SERPER_QUERY = "{name} uae restaurant"
SERPER_ENDPOINT = "https://google.serper.dev/search"

LLM_MODEL = "gpt-4.1-mini"
LLM_BATCH = 25
LLM_CONCURRENCY = 8

# Nominatim policy is 1 req/s. Independent of Google, deliberately: Talabat's
# addresses appear to originate from Google, so Serper/Apify echo the same error
# (measured: wrong value in 2 of 3 spot-checks).
NOMINATIM_DELAY = 1.1
