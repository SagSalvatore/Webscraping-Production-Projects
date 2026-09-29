# UAE Restaurant Directory Enrichment Plan (Revised)

## Objective

Build a **UAE-wide restaurant location directory**. For every unique
restaurant brand pulled from Talabat, discover **every physical
location that brand actually has across the UAE** via Google Maps —
not just the branches Talabat happens to list. The deliverable proves
to the client: *"this restaurant isn't just in the N places Talabat
shows — it's actually present in these M locations across the
country,"* backed by real Google data.

Talabat is the **seed list of brand names** (and a rough location
hint). Google Maps (via Apify) is the **source of truth for full
coverage** — including locations Talabat never listed.

------------------------------------------------------------------------

## Final Output Fields

-   Talabat Restaurant Name (search term used)
-   Brand Type — Chain / Independent
-   Talabat Branch Count (for that name)
-   Google Business Name
-   Complete Address
-   Phone Number
-   Website
-   Google Maps URL
-   Google Place ID
-   Latitude / Longitude
-   Rating
-   Review Count
-   Category / All Categories
-   Business Status
-   Emirate (derived)
-   Talabat Source URL (one representative `map_url`, for traceability)

Schema builds on the already-proven `output/KFC_UAE.csv` shape
(`place_id, Name, Address, Street, Neighborhood, Contact_No,
Google_Maps_URL, Geo_Lat, Geo_Lng, Category, All_Categories, Rating,
Review_Count, Emirate`), adding **Website** (not captured in the KFC
run but required per client spec) and the Talabat traceability
columns.

------------------------------------------------------------------------

## Current Dataset

`apify/Complete_list_apify.csv` — **15,768 Talabat branches**

| Column | Description |
|---|---|
| branch_id | Talabat branch identifier (unique, 15,768/15,768) |
| restaurant_id | Talabat restaurant identifier (11,658 unique — confirms chains exist) |
| restaurant_name | Restaurant name |
| ld_name | Display name (duplicate of restaurant_name in practice) |
| map_url | Talabat branch URL — contains `aid=` delivery-zone param |
| ld_lat / ld_lon | Branch coordinates |
| area_id / area_name | Talabat delivery zone (see note below — **not** a real neighborhood) |
| serves_cuisine | Cuisine tags |

### Data quality already checked
- 0 empty names / lat / lon / map_url
- 1 bad coordinate row (`Dawgs & Co`, `ld_lat=0`) — flag and exclude from distance checks
- `area_id`/`area_name` is Talabat's **delivery zone**, not a neighborhood — only **17 zones** cover all 15,768 branches (one zone, "Al Barsha 3", covers 5,207 rows spanning a huge stretch of Dubai). It maps cleanly to **emirate** level only (Dubai/Sharjah/Ajman belt vs Abu Dhabi vs Northern Emirates), with ~3 GPS-noise outliers. **Use it only as a coarse emirate cross-check, not as the search-query city.**
- One zone has a BOM/encoding artifact on "Dubai World Trade Center - DWTC" (3 byte-variants of the same string) — needs cleanup before grouping.
- Real city/emirate for search queries and matching comes from **reverse-geocoding `ld_lat`/`ld_lon`**, cross-validated against the `area_id` emirate as a sanity check.

### Already completed — exclude from remaining work
- **KFC** — done (`apify/brands/kfc`, `output/KFC_UAE.csv`, 247 places)
- **Domino's** — done (`apify/brands/dominos`)

### Remaining scope after dedup + exclusions

**Important correction:** chain-vs-independent status is decided by
**what Google Maps actually returns**, not by how many branches
Talabat lists. A name with 1 Talabat branch can easily turn out to be
a real local chain with 5 physical locations Talabat never onboarded
— capping its search low in advance would truncate that discovery and
undercut the whole point of the project. The only names classified
*before* searching are a small curated list of already-known global
giants (McDonald's, Starbucks, Subway, etc. — see Phase 4).

| Segment | Unique names | Talabat branches covered |
|---|---|---|
| Known global chains (pre-classified allowlist) | 46 | 404 |
| Everyone else (classification decided after search) | 10,235 | 15,352 |
| **Total remaining** | **10,281** | **15,756** |

`Brand_Type` (Independent / Local Chain / Global Chain) is a
**derived output field**, computed after Google results come back:
1 result → Independent, 2+ results → Local Chain, name on the
allowlist → Global Chain.

------------------------------------------------------------------------

## System Architecture

```text
Talabat CSV (15,768 branches)
    ↓
Data Cleaning + Brand Classification (chain vs independent)
    ↓
Dedup → 10,281 unique search terms (KFC/Domino's excluded)
    ↓
Emirate Derivation (lat/lon reverse-geocode, cross-checked vs area_id)
    ↓
Search Query Builder (Name + Emirate + UAE)
    ↓
Apify Google Maps Scraper — TWO-TIER search settings
    ↓
Google Places Dataset (all discovered locations per brand)
    ↓
Output Assembly + Traceability linking
    ↓
UAE Restaurant Master Directory
```

------------------------------------------------------------------------

## Apify Integration

The Claude Desktop **Apify MCP extension repeatedly fails to install**
(`ENOTEMPTY` errors on different `node_modules` subfolders each
attempt — a race condition during extraction, not a one-off). Rather
than depend on it, built a local wrapper:

**`apify/apify_helper.py`** — thin REST client over Apify API v2 using
`APIFY_API_TOKEN` from the root `.env` (already present and verified
live). Provides everything the MCP connector would:

```bash
python apify_helper.py whoami              # account/plan/pricing info
python apify_helper.py info <actor_id>     # actor details + current pricing
python apify_helper.py search "<query>"    # search the Apify Store
python apify_helper.py run <actor_id> input.json   # trigger + poll a run
python apify_helper.py dataset <dataset_id>        # pull results
```

### Confirmed account details
- Plan: **STARTER** ($29/mo, $29 usage credits included)
- Pricing tier: **BRONZE**
- Actor: `compass/crawler-google-places` ("Google Maps Scraper")
- **Pricing model: PAY_PER_EVENT** (changed from flat compute units —
  the old plan's $22–37 estimate assumed a different, cheaper tier)

| Charge event | BRONZE price |
|---|---|
| `place-scraped` (primary) | **$0.003** / place |
| `place-details-scraped` (add-on) | $0.002 / place |
| `filter-applied` (add-on) | $0.001 / place / filter |
| `apify-actor-start` | $0.00005 / GB memory (one-time per run) |
| review/image/contact/lead enrichment | not used — disabled per spec |

------------------------------------------------------------------------

## Phase 1. Data Cleaning & Pre-Classification

Normalize (lowercase, trim, remove duplicate spaces/punctuation/™
symbols, normalize "&"/"and", strip marketing words) → build
`restaurant_name_clean`.

Pre-classify only against the curated known-global-chain allowlist
(`apify/brands/known_global_chains.json` — 46 names, 407 branches,
compiled by matching common multinational QSR/coffee/retail brands
against the actual `restaurant_name` column, with 3 confirmed
false-positive substring matches excluded — e.g. "burger kingdom" ≠
Burger King). Everything else stays unclassified until Phase 4
results come back — see the correction under "Remaining scope" above.

Drop the 1 row with `ld_lat=0` from any distance-based logic; keep it
in the base dataset with a flag.

------------------------------------------------------------------------

## Phase 1.5. Emirate Derivation

Primary source: reverse-geocode `ld_lat`/`ld_lon` via offline bounding
boxes for the 7 emirates. Cross-check against the `area_id`-derived
region (Dubai/Sharjah/Ajman belt vs Abu Dhabi vs Northern Emirates) —
mismatches get flagged for manual look, but in practice only ~3 rows
disagree out of 15,768.

Clean the DWTC BOM-encoding artifact (`ï»¿Dubai World Trade Center -
DWTC` / `﻿Dubai World Trade Center - DWTC` / `Dubai World Trade Center
- DWTC` → single canonical value) before grouping by zone.

------------------------------------------------------------------------

## Phase 2. Deduplication

```text
15,768 branches
      ↓ (exclude KFC "kfc": -5 branches, Domino's "domino's pizza": -7 branches)
15,756 branches remaining, 2 names excluded
      ↓ normalize + group by cleaned name
10,281 unique search terms
      ↓ split against known-chain allowlist only
46 known global chains  +  10,235 unclassified (resolved post-search)
```

------------------------------------------------------------------------

## Phase 3. Search Query Builder

Primary: `Restaurant Name + Emirate + UAE`
Fallback: `Restaurant Name + UAE`

Uses the **derived** emirate (Phase 1.5), not the raw scraped
`area_name`, since the latter is zone-level and would misdirect
searches (e.g. tagging an Al Nahyan restaurant as "Zayed Sports City").

------------------------------------------------------------------------

## Phase 4. Google Maps Extraction — Allowlist + Default Tier

Chain-vs-independent status is **not** knowable in advance (see
correction above) except for a small set of already-known global
giants. So there are only two search tiers, and only one of them is
based on prior knowledge:

**Known global chain tier** (46 names, `apify/brands/known_global_chains.json`)
- `maxCrawledPlacesPerSearch`: **150**
- These are brands we already know from common knowledge have large
  UAE footprints (McDonald's, Starbucks, Subway, Burger King, Pizza
  Hut, etc.) — no need to make Google "prove" it with a low cap first
- Batch size: all 46 in one run (small, high-value)

**Default tier** (10,235 names — everything not on the allowlist)
- `maxCrawledPlacesPerSearch`: **30**
- High enough to catch a real local/regional chain Google reveals
  (e.g. a 5–15 branch UAE bakery chain Talabat only lists once),
  without paying for near-duplicate noise on genuine one-location
  independents
- Business Details: enabled (address, phone, website, coordinates,
  rating, review count, place ID, status) — for both tiers
- Reviews / Images / Menus / Emails: disabled — for both tiers
- Batch size: ~500 names/run, ~21 batches

`Brand_Type` is assigned **after** results return: allowlist name →
Global Chain; 2+ Google results → Local Chain; exactly 1 Google
result → Independent.

------------------------------------------------------------------------

## Phase 5. Output Assembly

Merge Google Places results back with:
- `Talabat_Restaurant_Name` (the search term)
- `Brand_Type` (Chain/Independent)
- `Talabat_Branch_Count` (lets the client see "Talabat shows 3, Google
  shows 12")
- `Talabat_Source_URL` (one representative `map_url` for traceability)
- `Emirate` (from Phase 1.5)

No 1:1 branch-matching, confidence scoring, or manual-review queue —
that reconciliation problem doesn't apply here. Every discovered
Google location is retained as a legitimate UAE presence of the brand.

------------------------------------------------------------------------

## Phase 6. Validation Batch

Given KFC (247 places) and Domino's already ran successfully
end-to-end through this same actor/schema, skip the original 10 → 100
→ 500 staged crawl. Instead:

- **Validation batch: ~300 names** — mix of default-tier names
  (Arabic/English/mixed, mall restaurants, single- and multi-branch
  Talabat entries) plus the full 46-name known-chain allowlist, to
  confirm both tiers behave correctly
- Check: field completeness, cost-per-place actuals vs estimate,
  Emirate assignment accuracy, and — critically — how many
  default-tier names come back with 2+ Google results (this
  calibrates the real avg-places-per-search assumption behind the
  cost estimate before committing to the full 10,235-name run)

If validation batch passes → proceed straight to full production.

------------------------------------------------------------------------

## Phase 7. Full Production Run

```text
Known-chain run     (46 names, single batch, cap=150)
Default-tier batches (10,235 names ÷ ~500/batch ≈ 21 batches, cap=30)
      ↓
Per-batch: run → poll → pull dataset → checkpoint to disk
      ↓
Retry failed searches (max 3x), log and continue — never halt a batch
for one failure
      ↓
Merge all batch outputs → derive Brand_Type per name from actual
result counts → UAE Restaurant Master Directory
```

------------------------------------------------------------------------

## Estimated Cost (BRONZE tier, `place-scraped` = $0.003)

| Segment | Names | Est. avg places/search | Est. total places | Est. cost |
|---|---|---|---|---|
| Known global chains | 46 | ~40 (capped at 150, real chains like Starbucks likely land well below the cap) | ~1,840 | ~$5.50 |
| Default tier | 10,235 | ~3 (mix of true independents at 1 and small local chains at 2–10, capped at 30) | ~30,700 | ~$92 |
| **Total (moderate scenario)** | **10,281** | | **~32,540** | **~$98** |

Range across scenarios: **~$70–$180** depending on actual Google
result density — the default tier is the main cost driver since it
covers 99.5% of remaining names, and its true average won't be known
until the validation batch (Phase 6) runs. Actor-start fees are
negligible when batched (a handful of runs, not 10,281 individual
runs).

This replaces the original plan's $22–37 estimate, which was based on
GOLD-tier pricing ($0.0015/place) — your account is BRONZE
($0.003/place). Current Starter plan credit ($29/mo) will need
top-up; recommend checking Apify's usage dashboard mid-run to track
actual spend against this estimate.

------------------------------------------------------------------------

## Deliverables

Main
- `uae_restaurant_directory.csv` — full merged output, all tiers

Supporting
- `independent_results_raw.json` / `chain_results_raw.json`
- `validation_batch_results.csv`
- `statistics.json` (places found vs Talabat branches, by brand)
- `logs/` (per-batch run IDs, retries, failures)

------------------------------------------------------------------------

## Recommended Python Stack

- Python 3.13+
- `requests` (direct Apify REST calls via `apify_helper.py`)
- `pandas` / `polars` for CSV merging
- `python-dotenv`
- `loguru`
- Optional: RapidFuzz for name-cleaning fuzzy dedup checks (light QA
  use only, not a matching engine), Haversine for emirate
  cross-validation

------------------------------------------------------------------------

## Project Structure

```text
apify/
├── UAE_Restaurant_Directory_Enrichment_Plan_v2.md   ← this file
├── apify_helper.py            ← local Apify API wrapper (MCP replacement)
├── Complete_list_apify.csv    ← source data (15,768 branches)
├── brands/
│   ├── kfc/                   ← done
│   ├── dominos/                ← done
│   └── local/
├── output/
│   ├── KFC_UAE.csv            ← done, reference schema
│   ├── raw/
│   └── logs/
└── scripts/
    ├── 1_clean_and_classify.py
    ├── 2_derive_emirate.py
    ├── 3_dedup_search_terms.py
    ├── 4_run_apify_batches.py
    └── 5_merge_output.py
```

------------------------------------------------------------------------

## Production KPIs

| KPI | Target |
|---|---|
| Google Retrieval Success | >98% |
| Failed Searches | <2% |
| Chain-tier: Google count ≥ Talabat count | >90% of chains |
| Field Completeness (address/coords/place ID) | >95% |
| Field Completeness (phone/website) | >70% (Google data availability varies) |
| Total Runtime | <8 hours (batched) |

------------------------------------------------------------------------

## Future Enhancements

- Cache Google Place IDs to avoid re-searching on future refreshes
- Re-run only new/changed Talabat names on subsequent syncs
- Multi-platform enrichment (Deliveroo, Zomato, Noon Food) using the
  same brand-census approach
- UAE Restaurant Master Database (persistent store, not just CSV)
