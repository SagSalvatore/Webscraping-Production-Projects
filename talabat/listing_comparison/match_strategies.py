"""Cascading match strategies - decide whether a run-2 listing is the SAME
physical venue as one we already hold.

Rather than a single name+geo rule, candidates run down a ladder of strategies
ordered strongest-first. The FIRST rule that fires wins, and its name is
recorded, so every match is auditable and you can accept/reject by strategy
instead of trusting one opaque score.

Signals available in both datasets (verified):
    restaurant_id   Talabat's chain id
    branch_slug     Talabat's URL slug (parsed out of `url`)
    name / ld_name  listing name and JSON-LD canonical name
    lat/lon         listing coords
    ld_lat/ld_lon   JSON-LD coords - an INDEPENDENT second opinion on position
    serves_cuisine  cuisine signature, used only to corroborate

Critical semantics: restaurant_id identifies a CHAIN, not a venue. KFC has one
restaurant_id across 258 branches, so restaurant_id alone can never prove two
rows are the same place - it is only decisive when paired with proximity.
That is why every restaurant_id rule below carries a distance guard.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from common import haversine_m, norm_name

_SLUG = re.compile(r"/restaurant/\d+/([^/?#]+)")


def slug_of(row: dict) -> str:
    if row.get("branch_slug"):
        return str(row["branch_slug"]).strip().lower()
    m = _SLUG.search(row.get("url") or "")
    return m.group(1).lower() if m else ""


def best_coords(row: dict):
    """Prefer JSON-LD coords when present - they come from Talabat's own
    structured data rather than the listing card."""
    for a, b in (("ld_lat", "ld_lon"), ("lat", "lon")):
        if row.get(a) and row.get(b):
            try:
                return float(row[a]), float(row[b])
            except (TypeError, ValueError):
                continue
    return None


def cuisine_set(row: dict) -> set[str]:
    v = row.get("matched_cuisines") or row.get("serves_cuisine") or ""
    if isinstance(v, list):
        parts = v
    else:
        parts = str(v).split(",")
    return {p.strip().lower() for p in parts if p and p.strip()}


def prepare(row: dict) -> dict:
    """Attach derived keys once, so the matcher does no repeated work."""
    row["_slug"] = slug_of(row)
    row["_name_key"] = norm_name(row.get("name"))
    row["_ld_key"] = norm_name(row.get("ld_name"))
    row["_coords"] = best_coords(row)
    row["_cuisines"] = cuisine_set(row)
    return row


def distance(a: dict, b: dict) -> float:
    ca, cb = a.get("_coords"), b.get("_coords")
    if not ca or not cb:
        return float("inf")
    return haversine_m(ca[0], ca[1], cb[0], cb[1])


# --------------------------------------------------------------------------
# The ladder. Each returns (strategy_name, confidence) or None.
# Ordered strongest -> weakest; first hit wins.
# --------------------------------------------------------------------------

def s1_slug_exact(n, e, d):
    """Identical Talabat URL slug AND co-located.

    The distance guard is essential and was missing in the first version: the
    slug is BRAND-based, not venue-unique, so two branches of one brand share
    it. Without the guard this rule matched a venue in Abu Dhabi to one 155 km
    away in RAK at 0.99 confidence. Slug alone proves same brand, never same
    place - see s1b for that weaker claim.
    """
    if n["_slug"] and n["_slug"] == e["_slug"] and d <= 100:
        return "slug_exact+coords_100m", 0.99
    return None


def s1b_slug_no_geo(n, e, d):
    """Identical slug but the coordinates disagree, or one side has none.

    Deliberately LOW confidence and placed near the bottom of the ladder: this
    is almost always a legitimate second branch of the same brand, not a
    re-listing. Surfaced for review rather than silently dropped, because when
    coordinates are missing entirely it is the only signal left.
    """
    if n["_slug"] and n["_slug"] == e["_slug"]:
        if d == float("inf"):
            return "slug_exact+no_coords", 0.60
        if d > 100:
            return f"slug_exact+far_{d/1000:.0f}km_LIKELY_DIFFERENT_BRANCH", 0.25
    return None


def s2_chain_same_point(n, e, d):
    """Same chain id AND effectively the same coordinates. A chain cannot have
    two branches 30m apart, so this is a re-listing."""
    if n.get("restaurant_id") and n["restaurant_id"] == e.get("restaurant_id") and d <= 30:
        return "restaurant_id+coords_30m", 0.98
    return None


def s3_chain_close(n, e, d):
    """Same chain id, very close. Slightly looser radius for GPS drift between
    a listing card and JSON-LD."""
    if n.get("restaurant_id") and n["restaurant_id"] == e.get("restaurant_id") and d <= 120:
        return "restaurant_id+coords_120m", 0.93
    return None


def s4_name_same_point(n, e, d):
    """Identical normalised brand at the same point, different chain id.
    Happens when Talabat re-onboards a venue under a fresh restaurant_id."""
    if n["_name_key"] and n["_name_key"] == e["_name_key"] and d <= 50:
        return "name_exact+coords_50m", 0.95
    return None


def s5_ldname_same_point(n, e, d):
    """JSON-LD canonical names agree at the same point. Catches cases where the
    listing card names differ but Talabat's structured data agrees."""
    if n["_ld_key"] and n["_ld_key"] == e["_ld_key"] and d <= 50:
        return "ld_name_exact+coords_50m", 0.94
    return None


def s6_name_near(n, e, d):
    if n["_name_key"] and n["_name_key"] == e["_name_key"] and d <= 250:
        return "name_exact+coords_250m", 0.85
    return None


def s7_fuzzy_near(n, e, d):
    """Fuzzy name with a tight radius. Cuisine overlap is required as
    corroboration so two unrelated venues in one mall do not match."""
    if d > 150 or not n["_name_key"] or not e["_name_key"]:
        return None
    sc = fuzz.token_sort_ratio(n["_name_key"], e["_name_key"])
    if sc >= 90 and (not n["_cuisines"] or not e["_cuisines"]
                     or n["_cuisines"] & e["_cuisines"]):
        return f"fuzzy_name_{sc:.0f}+coords_150m", 0.80
    return None


def s8_fuzzy_same_area(n, e, d):
    """Last resort for rows whose coordinates are missing or unreliable:
    high fuzzy name within the SAME area_id, plus cuisine overlap."""
    if n.get("area_id") and n["area_id"] == e.get("area_id") and n["_name_key"]:
        sc = fuzz.token_sort_ratio(n["_name_key"], e["_name_key"])
        if sc >= 95 and n["_cuisines"] & e["_cuisines"]:
            return f"fuzzy_name_{sc:.0f}+same_area", 0.70
    return None


# strongest -> weakest; first hit wins. s1b sits at the bottom because an
# identical slug at distance means "same brand, different branch", which is the
# opposite of a re-listing.
LADDER = [s1_slug_exact, s2_chain_same_point, s3_chain_close, s4_name_same_point,
          s5_ldname_same_point, s6_name_near, s7_fuzzy_near, s8_fuzzy_same_area,
          s1b_slug_no_geo]

# a match at or above this is treated as a genuine re-listing; below it the row
# is reported for review but still counted as a new venue
ACCEPT_THRESHOLD = 0.80


def match(new_row: dict, existing_row: dict):
    """-> (strategy, confidence, distance_m) or None."""
    d = distance(new_row, existing_row)
    for rule in LADDER:
        hit = rule(new_row, existing_row, d)
        if hit:
            return hit[0], hit[1], (None if d == float("inf") else round(d, 1))
    return None
