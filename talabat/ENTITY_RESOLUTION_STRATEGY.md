# Cross-Platform Restaurant Entity Resolution Strategy

## The Problem

When you scrape the same restaurant from Talabat AND Zomato, you get two independent
records with no shared key:

| Platform | ID    | Name                        | Address             |
|----------|-------|-----------------------------|---------------------|
| Talabat  | 663941 | Al-Banosh Seafood - Al Wasl | Al Wasl Rd, Dubai  |
| Zomato   | 18273 | Al Banosh Seafood           | Al-Wasl Road, Dubai |

Goal: detect that these are **the same physical branch** and create one canonical
restaurant entity that links both platform IDs.

---

## Why Simple Approaches Fail

### Name match
- "Al-Banosh" vs "Al Banosh" — hyphen difference → no exact match
- "McDonald's" vs "McDonalds" vs "Mac Donald's" — common divergence
- "KFC" exists in 200+ locations in UAE — even a perfect name match is ambiguous

### Address match
- "Al Wasl Road" vs "Al-Wasl Rd" vs "Wasl Road, Bur Dubai" — same street, different formats
- Arabic/English mixed: "شارع الوصل" vs "Al Wasl Road"
- Platform-specific abbreviations, missing floor/unit numbers

### VLOOKUP mental exercise
If you were doing VLOOKUP in Excel to map Talabat ↔ Zomato:

```
=VLOOKUP(talabat_name, zomato_table, zomato_id, FALSE)
```

This fails because:
- Slight name differences return `#N/A`
- Duplicate names (10 KFCs) return the wrong row

The only way VLOOKUP actually works here is if you build a **computed key** that
is consistent across both platforms:

```
=VLOOKUP(ROUND(lat,3)&"_"&ROUND(lon,3), zomato_geo_table, zomato_id, FALSE)
```

**Rounded geo-coordinates are the closest thing to a universal key for a physical location.**

---

## The Matching Hierarchy

Use these rules in order — stop at the first hit:

| Priority | Rule | Confidence | Notes |
|----------|------|-----------|-------|
| 1 | Phone number exact match | 100% | Best but rarely exposed by both platforms |
| 2 | Geo ≤ 30m + fuzzy name ≥ 0.85 | 99% | Main production rule |
| 3 | Geo ≤ 100m + fuzzy name ≥ 0.90 + same cuisine | 95% | Wider radius, stricter name |
| 4 | Geo ≤ 50m + exact normalized name | 95% | For when fuzzy is flaky |
| 5 | Geo ≤ 200m + fuzzy name ≥ 0.95 | 80% | Flag for manual review |
| 6 | No match | — | Create new entity |

**Geo distance is the anchor. Name similarity is the tiebreaker.**

---

## Geo-Key Design

Round lat/lon to 3 decimal places ≈ ±55m precision at UAE latitudes.
This is precise enough that two different restaurants almost never share a geo-key,
but forgiving enough to absorb GPS drift between platforms.

```python
def geo_key(lat: float, lon: float) -> str:
    """Deterministic key for ~55m radius cell."""
    return f"{round(lat, 3)}_{round(lon, 3)}"

# Example:
# Talabat: lat=25.205, lon=55.275 → key "25.205_55.275"
# Zomato:  lat=25.2051, lon=55.2749 → key "25.205_55.275"  ← same key!
```

---

## Name Normalization

Before fuzzy matching, normalize both names:

```python
import re
import unicodedata

def normalize_name(name: str) -> str:
    # Lowercase
    name = name.lower()
    # Remove diacritics (café → cafe)
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    # Strip punctuation and extra spaces
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Strip location suffixes that vary by platform
    suffixes = [
        r"\b(branch|br|uae|dubai|abu dhabi|sharjah|restaurant|rest|cafe|kitchen|kitch)\b"
    ]
    for pat in suffixes:
        name = re.sub(pat, "", name)
    return name.strip()

# "Al-Banosh Seafood - Al Wasl"   → "al banosh seafood al wasl"
# "Al Banosh Seafood"              → "al banosh seafood"
# fuzzy_ratio → 0.87 → MATCH
```

---

## The Unified Restaurant Entity Schema

```json
{
  "entity_id": "RE_UAE_001234",
  "canonical_name": "Al-Banosh Seafood Restaurant",
  "geo": {
    "lat": 25.205226,
    "lon": 55.274179,
    "geo_key": "25.205_55.274"
  },
  "city": "Dubai",
  "area": "Al Wasl",
  "cuisine_types": ["Seafood", "Arabic"],
  "platforms": {
    "talabat": {
      "branch_id": 663941,
      "restaurant_id": 12345,
      "url": "https://www.talabat.com/uae/restaurant/663941/...",
      "area_id": 1280,
      "first_seen": "2026-06-12",
      "last_seen": "2026-06-12"
    },
    "zomato": {
      "restaurant_id": "zomato_18273",
      "url": "https://www.zomato.com/dubai/...",
      "first_seen": null,
      "last_seen": null
    },
    "deliveroo": null,
    "noon_food": null
  },
  "match_confidence": {
    "talabat_zomato": 0.92,
    "match_rule": "geo_30m_name_fuzzy"
  },
  "created_at": "2026-06-12T08:00:00Z",
  "updated_at": "2026-06-12T08:00:00Z"
}
```

---

## Ingestion Pipeline Logic

```python
from difflib import SequenceMatcher
from math import radians, sin, cos, sqrt, atan2

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Distance in metres between two geo points."""
    R = 6_371_000
    φ1, φ2 = radians(lat1), radians(lat2)
    Δφ = radians(lat2 - lat1)
    Δλ = radians(lon2 - lon1)
    a = sin(Δφ/2)**2 + cos(φ1)*cos(φ2)*sin(Δλ/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

def fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def find_or_create_entity(new_branch: dict, entity_db: list[dict]) -> dict:
    """
    Given a freshly scraped branch, find an existing entity or create one.
    new_branch must have: name, lat, lon, platform, platform_id
    """
    name_norm = normalize_name(new_branch["name"])
    candidates = []

    for entity in entity_db:
        dist = haversine_m(
            new_branch["lat"], new_branch["lon"],
            entity["geo"]["lat"], entity["geo"]["lon"]
        )
        if dist > 200:
            continue  # too far — fast reject

        sim = fuzzy_ratio(name_norm, normalize_name(entity["canonical_name"]))
        candidates.append((entity, dist, sim))

    # Apply matching rules in priority order
    for entity, dist, sim in sorted(candidates, key=lambda x: x[1]):
        if dist <= 30 and sim >= 0.85:
            return _link(entity, new_branch, "geo_30m_name_0.85", sim)
        if dist <= 100 and sim >= 0.90:
            return _link(entity, new_branch, "geo_100m_name_0.90", sim)
        if dist <= 50 and normalize_name(entity["canonical_name"]) == name_norm:
            return _link(entity, new_branch, "geo_50m_exact_name", 1.0)
        if dist <= 200 and sim >= 0.95:
            # Flag for manual review but link tentatively
            entity["needs_review"] = True
            return _link(entity, new_branch, "geo_200m_name_0.95_REVIEW", sim)

    # No match — create new entity
    return _create_entity(new_branch)


def _link(entity: dict, branch: dict, rule: str, confidence: float) -> dict:
    platform = branch["platform"]
    entity["platforms"][platform] = {
        "branch_id": branch.get("branch_id"),
        "restaurant_id": branch.get("restaurant_id"),
        "url": branch["url"],
        "first_seen": branch["scraped_at"],
        "last_seen": branch["scraped_at"],
    }
    entity["match_confidence"][f"talabat_{platform}"] = confidence
    entity["match_confidence"]["match_rule"] = rule
    return entity
```

---

## Database Design for Price Tracking

```sql
-- Canonical restaurant entity (one row per physical branch)
CREATE TABLE restaurant_entities (
    entity_id       TEXT PRIMARY KEY,  -- "RE_UAE_001234"
    canonical_name  TEXT NOT NULL,
    lat             NUMERIC,
    lon             NUMERIC,
    geo_key         TEXT,              -- "25.205_55.274" for fast lookup
    city            TEXT,
    area            TEXT,
    needs_review    BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_geo_key ON restaurant_entities (geo_key);

-- Per-platform branch data
CREATE TABLE platform_branches (
    id              SERIAL PRIMARY KEY,
    entity_id       TEXT REFERENCES restaurant_entities(entity_id),
    platform        TEXT NOT NULL,     -- 'talabat', 'zomato', etc.
    branch_id       TEXT,              -- platform-specific branch ID
    restaurant_id   TEXT,              -- platform-specific chain ID
    url             TEXT,
    match_confidence NUMERIC,
    match_rule      TEXT,
    first_seen      DATE,
    last_seen       DATE
);

CREATE UNIQUE INDEX idx_platform_branch ON platform_branches (platform, branch_id);

-- Price history (populated by Phase 2 menu scraper)
CREATE TABLE price_history (
    id              SERIAL PRIMARY KEY,
    entity_id       TEXT REFERENCES restaurant_entities(entity_id),
    platform        TEXT,
    item_id         TEXT,
    item_name       TEXT,
    price           NUMERIC,
    currency        TEXT DEFAULT 'AED',
    recorded_at     TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Edge Cases

| Scenario | How to handle |
|----------|--------------|
| Same restaurant chain, 2 branches 80m apart | Both have different branch_ids and geo-keys → stay separate ✓ |
| Restaurant moves location (new address) | New geo-key → new entity created; old one gets `status=closed` |
| Ghost kitchen (multiple brands, same address) | Same geo, different names → separate entities linked by geo proximity flag |
| Platform shows wrong geo (pin dropped at street, not building) | ≤100m threshold absorbs normal GPS drift; >200m = no match |
| Restaurant renamed | Old canonical_name preserved; `name_history[]` tracks changes |
| Arabic name on one platform, English on other | Transliterate both to Latin before fuzzy match |

---

## Implementation Priority

1. **Phase 1 (Now):** Scrape Talabat → store with `branch_id`, `restaurant_id`, `lat`, `lon` ✓
2. **Phase 2:** Scrape menus → attach to entities by `branch_id`
3. **Phase 3:** Scrape Zomato → run entity resolution → link or create
4. **Phase 4:** Supabase `restaurant_entities` table as master record; price delta tracking

The geo-based matching requires lat/lon from both platforms. **Make sure every scraper
always captures lat/lon** — it is the only reliable cross-platform anchor.
