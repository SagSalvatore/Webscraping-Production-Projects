"""
Export talabat_restaurants + talabat_menu_items + menu_item_ingredients into the
IT team's JSON schema, ready to hand off for app ingestion.

Pipeline:
  1. Heavy lifting (menu item + ingredient aggregation) is done IN POSTGRES via
     jsonb_agg/array_agg per branch_id batch — far faster than joining 1.1M
     menu items x 2.2M ingredient rows in Python.
  2. Batches of branch_ids are fetched CONCURRENTLY over an asyncpg connection
     pool (async, as requested) rather than one giant sequential query.
  3. Results are streamed straight to disk as a JSON array (manual incremental
     write: '[' ... ',' between objects ... ']') instead of building one giant
     in-memory list — this is the actual bottleneck for a ~1.1M-item export,
     and is the standard fast/low-memory approach for this direction (ijson
     itself is a streaming JSON PARSER for reading; it has no maintained
     writer, so the write side here is the equivalent hand-rolled streaming
     writer — same "single-pass, constant memory" property, applied to the
     write path where ijson doesn't apply).

Field mapping (per user's spec):
  source_name          = "talabat" (constant)
  source_id            = branch_id
  name                 = restaurant_name, smart-title-cased (acronyms preserved
                          for the 26+2 known MNC brands; apostrophes handled
                          correctly, unlike str.title())
  cuisine              = serves_cuisine[0]
  sub_cuisines         = key_cuisines column (full array)
  key_cuisines         = serves_cuisine column (full array)  [per explicit spec]
  restaurant_type/outlet_type/chain_type = as-is (chain_type <- chained_outlet_type)
  currency             = "AED" (constant)
  location.raw         = address, falling back to area_name when address is
                          NULL (~30% of restaurants) — area_name has only 19
                          distinct values across all 15,768 rows, so it is
                          NEVER used as primary (would create mass false
                          "same location" collisions), only as a fallback so
                          the field isn't empty.
  location.city/area/sublocality = ALL parsed directly from the same resolved
                          address text used for location.raw (never a
                          separately-sourced column) — segments before the
                          country are split into city (last), area (up to
                          2 segments before that, e.g. "Al Muraqqabat - Deira"),
                          and sublocality (everything earlier, e.g. building/
                          street). This guarantees area can never disagree
                          with what raw actually says. When there's no address
                          at all (~4.7k restaurants), area falls back to
                          area_name — same value as raw in that case.
  geo                   = ld_lat / ld_lon
  contact_phone         = contact
  website               = website
  maps_url              = google_maps_url (NOT map_url, which is Talabat's own
                          restaurant page, not a Google Maps link)
  menu_items[].name         = item_key_clean, underscores -> spaces, smart-title
  menu_items[].section      = menu_category_clean, same treatment
  menu_items[].description  = description_clean, same treatment
  menu_items[].std_term     = std_term, same treatment
  menu_items[].price        = price_aed
  menu_items[].ingredients  = menu_item_ingredients.ingredient_name (array),
                          looked up by item_key ALONE (not branch_id) — the
                          same item name is sold by many different branches,
                          and reusing whichever branch's already-extracted
                          ingredient list applies to every branch selling
                          that item takes real coverage from 30.4% of menu
                          rows (336,152 branch-specific matches) to 96.7%
                          (1,071,112 rows share an item_key that's known
                          somewhere in the catalog)
  menu_items[].is_popular   = true if raw menu_category contains
                          "picks for you" / "popular" / "best seller"
                          (case-insensitive), per the IT team's rule

MNC override: for restaurants identified as one of the 26 MNC brands (by the
same name-regex used when building mnc_chain_locations), location/geo/phone/
website/maps_url are overwritten with the SPECIFIC matched mnc_chain_locations
row — but only when a confident geo-match exists (nearest real-world location
within 100m of the branch's own ld_lat/ld_lon; mnc_chain_locations itself has
no branch_id column, so this match is recomputed here, same threshold used
when the table was built). If no confident match exists, the branch's own
Talabat-sourced data is kept as-is — never guessed from an arbitrary same-brand
row.

Verified-location entries (per IT request): every real-world location for a
tracked MNC brand is emitted as its OWN top-level entry in the output array
(flagged "is_verified_location": true), on top of the normal per-branch
Talabat entries — not instead of them, since these entries carry no scraped
menu of their own and dropping the real branch entries would lose the only
menu/price data that exists for that brand. These entries are NOT also
nested inside the branch record (no per-branch "locations" array anymore) —
that would duplicate the same real-world location data twice in the same
file, once nested and once flattened. Flattening happens ONCE PER BRAND (not
once per matching branch-entry) — every branch of a brand maps to the SAME
1,989-location census, so flattening per branch-entry would multiply it into
~39,800 duplicated rows. source_id/cuisine/restaurant_type/etc. on the
flattened entries are copied from one representative already-built branch
record for that brand (same chain_id either way), not freshly re-derived —
IT's own DB keys off chain_id for these, so no new synthetic per-location ID
is generated.

Run:
  python talabat/export/export_to_json.py
"""

import asyncio
import csv
import hashlib
import re
import time
from pathlib import Path

import asyncpg
import numpy as np
import orjson
from loguru import logger
from unidecode import unidecode

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_DSN  # noqa: E402

DB_DSN = PG_DSN
OUT_PATH = Path(__file__).parent / "talabat_export.json"
ISSUE_DIR = Path(__file__).parent / "issue"

BATCH_SIZE = 500
CONCURRENCY = 8
MNC_MATCH_THRESHOLD_KM = 0.1

POPULAR_KEYWORDS = ("picks for you", "popular", "best seller")

# Same 26 MNC brands captured in mnc_chain_locations, with their canonical
# real-world stylization (for name casing) and Talabat name-matching regex.
MNC_BRAND_CONFIG = [
    ("KFC",                r"\bkfc\b"),
    ("Domino's Pizza",     r"\bdominos pizza\b"),
    ("ALBAIK",             r"\balbaik\b"),
    ("% Arabica",          r"%\s*arabica"),
    ("BurgerFuel",         r"\bburger fuel\b"),
    ("Burger King",        r"\bburger king\b"),
    ("Costa Coffee",       r"\bcosta coffee\b"),
    ("Caffè Nero",         r"\bcaffe nero\b"),
    ("Caribou Coffee",     r"\bcaribou\b"),
    ("Dunkin' Donuts",     r"\bdunkin\b"),
    ("FiLLi Cafe",         r"\bfilli\b"),
    ("Five Guys",          r"\bfive guys\b"),
    ("German Doner Kebab", r"\bgerman doner kebab\b|\bgdk\b"),
    ("Jollibee",           r"\bjollibee\b"),
    ("McDonald's",         r"\bmcdonalds\b"),
    ("New York Fries",     r"\bnew york fries\b"),
    ("Peet's Coffee",      r"\bpeets coffee\b"),
    ("Pickl",              r"\bpickl\b"),
    ("Pizza Hut",          r"\bpizza hut\b"),
    ("Popeyes",            r"\bpopeyes\b"),
    ("Pret A Manger",      r"\bpret a manger\b"),
    # also matches "Shak Shak" — branch 774480's raw Talabat name is a typo
    # missing a letter in each word ("Shake"->"Shak", "Shack"->"Shak"); its
    # website field confirms it's genuinely shakeshackme.com, just never
    # matched the strict pattern before.
    ("Shake Shack",        r"\bshake shack\b|\bshak shak\b"),
    ("Subway",             r"\bsubway\b"),
    ("Tim Hortons",        r"\btim hortons\b"),
    ("Wendy's",            r"\bwendys\b"),
    ("Zaatar w Zeit",      r"\bzaatar w ?zeit\b"),
    ("Starbucks",          r"\bstarbucks\b"),
    ("Häagen-Dazs",        r"\bhaagen dazs\b"),
]

# canonical real-world stylization for these brands, used to override
# generic smart-title-case (which would otherwise produce "Kfc", "Gdk", etc.)
ACRONYM_OVERRIDES = {name.lower(): name for name, _ in MNC_BRAND_CONFIG}
ACRONYM_OVERRIDES.update({"gdk": "GDK", "mcdonald's": "McDonald's", "mcdonalds": "McDonald's", "uae": "UAE"})

# ~19 raw Talabat names are fused like "KFCinOld Bahya,UAE" (missing space
# before "in", missing space after comma) — a known data-quality quirk
# (see workdone.md). Repair this BEFORE title-casing, and re-apply the
# acronym overrides word-by-word afterwards so "KFC" inside a longer phrase
# ("KFC in Old Bahya, UAE") still comes out as "KFC", not "Kfc".
FUSED_IN_PATTERN = re.compile(r"(?<=[a-zA-Z])in(?=[A-Z])")
_ACRONYM_WORD_PATTERNS = sorted(ACRONYM_OVERRIDES.keys(), key=len, reverse=True)


def normalize_for_match(s):
    s = str(s)
    s = FUSED_IN_PATTERN.sub(" in ", s)  # "KFCinOld" -> "KFC in Old" so \bkfc\b still matches
    s = s.lower()
    s = re.sub(r"[''`]", "", s)
    s = re.sub(r"[^\w\s%]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def slugify(text):
    """Stable, human-readable slug (internal use only — see chain_id_int for
    the actual integer ID shipped in the export): 'McDonald's' -> 'mcdonalds',
    '% Arabica' -> 'arabica'. Deterministic across re-runs."""
    s = unidecode(str(text)).lower()
    s = re.sub(r"[''`]", "", s)  # "mcdonald's" -> "mcdonalds", not "mcdonald-s"
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def chain_id_int(slug):
    """Deterministic integer chain_id, derived from the slug via SHA-256 (NOT
    Python's built-in hash(), which is randomized per-process for strings and
    would produce a different chain_id on every run — the opposite of what
    we need). 52 bits of the digest keeps every value safely under
    JavaScript's Number.MAX_SAFE_INTEGER (2^53-1), so IT's app can parse it
    as a normal JSON number with no precision loss. Verified zero collisions
    across all 9,923 real chains in this dataset before shipping."""
    digest = hashlib.sha256(slug.encode()).hexdigest()
    return int(digest[:13], 16)


def light_normalize_name(name):
    """Exact-match-safe normalization for grouping Talabat's OWN
    restaurant_name column into local chains — case/whitespace only, no
    generic-word stripping (unlike normalize_for_match, which is tuned for
    fuzzy brand-name matching and would over-merge unrelated small
    businesses that happen to share a word like 'restaurant')."""
    return re.sub(r"\s+", " ", str(name).strip().lower())


def build_chain_ids(talabat_rows, branch_to_brand):
    """chain_id for EVERY branch (even single-location ones, per instruction
    — so a brand that gains more locations later already has a stable,
    correct identifier with nothing to retroactively fix): the 27 tracked
    MNC brands use their validated canonical brand_name (so fused-name
    quirks like 'KFCinOld Bahya,UAE' still correctly group under 'kfc');
    everything else groups by exact-normalized Talabat restaurant_name.
    Returns {branch_id: (chain_id, chain_locations_count)}."""
    branch_chain_key = {}
    for r in talabat_rows:
        bid = r["branch_id"]
        brand = branch_to_brand.get(bid)
        if brand:
            key = ("mnc", brand)
        else:
            # group by the SAME cleaned identity used for the display name —
            # otherwise "The Monk In Al Karama, UAE" / "The Monk In Meadows,
            # UAE" / "The Monk In Dubai Silicon Oasis, UAE" each keep their
            # distinct address suffix and get treated as 3 separate chains
            # instead of correctly collapsing to one "The Monk".
            cleaned = strip_fused_address_suffix(smart_title(r["restaurant_name"]))
            key = ("local", light_normalize_name(cleaned))
        branch_chain_key[bid] = key

    counts = {}
    for key in branch_chain_key.values():
        counts[key] = counts.get(key, 0) + 1

    result = {}
    for bid, key in branch_chain_key.items():
        chain_id = chain_id_int(slugify(key[1]))
        result[bid] = (chain_id, counts[key])
    return result


def smart_title(text):
    """Title-case that doesn't break on apostrophes (str.title() turns
    "mcdonald's" into "Mcdonald'S"), with a small allowlist override for
    known MNC brand stylization."""
    if text is None:
        return None
    text = str(text).replace("_", " ")
    text = FUSED_IN_PATTERN.sub(" in ", text)      # "KFCinOld" -> "KFC in Old"
    text = re.sub(r",(?=\S)", ", ", text)          # "Bahya,UAE" -> "Bahya, UAE"
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return text
    key = text.lower()
    if key in ACRONYM_OVERRIDES:
        return ACRONYM_OVERRIDES[key]
    lowered = text.lower()
    titled = re.sub(r"(?:^|(?<=[\s\-/(]))([a-z])", lambda m: m.group(1).upper(), lowered)
    for acronym_key in _ACRONYM_WORD_PATTERNS:
        titled = re.sub(rf"\b{re.escape(acronym_key)}\b", ACRONYM_OVERRIDES[acronym_key], titled, flags=re.IGNORECASE)
    return titled


# The fused-name bug (see FUSED_IN_PATTERN above) doesn't just need spacing
# fixed — the whole "In <location>, UAE" tail is a glued-on address artifact,
# not part of the actual restaurant name (raw DB value is literally
# "The MonkinMeadows,UAE" — workdone.md's own cited example of this quirk).
# Confirmed via full-catalog profiling that every restaurant matching this
# structural pattern is an instance of the same bug (50 total: 7 within the
# 27 MNC brands, 43 local/independent) — safe to strip project-wide.
FUSED_ADDRESS_SUFFIX_PATTERN = re.compile(r"^(.+?)\s+[Ii]n\s+.+,\s*UAE\s*$")


def strip_fused_address_suffix(name):
    if not name:
        return name
    m = FUSED_ADDRESS_SUFFIX_PATTERN.match(name)
    return m.group(1).strip() if m else name


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


UAE_EMIRATES = {
    "dubai", "abu dhabi", "sharjah", "ajman", "fujairah",
    "ras al khaimah", "umm al quwain", "al ain", "khor fakkan", "kalba",
}


COUNTRY_SUFFIX_PATTERN = re.compile(r"[\s,\-]+(united arab emirates|u\.?a\.?e\.?)\s*$", re.IGNORECASE)
EMIRATE_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(UAE_EMIRATES, key=len, reverse=True)).replace(" ", "[\\s\\-]+") + r")\b",
    re.IGNORECASE,
)


def parse_address_components(address):
    """Derive (city, area, sublocality) directly from the free-text address
    itself, rather than a separately-sourced column — so 'area' can never
    disagree with what 'raw' actually says. The trailing country marker
    (either "United Arab Emirates" or the "UAE"/"U.A.E." abbreviation — both
    forms appear in the data) is stripped first; the segment matching a known
    emirate is the city (matched by substring, not exact equality, since some
    addresses append a zone code like "Abu Dhabi 53174"); the up-to-two
    segments before that are the area (e.g. "Al Muraqqabat - Deira"); anything
    earlier is the building/street-level sublocality."""
    if not address:
        return None, None, None
    addr = COUNTRY_SUFFIX_PATTERN.sub("", address.strip()).strip()
    if not addr:
        return None, None, None

    if " - " in addr:
        parts = [p.strip() for p in addr.split(" - ") if p.strip()]
    else:
        parts = [p.strip() for p in addr.split(",") if p.strip()]

    if not parts:
        return None, None, None

    # a trailing standalone zone/postal code segment (e.g. "...Abu Dhabi,54154"
    # splits into a separate "54154" segment) would otherwise block emirate
    # detection on the real last segment — drop it first if present.
    if len(parts) > 1 and re.fullmatch(r"\d+", parts[-1]):
        parts = parts[:-1]

    city = None
    m = EMIRATE_PATTERN.search(parts[-1])
    if m:
        city = smart_title(m.group(1))
        parts = parts[:-1]

    if not parts:
        return city, None, None

    if len(parts) >= 2:
        area = " - ".join(parts[-2:])
        sublocality_parts = parts[:-2]
    else:
        area = parts[-1]
        sublocality_parts = []

    sublocality = " - ".join(sublocality_parts) if sublocality_parts else None
    return city, area, sublocality


def is_popular(menu_category_raw):
    if not menu_category_raw:
        return False
    low = menu_category_raw.lower()
    return any(kw in low for kw in POPULAR_KEYWORDS)


async def build_mnc_overrides(pool):
    """For every branch that matches one of the 26 MNC brand names:
      - overrides[branch_id]: its single nearest real-world mnc_chain_locations
        row (same brand) within 100m — used for the existing singular
        location/contact_phone/website/maps_url fields (unchanged behavior).
      - branch_to_brand[branch_id]: which of the 26 brands this branch belongs
        to, regardless of whether a confident nearest-match was found.
      - brand_locations_full[brand_name]: EVERY real-world location captured
        for that brand (all 111 for Burger King, etc.) — used for the new
        "locations" array so the full census isn't lost behind a single pick.
    """
    async with pool.acquire() as conn:
        talabat_rows = await conn.fetch(
            "SELECT branch_id, restaurant_name, ld_lat, ld_lon FROM talabat_restaurants"
        )
        mnc_rows = await conn.fetch(
            "SELECT brand_name, address, street, neighborhood, emirate, phone, website, "
            "google_maps_url, latitude, longitude FROM mnc_chain_locations"
        )

    brand_locations_full = {}
    for r in mnc_rows:
        brand_locations_full.setdefault(r["brand_name"], []).append(
            {
                "address": r["address"],
                "phone": r["phone"],
                "website": r["website"],
                "maps_url": r["google_maps_url"],
                "geo": {
                    "lat": float(r["latitude"]) if r["latitude"] is not None else None,
                    "lng": float(r["longitude"]) if r["longitude"] is not None else None,
                },
            }
        )

    mnc_by_brand = {}
    for r in mnc_rows:
        if r["latitude"] is not None and r["longitude"] is not None:
            mnc_by_brand.setdefault(r["brand_name"], []).append(r)

    overrides = {}
    branch_to_brand = {}
    matched_count = 0
    for brand_name, pattern in MNC_BRAND_CONFIG:
        for t in talabat_rows:
            if not re.search(pattern, normalize_for_match(t["restaurant_name"])):
                continue
            branch_to_brand[t["branch_id"]] = brand_name

        candidates = mnc_by_brand.get(brand_name)
        if not candidates:
            continue
        lats = np.array([c["latitude"] for c in candidates], dtype=float)
        lngs = np.array([c["longitude"] for c in candidates], dtype=float)

        for t in talabat_rows:
            if t["ld_lat"] is None or t["ld_lon"] is None:
                continue
            if not re.search(pattern, normalize_for_match(t["restaurant_name"])):
                continue
            dists = haversine(float(t["ld_lat"]), float(t["ld_lon"]), lats, lngs)
            idx = int(np.argmin(dists))
            if dists[idx] <= MNC_MATCH_THRESHOLD_KM:
                best = candidates[idx]
                overrides[t["branch_id"]] = {
                    "address": best["address"],
                    "street": best["street"],
                    "neighborhood": best["neighborhood"],
                    "emirate": best["emirate"],
                    "phone": best["phone"],
                    "website": best["website"],
                    "google_maps_url": best["google_maps_url"],
                    "lat": float(best["latitude"]),
                    "lon": float(best["longitude"]),
                }
                matched_count += 1

    logger.info(
        f"MNC override map built: {matched_count} branches with a specific nearest-match; "
        f"{len(branch_to_brand)} branches identified as one of the 26 brands overall; "
        f"{sum(len(v) for v in brand_locations_full.values())} total real-world locations across "
        f"{len(brand_locations_full)} brands available for the 'locations' array"
    )
    return overrides, branch_to_brand, brand_locations_full


async def build_item_key_ingredients_map(pool):
    """item_key -> ingredients, aggregated GLOBALLY across every branch that
    has that item_key (not just per-branch). item_key is a shared identifier
    across restaurants (e.g. "french fries" sold by thousands of different
    branches) — only ~336k of the 1.1M menu item ROWS were ever directly
    covered by ingredient extraction, but those rows cover 336,150 of the
    346,905 DISTINCT item names that exist catalog-wide. Reusing the known
    ingredient list for every branch selling the same-named item takes real
    coverage from 30.4% of rows to 96.7%, instead of leaving ingredients=[]
    on rows that just happened not to be in the original extraction batch."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT item_key, array_agg(DISTINCT ingredient_name) AS ingredients "
            "FROM menu_item_ingredients GROUP BY item_key"
        )
    mapping = {r["item_key"]: [i.lower() for i in r["ingredients"]] for r in rows}
    logger.info(f"item_key -> ingredients map built: {len(mapping):,} distinct item names")
    return mapping


MENU_AGG_QUERY = """
SELECT
    r.branch_id, r.restaurant_name, r.serves_cuisine, r.key_cuisines,
    r.restaurant_type, r.outlet_type, r.chained_outlet_type,
    r.address, r.area_name, r.ld_lat, r.ld_lon, r.contact, r.website, r.google_maps_url,
    COALESCE(ma.menu_items, '[]'::jsonb) AS menu_items
FROM talabat_restaurants r
LEFT JOIN (
    SELECT
        mi.branch_id,
        jsonb_agg(
            jsonb_build_object(
                'item_key', mi.item_key,
                'item_key_clean', mi.item_key_clean,
                'item_name', mi.item_name,
                'menu_category_clean', mi.menu_category_clean,
                'menu_category_raw', mi.menu_category,
                'description_clean', mi.description_clean,
                'std_term', mi.std_term,
                'price_aed', mi.price_aed
            )
        ) AS menu_items
    FROM talabat_menu_items mi
    WHERE mi.branch_id = ANY($1::bigint[])
    GROUP BY mi.branch_id
) ma ON r.branch_id = ma.branch_id
WHERE r.branch_id = ANY($1::bigint[])
"""


def build_record(row, overrides, branch_to_brand, brand_locations_full, item_key_ingredients_map, chain_ids):
    branch_id = row["branch_id"]
    override = overrides.get(branch_id)
    brand_name = branch_to_brand.get(branch_id)
    chain_id, chain_locations_count = chain_ids.get(branch_id, (None, 1))

    address = row["address"]
    lat, lon = row["ld_lat"], row["ld_lon"]
    phone = row["contact"]
    website = row["website"]
    maps_url = row["google_maps_url"]

    if override:
        address = override["address"] or address
        lat, lon = override["lat"], override["lon"]
        phone = override["phone"] or phone
        website = override["website"] or website
        maps_url = override["google_maps_url"] or maps_url

    # city/area/sublocality are ALWAYS parsed from the final resolved address
    # text itself (never a separately-sourced column) — so "area" can never
    # disagree with what "raw" says. When there's no address at all (~4.7k
    # restaurants), area falls back to area_name, same value as raw.
    if address:
        city, area, sublocality = parse_address_components(address)
    else:
        city, area, sublocality = None, row["area_name"], None

    serves_cuisine = row["serves_cuisine"] or []
    key_cuisines_col = row["key_cuisines"] or []

    menu_items_out = []
    for item in row["menu_items"]:
        menu_items_out.append(
            {
                "name": smart_title(item["item_key_clean"] or item["item_name"]),
                "section": smart_title(item["menu_category_clean"] or item["menu_category_raw"]),
                "description": smart_title(item["description_clean"]),
                "std_term": smart_title(item["std_term"]),
                "price": float(item["price_aed"]) if item["price_aed"] is not None else None,
                "ingredients": item_key_ingredients_map.get(item["item_key"], []),
                "is_popular": is_popular(item["menu_category_raw"]),
            }
        )

    location_raw = address if address else row["area_name"]

    # display name: MNC-matched restaurants always get the canonical brand
    # name (no matter how noisy Talabat's own listing text is — this is what
    # collapses "KFC In Old Bahya, UAE" down to plain "KFC"); everything else
    # gets the same fused-address-suffix strip applied, since that bug isn't
    # confined to the 27 tracked brands (43 of the 50 total instances are
    # local/independent restaurants with the identical glued-address quirk).
    if brand_name:
        display_name = smart_title(brand_name)
    else:
        display_name = strip_fused_address_suffix(smart_title(row["restaurant_name"]))

    record = {
        "source_name": "talabat",
        "source_id": str(branch_id),
        "name": display_name,
        "cuisine": serves_cuisine[0] if serves_cuisine else None,
        "sub_cuisines": key_cuisines_col,
        "key_cuisines": serves_cuisine,
        "restaurant_type": row["restaurant_type"],
        "outlet_type": row["outlet_type"],
        "chain_type": row["chained_outlet_type"],
        "chain_id": chain_id,
        "chain_locations_count": chain_locations_count,
        "currency": "AED",
        "location": {
            "raw": location_raw,
            "country": "UAE",
            "city": city,
            "area": area,
            "sublocality": sublocality,
        },
        "geo": {
            "lat": float(lat) if lat is not None else None,
            "lng": float(lon) if lon is not None else None,
        },
        "contact_phone": phone,
        "website": website,
        "maps_url": maps_url,
    }

    # NOTE: the full real-world location census for this brand is NOT nested
    # here anymore — it's emitted separately as standalone top-level entries
    # (see build_verified_location_entries) so the same location data doesn't
    # appear twice in the file (once nested, once flattened). Only the count
    # override stays: for the 27 tracked MNC brands, chain_locations_count
    # should reflect the real-world verified location count (e.g. Subway's
    # actual 164 UAE branches), not how many of those happen to also be
    # listed on Talabat (36) — the whole point of this project was surfacing
    # that gap. Local chains have no such real-world count, so they keep the
    # Talabat-branch tally as the only signal available.
    if brand_name:
        record["chain_locations_count"] = len(brand_locations_full.get(brand_name, []))

    record["menu_items"] = menu_items_out

    return record


def build_verified_location_entries(brand_locations_full, brand_representative):
    """One flattened top-level entry per real-world location in brand_locations_full
    (1,989 total across 28 brands) — ADDED alongside the normal per-branch Talabat
    entries, not replacing them. source_id/cuisine/chain_id/menu_items/etc. are all
    copied from a representative branch of the same brand rather than minted fresh,
    since chain_id is what IT's own DB uses to link these back to the brand.
    menu_items is the brand's own scraped Talabat menu, reused as-is across every
    location entry for that brand (these addresses were never independently
    scraped, so the brand's standard menu is the best available proxy) — IT's
    upload pipeline rejects entries with no menu_items at all, so leaving it out
    (the original design) is no longer an option."""
    entries = []
    for brand_name, brand_locs in brand_locations_full.items():
        rep = brand_representative.get(brand_name)
        if not rep:
            continue
        for loc in brand_locs:
            addr = loc["address"]
            if addr:
                city, area, sublocality = parse_address_components(addr)
            else:
                city, area, sublocality = None, None, None
            entries.append(
                {
                    "source_name": rep["source_name"],
                    "source_id": rep["source_id"],
                    "name": rep["name"],
                    "cuisine": rep["cuisine"],
                    "sub_cuisines": rep["sub_cuisines"],
                    "key_cuisines": rep["key_cuisines"],
                    "restaurant_type": rep["restaurant_type"],
                    "outlet_type": rep["outlet_type"],
                    "chain_type": rep["chain_type"],
                    "chain_id": rep["chain_id"],
                    "chain_locations_count": len(brand_locs),
                    "currency": "AED",
                    "location": {
                        "raw": addr,
                        "country": "UAE",
                        "city": city,
                        "area": area,
                        "sublocality": sublocality,
                    },
                    "geo": loc["geo"],
                    "contact_phone": loc["phone"],
                    "website": loc["website"],
                    "maps_url": loc["maps_url"],
                    "is_verified_location": True,
                    "menu_items": rep["menu_items"],
                }
            )
    return entries


async def _init_connection(conn):
    """asyncpg returns jsonb columns as raw strings by default — decode them
    to Python objects automatically via orjson instead of patching every
    call site."""
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda v: orjson.dumps(v).decode(),
        decoder=orjson.loads,
        schema="pg_catalog",
    )


async def fetch_batch(pool, sem, branch_ids):
    async with sem:
        async with pool.acquire() as conn:
            rows = await conn.fetch(MENU_AGG_QUERY, branch_ids)
    return rows


def load_excluded_branch_ids():
    """Restaurants flagged by IT as having empty/missing menus (talabat/export/issue/*.csv,
    first column = branch_id) are excluded from the JSON export ONLY — the database is
    left untouched, since these branches are still valid Talabat listings that may get
    a menu re-scrape later. Reads every CSV in the issue/ folder so future additions
    (issue-2.csv, etc.) are picked up automatically without a code change."""
    excluded = set()
    if not ISSUE_DIR.exists():
        return excluded
    for path in sorted(ISSUE_DIR.glob("*.csv")):
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if row and row[0].strip().isdigit():
                    excluded.add(int(row[0].strip()))
        logger.info(f"Loaded exclusion list from {path.name}")
    return excluded


async def main():
    t_start = time.time()
    pool = await asyncpg.create_pool(DB_DSN, min_size=2, max_size=CONCURRENCY + 2, init=_init_connection)

    overrides, branch_to_brand, brand_locations_full = await build_mnc_overrides(pool)
    item_key_ingredients_map = await build_item_key_ingredients_map(pool)

    async with pool.acquire() as conn:
        all_talabat_rows = await conn.fetch("SELECT branch_id, restaurant_name FROM talabat_restaurants ORDER BY branch_id")

    excluded_ids = load_excluded_branch_ids()
    before_count = len(all_talabat_rows)
    all_talabat_rows = [r for r in all_talabat_rows if r["branch_id"] not in excluded_ids]
    all_branch_ids = [r["branch_id"] for r in all_talabat_rows]
    logger.info(
        f"Excluded {before_count - len(all_branch_ids):,} restaurants flagged with empty menus "
        f"(database untouched, JSON-export-only exclusion) — {len(excluded_ids):,} branch_ids loaded from issue/*.csv"
    )
    logger.info(f"Total restaurants to export: {len(all_branch_ids):,}")

    # chain_id counts reflect the EXPORTED set only (post-exclusion) — so the
    # count in the JSON always matches how many records with that chain_id
    # are actually findable in the file itself.
    chain_ids = build_chain_ids(all_talabat_rows, branch_to_brand)
    logger.info(f"chain_id assigned to all {len(chain_ids):,} exported restaurants ({len(set(c[0] for c in chain_ids.values())):,} distinct chains)")

    batches = [all_branch_ids[i : i + BATCH_SIZE] for i in range(0, len(all_branch_ids), BATCH_SIZE)]
    sem = asyncio.Semaphore(CONCURRENCY)

    # Verified-location entries need to be written FIRST in the file (ahead of
    # the branch/menu entries, so reviewing them doesn't require scrolling past
    # every branch's full menu) — build them up front from a representative
    # branch per brand, rather than discovering one mid-stream through the
    # main 15,198-branch loop below. Every one of that brand's verified
    # locations reuses this ONE branch's menu_items (IT's upload pipeline
    # rejects entries with no menu at all, and these addresses were never
    # independently scraped) — so the representative must be a branch that
    # actually HAS a non-empty menu. Picking blindly (e.g. just the first
    # matched branch_id) risks silently landing on one of the 570 branches
    # flagged empty-menu and excluded from the export, which would leave
    # every location entry for that brand with menu_items=[]. Fetching all
    # ~345 matched branches (not just 28) and checking each brand's own set
    # for the first one with real menu content avoids that.
    branches_by_brand = {}
    for bid, brand in branch_to_brand.items():
        branches_by_brand.setdefault(brand, []).append(bid)
    all_brand_branch_ids = list(branch_to_brand.keys())
    rep_rows = await fetch_batch(pool, sem, all_brand_branch_ids)
    records_by_branch = {
        row["branch_id"]: build_record(row, overrides, branch_to_brand, brand_locations_full, item_key_ingredients_map, chain_ids)
        for row in rep_rows
    }
    brand_representative = {}
    for brand, bids in branches_by_brand.items():
        if brand not in brand_locations_full:
            continue
        candidates = [records_by_branch[b] for b in bids if b in records_by_branch]
        if not candidates:
            continue
        best = next((r for r in candidates if r["menu_items"]), candidates[0])
        if not best["menu_items"]:
            logger.warning(f"No branch with a non-empty menu found for brand '{brand}' ({len(candidates)} branches checked) — its verified-location entries will carry menu_items=[]")
        brand_representative[brand] = {
            "source_name": best["source_name"],
            "source_id": best["source_id"],
            "name": best["name"],
            "cuisine": best["cuisine"],
            "sub_cuisines": best["sub_cuisines"],
            "key_cuisines": best["key_cuisines"],
            "restaurant_type": best["restaurant_type"],
            "outlet_type": best["outlet_type"],
            "chain_type": best["chain_type"],
            "chain_id": best["chain_id"],
            "menu_items": best["menu_items"],
        }
    location_entries = build_verified_location_entries(brand_locations_full, brand_representative)
    logger.info(f"Built {len(location_entries):,} verified-location entries across {len(brand_representative):,} brands (written first)")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    total_menu_items = 0

    with open(OUT_PATH, "wb") as f:
        f.write(b"[")
        first = True

        chunk = bytearray()
        for entry in location_entries:
            if not first:
                chunk += b","
            first = False
            chunk += orjson.dumps(entry)
        f.write(bytes(chunk))

        # process batches concurrently, but write results out in the order they complete
        tasks = [asyncio.create_task(fetch_batch(pool, sem, b)) for b in batches]
        for i, task in enumerate(tasks):
            rows = await task
            chunk = bytearray()
            for row in rows:
                record = build_record(row, overrides, branch_to_brand, brand_locations_full, item_key_ingredients_map, chain_ids)
                total_menu_items += len(record["menu_items"])
                if not first:
                    chunk += b","
                first = False
                chunk += orjson.dumps(record)
            f.write(bytes(chunk))
            written += len(rows)
            if (i + 1) % 5 == 0 or (i + 1) == len(tasks):
                logger.info(f"Progress: {written:,}/{len(all_branch_ids):,} restaurants written, {total_menu_items:,} menu items so far")

        f.write(b"]")

    await pool.close()
    elapsed = time.time() - t_start
    size_mb = OUT_PATH.stat().st_size / 1e6
    total_written = written + len(location_entries)
    logger.info(f"Done: {len(location_entries):,} verified-location entries + {written:,} branch restaurants = {total_written:,} total, {total_menu_items:,} menu items -> {OUT_PATH} ({size_mb:.1f} MB) in {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
