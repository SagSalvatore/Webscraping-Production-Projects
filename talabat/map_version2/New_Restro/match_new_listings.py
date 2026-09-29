"""
Match New_Discovered_Listings.csv (18,146 rows, name-only, no shared ID with
Talabat, ~10% bilingual/Arabic names) against talabat_restaurants (15,768 rows)
to determine how many are already in Talabat vs genuinely new discoveries.

Strategy (deterministic first, LLM only for the residual ambiguous set):
  1. GEO MATCH (primary signal) — both datasets carry real lat/lon. Build a
     haversine BallTree on talabat_restaurants and find each new listing's
     nearest Talabat branch. Geographic proximity this tight (<=100m) is a
     much stronger identity signal than name text ever is on its own.
  2. NAME CONFIRMATION — geo-closeness alone isn't sufficient (two different
     businesses can share a mall/food-court address), so every geo-close
     candidate is cross-checked with fuzzy name similarity. Names are
     normalized first: bilingual Arabic+Latin names have their Latin portion
     extracted via regex (no translation needed for ~1,507 of the 1,875
     Arabic-containing names); generic suffixes (restaurant, cafe, llc, etc.)
     are stripped to avoid the exact false-positive trap documented in this
     project's own workdone.md ("a single shared generic word is not enough
     to confirm two business names are the same place").
  3. RESIDUAL — geo-close candidates where normalized name similarity is low
     AND the name is pure-Arabic (no Latin at all, ~368 names) are the only
     set where translation could plausibly help; that's the one place an LLM
     call is used, deliberately kept small to control cost.

Output: match_results.csv with a verdict per new listing (Matched / Ambiguous
/ New) plus the underlying evidence (distance, name score) for audit.

Run:
  python talabat/map_version2/New_Restro/match_new_listings.py
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from rapidfuzz import fuzz
from sklearn.neighbors import BallTree

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from db_config import PG_PASSWORD  # noqa: E402

HERE = Path(__file__).parent
NEW_LISTINGS_PATH = HERE / "New_Discovered_Listings.csv"
OUT_PATH = HERE / "match_results.csv"

DB_PARAMS = dict(host="localhost", port=5432, database="RestaurantIntelligence", user="postgres", password=PG_PASSWORD)

EARTH_KM = 6371.0
GEO_MATCH_THRESHOLD_M = 100
# empirically tuned against hand-sampled bands (see analysis in conversation):
# score>=60 was ~100% genuine matches, score<45 was ~100% coincidental/different
# businesses (cloud-kitchen-style shared addresses), 45-60 is genuinely mixed
# and needs LLM arbitration rather than a hardcoded cutoff.
MATCH_SCORE_HIGH = 60
MATCH_SCORE_LOW = 45

GENERIC_WORDS = {
    "restaurant", "cafe", "caf", "coffee", "shop", "llc", "kitchen", "cafeteria",
    "grill", "house", "bakery", "sweets", "the", "and", "co", "uae", "dubai",
    "abu", "dhabi", "sharjah", "ajman", "l", "l.l.c", "fzco", "fz", "branch",
}

ARABIC_PATTERN = re.compile(r"[؀-ۿ]")
LATIN_PATTERN = re.compile(r"[A-Za-z]")


def extract_latin_portion(name: str) -> str:
    """Bilingual names like 'Wadi Al Arayesh Restaurant مطعم وادي العرايش'
    carry the English name directly — strip Arabic script rather than
    translating anything, and keep whatever Latin text remains."""
    latin_only = re.sub(r"[؀-ۿ]+", " ", str(name))
    latin_only = re.sub(r"\s+", " ", latin_only).strip(" |-")
    return latin_only


def normalize_name(name: str) -> str:
    s = extract_latin_portion(name) if ARABIC_PATTERN.search(str(name)) else str(name)
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    words = [w for w in s.split() if w not in GENERIC_WORDS]
    return " ".join(words).strip()


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    talabat = pd.read_sql(
        "SELECT branch_id, restaurant_name, ld_lat, ld_lon FROM talabat_restaurants", conn
    )
    conn.close()

    new = pd.read_csv(NEW_LISTINGS_PATH)
    print(f"talabat_restaurants: {len(talabat):,} | New_Discovered_Listings: {len(new):,}")

    # ── 1. Geo nearest-neighbors (top-K, not just nearest-1) ─────────────────
    # Cloud-kitchen listings mean several DIFFERENT brands can share one exact
    # address — the single nearest point isn't reliably the right one, so pull
    # several candidates within radius and let name similarity pick among them.
    K = 5
    talabat_rad = np.radians(talabat[["ld_lat", "ld_lon"]].values.astype(float))
    tree = BallTree(talabat_rad, metric="haversine")

    new_rad = np.radians(new[["Latitude", "Longitude"]].values.astype(float))
    dist, idx = tree.query(new_rad, k=K)
    dist_m = dist * EARTH_KM * 1000

    new["norm_name_new"] = new["Name"].apply(normalize_name)

    def best_candidate(row_i):
        norm_new = new.at[row_i, "norm_name_new"]
        best = (None, None, 999999.0, 0.0)  # branch_id, name, dist_m, score
        for k in range(K):
            d = dist_m[row_i, k]
            if d > GEO_MATCH_THRESHOLD_M:
                continue
            j = idx[row_i, k]
            cand_name = talabat["restaurant_name"].values[j]
            norm_cand = normalize_name(cand_name)
            s = 0.0
            if norm_new and norm_cand:
                s = max(fuzz.token_set_ratio(norm_new, norm_cand), fuzz.token_sort_ratio(norm_new, norm_cand))
            if s > best[3]:
                best = (talabat["branch_id"].values[j], cand_name, d, s)
        if best[0] is None:
            # no candidate within radius at all -> report the raw nearest for context
            j = idx[row_i, 0]
            best = (talabat["branch_id"].values[j], talabat["restaurant_name"].values[j], dist_m[row_i, 0], 0.0)
        return best

    results = [best_candidate(i) for i in range(len(new))]
    new["nearest_branch_id"] = [r[0] for r in results]
    new["nearest_talabat_name"] = [r[1] for r in results]
    new["dist_m"] = [r[2] for r in results]
    new["name_score"] = [r[3] for r in results]
    new["is_arabic_only"] = new["Name"].apply(
        lambda s: bool(ARABIC_PATTERN.search(str(s))) and not bool(LATIN_PATTERN.search(str(s)))
    )

    # ── 3. Classify ───────────────────────────────────────────────────────────
    def classify(row):
        geo_close = row["dist_m"] <= GEO_MATCH_THRESHOLD_M
        if not geo_close:
            return "New"
        if row["name_score"] >= MATCH_SCORE_HIGH:
            return "Matched"
        if row["name_score"] < MATCH_SCORE_LOW:
            return "New"
        return "Review"

    new["verdict"] = new.apply(classify, axis=1)

    print()
    print(new["verdict"].value_counts())
    print()
    print("Review rows that are Arabic-only (candidates for translation help):")
    print(new[(new["verdict"] == "Review") & new["is_arabic_only"]].shape[0])

    new.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\nSaved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
