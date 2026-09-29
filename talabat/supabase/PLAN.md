# Supabase Database Schema — UAE Food Aggregator Intelligence
## Project: sagar.singh@mordorintelligence.com's Project-2
## Project ID: cbagujraolgpcjmnpplg | Region: ap-southeast-2 | Status: ACTIVE_HEALTHY

---

## Overview

9 tables total (+ 2 chain tables). Implementation order matters (FK dependencies).

```
ingredients_taxonomy          ← no deps, create first
scrape_runs                   ← no deps
chain_brands                  ← no deps
talabat_restaurants           ← no deps
talabat_menu_items            ← deps: talabat_restaurants, scrape_runs
talabat_menu_deltas           ← deps: talabat_restaurants, scrape_runs
fact_restaurants              ← deps: none (platform-agnostic bridge)
chain_locations               ← deps: chain_brands, fact_restaurants (entity_group_id)
menu_item_ingredients         ← deps: ingredients_taxonomy
[zomato/deliveroo/noon_*]     ← future, same pattern as talabat_*
```

---

## Table 1 — `scrape_runs`
Log of every scraping run across all platforms. Referenced by menu tables.

| Column | Type | Notes |
|--------|------|-------|
| run_id | TEXT PK | e.g. `20260616_141830` |
| platform | TEXT NOT NULL | `talabat` / `zomato` / `deliveroo` / `noon` |
| mode | TEXT | `TEST` / `FULL` |
| restaurants_scraped | INTEGER DEFAULT 0 | |
| restaurants_changed | INTEGER DEFAULT 0 | |
| restaurants_no_change | INTEGER DEFAULT 0 | |
| restaurants_failed | INTEGER DEFAULT 0 | |
| items_total | INTEGER DEFAULT 0 | |
| items_added | INTEGER DEFAULT 0 | |
| items_removed | INTEGER DEFAULT 0 | |
| items_price_changed | INTEGER DEFAULT 0 | |
| items_desc_changed | INTEGER DEFAULT 0 | |
| items_cat_changed | INTEGER DEFAULT 0 | |
| started_at | TIMESTAMPTZ NOT NULL | |
| completed_at | TIMESTAMPTZ | NULL until run finishes |

---

## Table 2 — `talabat_restaurants`
Master restaurant record for every Talabat UAE branch scraped.
One row per branch_id. Updated on each re-scrape if any field changes.

| Column | Type | Notes |
|--------|------|-------|
| branch_id | BIGINT PK | Talabat's branch identifier |
| restaurant_id | BIGINT | Talabat's parent restaurant ID |
| restaurant_name | TEXT NOT NULL | Current name (from scraper) |
| ld_name | TEXT | Name from JSON-LD structured data |
| restaurant_type | TEXT | e.g. `Fast Food`, `Casual Dining` |
| outlet_type | TEXT | `Chain` / `Independent` |
| chained_outlet_type | TEXT | e.g. `Local Chain`, `International Chain` |
| std_terms | TEXT | Standardised cuisine terminology |
| contact | TEXT | Phone / WhatsApp |
| map_url | TEXT | Talabat listing URL |
| serves_cuisine | TEXT[] | All cuisine tags from Talabat |
| key_cuisines | TEXT[] | Primary cuisines (top 3) |
| ld_lat | NUMERIC(10,7) | Latitude from JSON-LD |
| ld_lon | NUMERIC(10,7) | Longitude from JSON-LD |
| area_id | INTEGER | Talabat's internal area ID |
| area_name | TEXT | Neighbourhood / district |
| first_scraped_at | TIMESTAMPTZ NOT NULL | When first seen |
| updated_at | TIMESTAMPTZ NOT NULL | Last any field changed |

---

## Table 3 — `talabat_menu_items`
Full current menu snapshot for each restaurant.
Replaced on each scrape run (upsert on branch_id + item_key).

| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL PK | |
| branch_id | BIGINT NOT NULL FK → talabat_restaurants | |
| run_id | TEXT NOT NULL FK → scrape_runs | |
| item_id | TEXT | Talabat's internal item ID |
| item_name | TEXT NOT NULL | Display name |
| item_key | TEXT NOT NULL | Normalised key (lower, stripped) |
| menu_category | TEXT | `originalSection` from __NEXT_DATA__ |
| price_aed | NUMERIC(10,2) | Price in AED |
| description | TEXT | Item description |
| image_url | TEXT | |
| scraped_at | TIMESTAMPTZ NOT NULL | |
| UNIQUE | (branch_id, item_key) | Latest state per item |

---

## Table 4 — `talabat_menu_deltas`
Every detected change vs the previous snapshot.
Append-only — never update, never delete. Full history.

| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL PK | |
| branch_id | BIGINT NOT NULL FK → talabat_restaurants | |
| run_id | TEXT NOT NULL FK → scrape_runs | |
| change_type | TEXT NOT NULL | `ADDED` / `REMOVED` / `CHANGED` |
| item_name | TEXT | |
| item_key | TEXT | |
| menu_category | TEXT | |
| field_changed | TEXT | `price` / `description` / `menu_category` / `new_item` / `removed_item` |
| old_value | TEXT | |
| new_value | TEXT | |
| old_price_aed | NUMERIC(10,2) | Populated when field_changed = 'price' |
| new_price_aed | NUMERIC(10,2) | Populated when field_changed = 'price' |
| price_change_pct | NUMERIC(8,2) | % change, positive = increase |
| detected_at | TIMESTAMPTZ NOT NULL | Timestamp of detection |
| baseline_run_id | TEXT | run_id of the comparison baseline |

---

## Table 5 — `fact_restaurants`
**Entity resolution bridge table.**
One row per unique real-world restaurant outlet, mapped to all platform IDs.
Enables cross-platform comparison (same restaurant on Talabat + Zomato + Deliveroo).

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK DEFAULT gen_random_uuid() | |
| platform | TEXT NOT NULL | `talabat` / `zomato` / `deliveroo` / `noon` / `hungerstation` / `ubereats` |
| platform_restaurant_id | TEXT | Platform's parent restaurant ID |
| platform_branch_id | TEXT NOT NULL | Branch-level ID (branch_id for Talabat) |
| restaurant_name | TEXT NOT NULL | Canonical name used for matching |
| normalized_name | TEXT | lower + strip + collapse spaces (for fuzzy match) |
| lat | NUMERIC(10,7) | |
| lon | NUMERIC(10,7) | |
| area_name | TEXT | |
| country_code | TEXT NOT NULL DEFAULT 'AE' | ISO country code |
| cuisine_types | TEXT[] | |
| outlet_type | TEXT | `Chain` / `Independent` |
| entity_group_id | UUID | Groups same outlet across platforms (flat-earth geo match) |
| first_seen_at | TIMESTAMPTZ NOT NULL | |
| last_updated_at | TIMESTAMPTZ NOT NULL | |
| is_active | BOOLEAN NOT NULL DEFAULT TRUE | FALSE if listing removed |
| UNIQUE | (platform, platform_branch_id) | One record per platform+branch |

> **entity_group_id**: When the same physical restaurant appears on Talabat AND Zomato,
> both rows share the same UUID. Matched via: normalized_name similarity > 0.85 AND
> haversine distance < 150m. Unmatched rows have entity_group_id = NULL.

---

## Table 6 — `ingredients_taxonomy`
Loaded from `FoodAnalytics.Ingredient_category_taxonomy.json`.
~1,000+ ingredients already classified.

| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL PK | |
| ingredient_name | TEXT NOT NULL UNIQUE | Original case preserved |
| ingredient_category | TEXT | `Fruits` / `Vegetables` / `Meat` / `Dairy` / `Seafood` / `Spices and Seasoning` / `Nuts and Seeds` / `Bakery and Cereal` / `Sweeteners` / `Fats and Oils` / `Beverages and Additives` / `Prepared and Processed Ingredients` / `Additives and Preservatives` / `Flavorings` / `Seaweed and Marine Products` |
| normalized_name | TEXT | For fuzzy matching against menu descriptions |
| source | TEXT DEFAULT 'FoodAnalytics' | Origin of taxonomy |
| created_at | TIMESTAMPTZ DEFAULT NOW() | |

---

## Table 7 — `menu_item_ingredients`
Links scraped menu items to the ingredient taxonomy.
Populated by AI (GPT-4o-mini parsing item descriptions) or keyword matching.

| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL PK | |
| platform | TEXT NOT NULL | |
| branch_id | TEXT NOT NULL | Platform branch ID |
| item_key | TEXT NOT NULL | Menu item key |
| ingredient_name | TEXT NOT NULL FK → ingredients_taxonomy(ingredient_name) | |
| ingredient_category | TEXT | Denormalized for fast queries |
| extraction_method | TEXT | `ai` / `keyword` / `manual` |
| confidence | NUMERIC(3,2) | 0.00–1.00 |
| extracted_at | TIMESTAMPTZ DEFAULT NOW() | |
| UNIQUE | (platform, branch_id, item_key, ingredient_name) | |

---

## Table 8 — `chain_brands`
Parent brand master table. One row per chain (KFC, McDonald's, Shake Shack, etc.).
Populated manually + enriched from Talabat's `chained_outlet_type` field.

| Column | Type | Notes |
|--------|------|-------|
| chain_id | BIGSERIAL PK | |
| chain_name | TEXT NOT NULL UNIQUE | Canonical brand name e.g. `KFC` |
| chain_name_normalized | TEXT | Lower + stripped for matching |
| chain_type | TEXT | `International Chain` / `Local Chain` / `Regional Chain` |
| country_of_origin | TEXT | e.g. `USA`, `UAE`, `Lebanon` |
| total_locations_uae | INTEGER DEFAULT 0 | Computed from chain_locations count |
| cuisine_category | TEXT | Primary cuisine type |
| website | TEXT | Brand website |
| created_at | TIMESTAMPTZ DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ DEFAULT NOW() | |

---

## Table 9 — `chain_locations`
Every individual outlet of a chain brand in the UAE scraped via **Apify Google Maps**.
One row per `place_id`. Each Apify run updates ratings/reviews; address/geo never change.

| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL PK | |
| chain_id | BIGINT NOT NULL FK → chain_brands | |
| place_id | TEXT NOT NULL UNIQUE | Google Maps unique place identifier |
| google_maps_url | TEXT | Full Google Maps URL |
| location_name | TEXT | Branch-specific name e.g. `KFC - Dubai Mall` |
| phone | TEXT | Local phone number |
| address | TEXT | Full street address |
| city | TEXT DEFAULT 'Dubai' | |
| emirate | TEXT | `Dubai` / `Abu Dhabi` / `Sharjah` / `Ajman` etc. |
| geo_lat | NUMERIC(10,7) | |
| geo_lng | NUMERIC(10,7) | |
| category | TEXT | Primary Google Maps category e.g. `Fast food restaurant` |
| all_categories | TEXT[] | All Google Maps categories for this place |
| category_type | TEXT | `Restaurant` / `Cafe` / `Bakery` / `Food Delivery` |
| price_range | TEXT | `$` / `$$` / `$$$` / `$$$$` |
| rating | NUMERIC(3,1) | 0.0–5.0 |
| review_count | INTEGER DEFAULT 0 | Total number of reviews |
| rating_distribution | JSONB | `{"5": 120, "4": 45, "3": 12, "2": 5, "1": 8}` |
| is_permanently_closed | BOOLEAN DEFAULT FALSE | |
| entity_group_id | UUID | FK → fact_restaurants.entity_group_id (geo-matched) |
| apify_run_id | TEXT | Which Apify actor run produced this row |
| scraped_at | TIMESTAMPTZ NOT NULL | When Apify scraped it |
| updated_at | TIMESTAMPTZ DEFAULT NOW() | Last ratings refresh |

### How chain tables link to everything else

```
chain_brands (chain_id)
    └── chain_locations (chain_id FK)
            └── entity_group_id ──→ fact_restaurants (entity_group_id)
                                          └── platform_branch_id ──→ talabat_restaurants
                                                                  └── zomato_restaurants
                                                                  └── deliveroo_restaurants
```

**Flow for a KFC outlet:**
- `chain_brands`: chain_name = "KFC", chain_type = "International Chain"
- `chain_locations`: place_id = "ChIJ...", address = "Dubai Mall", rating = 4.2, review_count = 1847
- `fact_restaurants`: platform = "talabat", platform_branch_id = "8415", entity_group_id = UUID_X
- `chain_locations.entity_group_id` = UUID_X (geo matched < 150m)
- This lets you query: "All KFC outlets in Dubai + their Talabat menu + price changes"

### Apify Actor
Use **Apify Google Maps Scraper** (`compass/crawler-google-places`).
Input: search query = `"KFC UAE"` or `"McDonald's Dubai"` per chain.
Outputs all fields above. Trigger via Apify REST API from Python.

---

## Indexes (Performance)

```sql
-- talabat_restaurants
CREATE INDEX idx_talabat_rest_area     ON talabat_restaurants(area_name);
CREATE INDEX idx_talabat_rest_cuisine  ON talabat_restaurants USING GIN(serves_cuisine);
CREATE INDEX idx_talabat_rest_geo      ON talabat_restaurants(ld_lat, ld_lon);

-- talabat_menu_items
CREATE INDEX idx_menu_items_branch     ON talabat_menu_items(branch_id);
CREATE INDEX idx_menu_items_run        ON talabat_menu_items(run_id);
CREATE INDEX idx_menu_items_category   ON talabat_menu_items(menu_category);

-- talabat_menu_deltas
CREATE INDEX idx_deltas_branch         ON talabat_menu_deltas(branch_id);
CREATE INDEX idx_deltas_detected       ON talabat_menu_deltas(detected_at);
CREATE INDEX idx_deltas_field          ON talabat_menu_deltas(field_changed);
CREATE INDEX idx_deltas_type           ON talabat_menu_deltas(change_type);

-- fact_restaurants
CREATE INDEX idx_fact_platform         ON fact_restaurants(platform);
CREATE INDEX idx_fact_entity_group     ON fact_restaurants(entity_group_id);
CREATE INDEX idx_fact_geo              ON fact_restaurants(lat, lon);
CREATE INDEX idx_fact_normalized       ON fact_restaurants(normalized_name);

-- ingredients
CREATE INDEX idx_ingredient_cat        ON ingredients_taxonomy(ingredient_category);
CREATE INDEX idx_ingredient_norm       ON ingredients_taxonomy(normalized_name);

-- chain_brands
CREATE INDEX idx_chain_brand_name      ON chain_brands(chain_name_normalized);
CREATE INDEX idx_chain_brand_type      ON chain_brands(chain_type);

-- chain_locations
CREATE INDEX idx_chain_loc_chain       ON chain_locations(chain_id);
CREATE INDEX idx_chain_loc_emirate     ON chain_locations(emirate);
CREATE INDEX idx_chain_loc_geo         ON chain_locations(geo_lat, geo_lng);
CREATE INDEX idx_chain_loc_entity      ON chain_locations(entity_group_id);
CREATE INDEX idx_chain_loc_rating      ON chain_locations(rating);
CREATE INDEX idx_chain_loc_categories  ON chain_locations USING GIN(all_categories);
CREATE INDEX idx_chain_loc_rating_dist ON chain_locations USING GIN(rating_distribution);
```

---

## Future Platform Tables (same pattern)
When Zomato / Deliveroo / Noon scraping begins:
- `zomato_restaurants` — same structure, zomato-specific IDs (`res_id`, `branch_id`)
- `deliveroo_restaurants` — (`restaurant_id`, `postcode`)
- `noon_restaurants` — (`store_id`, `area_code`)
- Each platform gets its own `_menu_items` and `_menu_deltas` tables
- `fact_restaurants` is the single join point across all platforms

---

## Implementation Steps (after your review)

1. **Create all 9 tables** via Supabase MCP `apply_migration`
2. **Seed `ingredients_taxonomy`** from `FoodAnalytics.Ingredient_category_taxonomy.json` (~1k rows)
3. **Add Supabase writer to `talabat_menu_tracker.py`** — after each scrape run:
   - Upsert into `scrape_runs`
   - Upsert into `talabat_restaurants`
   - Upsert into `talabat_menu_items`
   - Insert into `talabat_menu_deltas`
   - Upsert into `fact_restaurants`
4. **Run full 5,275 restaurant scrape** — data flows directly to Supabase
5. **Populate `chain_brands`** — extract unique `chained_outlet_type` values from talabat_restaurants, normalise into brand names
6. **Apify Google Maps scrape per chain brand** — one actor run per chain, results → `chain_locations`
7. **Geo-match `chain_locations` ↔ `fact_restaurants`** — Python script: haversine < 150m + name similarity > 0.85 → write `entity_group_id`
8. **Ingredient extraction** — batch job parsing menu descriptions via GPT-4o-mini → `menu_item_ingredients`

---

## Notes / Decisions

- **No RLS for now** — internal data pipeline, no user-facing auth needed yet. Add when building the client-facing app.
- **`talabat_menu_items` is upserted, not appended** — keeps current state lean. Full history is in `talabat_menu_deltas`.
- **`fact_restaurants.entity_group_id`** starts NULL for all Talabat rows; populated later when a second platform (Zomato) is scraped and geo-name matching runs.
- **Prices always in AED** — numeric(10,2) gives 8 digits before decimal, 2 after. Max representable: 99,999,999.99 AED. Safe.
- **`item_key`** is the normalised item name (lower, strip, collapse spaces) — used as the matching key for delta detection, consistent with the Python scraper.
