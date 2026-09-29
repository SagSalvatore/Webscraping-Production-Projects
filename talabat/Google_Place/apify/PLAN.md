# Apify — Dubai F&B Lead Generation Scraper
## Architecture & Cost Plan

---

## 1. Objective

Scrape **Google Maps via Apify** (`compass/crawler-google-places`) to build a structured
database of Dubai food & beverage establishments across 16 high-density areas for:

- **Lead generation** (outreach to restaurants, cafes, cloud kitchens)
- **Full-stack app data layer** (search, filter, map views, analytics)

**Target output:** `~1,800–2,500` unique UAE food establishments with verified details.

---

## 2. Why Apify + `compass/crawler-google-places`

| Option | Pro | Con |
|--------|-----|-----|
| **Apify `compass/crawler-google-places`** | Battle-tested, handles anti-bot, rich output, no API quota limits | Costs credits |
| Google Places API (existing) | Already set up | 1 req/restaurant, needs known names, no discovery |
| Manual Google Maps scraping | Free | Blocked instantly, maintenance nightmare |
| Talabat / Zomato scraping | Has menus | Only shows listed restaurants, misses independents |

**Verdict:** Apify is the right tool for **discovery** (finding places we don't know yet).
The existing Google Places API is best for **enrichment** (filling in details for known slugs).

---

## 3. Apify Actor Details

```
Actor   :  compass/crawler-google-places
Apify ID:  compass/crawler-google-places
Type    :  Free actor (no per-run fee — only platform compute units charged)
Docs    :  https://apify.com/compass/crawler-google-places
```

### Key Input Parameters

| Parameter | Value | Why |
|-----------|-------|-----|
| `searchStringsArray` | ["restaurants in Downtown Dubai", ...] | One query per area × category |
| `maxCrawledPlacesPerSearch` | **40** | Budget control — enough per area |
| `language` | `en` | English output |
| `countryCode` | `ae` | UAE only |
| `maxReviews` | `0` | We want ratings, NOT full review text (saves ~60% cost) |
| `maxImages` | `0` | No images needed |
| `includeOpeningHours` | `false` | Not needed for lead gen |
| `includePeopleAlsoSearch` | `false` | Cuts compute |
| `scrapeDirectories` | `false` | Cuts compute |
| `additionalInfo` | `false` | Cuts compute |
| `proxyConfig` | `{ "useApifyProxy": true, "apifyProxyGroups": ["RESIDENTIAL"] }` | Required to bypass Google anti-bot |

---

## 4. Areas & Categories Matrix

### 16 Target Areas (with center coordinates)

| # | Area | Lat | Lng | Density |
|---|------|-----|-----|---------|
| 1 | Downtown Dubai | 25.1972 | 55.2744 | ⭐⭐⭐⭐⭐ |
| 2 | Business Bay | 25.1871 | 55.2617 | ⭐⭐⭐⭐ |
| 3 | Dubai Marina | 25.0787 | 55.1426 | ⭐⭐⭐⭐⭐ |
| 4 | Jumeirah Lake Towers (JLT) | 25.0688 | 55.1552 | ⭐⭐⭐⭐ |
| 5 | Al Barsha 1 | 25.1006 | 55.1999 | ⭐⭐⭐⭐ |
| 6 | Al Quoz Industrial Area | 25.1381 | 55.2303 | ⭐⭐⭐ |
| 7 | Al Karama | 25.2363 | 55.3013 | ⭐⭐⭐⭐⭐ |
| 8 | Bur Dubai | 25.2578 | 55.2893 | ⭐⭐⭐⭐⭐ |
| 9 | Jumeirah 1 | 25.2088 | 55.2621 | ⭐⭐⭐ |
| 10 | Jumeirah 2 | 25.1980 | 55.2391 | ⭐⭐⭐ |
| 11 | Jumeirah 3 | 25.1849 | 55.2152 | ⭐⭐⭐ |
| 12 | Dubai Silicon Oasis | 25.1172 | 55.3785 | ⭐⭐⭐ |
| 13 | Motor City | 25.0559 | 55.2427 | ⭐⭐ |
| 14 | Barsha Heights (Tecom) | 25.0890 | 55.1758 | ⭐⭐⭐ |
| 15 | Deira | 25.2697 | 55.3094 | ⭐⭐⭐⭐⭐ |
| 16 | Dubai Investment Park | 24.9919 | 55.1624 | ⭐⭐ |

### 4 Search Categories per Area

| Category Slug | Search Query Template | Maps To |
|--------------|----------------------|---------|
| `restaurant` | "restaurants in {area}" | Full-service restaurants |
| `fast_food` | "fast food in {area}" | QSR, takeaways |
| `cafe_bakery` | "cafes and bakeries in {area}" | Cafes, patisseries, bakeries |
| `delivery` | "food delivery kitchen in {area}" | Cloud kitchens, ghost kitchens |

**Total queries = 16 areas × 4 categories = 64 queries**

---

## 5. Cost Analysis (Budget: $5.00)

### Apify Pricing Model

```
Platform Credits  =  Memory (GB)  ×  Time (hours)  ×  $0.005 per CU
Proxy Data Cost   =  Data transferred (GB)  ×  $10.00 per GB (residential)
```

### Per-Run Estimate

| Item | Value | Cost |
|------|-------|------|
| Queries | 64 | — |
| Results per query | 40 | — |
| Gross results | 2,560 | — |
| Unique after dedup (est.) | ~1,800–2,200 | — |
| Actor memory | 4 GB | — |
| Scrape time per query | ~90 seconds | — |
| Total run time | ~96 minutes (1.6 hrs) | — |
| **Compute CUs** | 4 GB × 1.6 hrs = **6.4 CUs** | **$0.032** |
| **Proxy data** | 64 queries × ~1.5 MB/page × 40 pages = ~3.8 GB | **$3.80** |
| **Storage** | ~5 MB dataset | **$0.001** |
| **Total Estimated Cost** | | **~$3.83** |
| **Buffer remaining** | | **~$1.17** |

> ⚠️ **Proxy cost dominates.** If the run goes over estimate, the safest lever is
> reducing `maxCrawledPlacesPerSearch` from 40 → 25, which drops proxy spend to ~$2.40.

### Budget Scenarios

| Scenario | Queries | Results/Query | Est. Unique | Est. Cost |
|----------|---------|---------------|-------------|-----------|
| 🟢 Conservative (safe) | 48 | 25 | ~800 | ~$1.80 |
| 🟡 **Balanced (default)** | 64 | 40 | ~1,800 | ~$3.83 |
| 🔴 Aggressive (risky) | 64 | 60 | ~2,500 | ~$5.50 ⛔ |

**Recommended: Balanced — 64 queries × 40 results = ~$3.83 (leaves $1.17 buffer)**

### Time to Complete

```
64 queries × 90 seconds/query = ~96 minutes total run time
(Apify runs this in parallel — wall-clock time ~20–30 minutes)
```

---

## 6. Data Pipeline

```
[generate_input.py]
        │
        ▼  writes
[apify_input.json]  ──────────────────────┐
                                          │
[run_scraper.py]  ──── Apify API ────► Actor Run
        │                                 │
        │         polls for completion    │
        │◄────────────────────────────────┘
        │
        ▼  downloads raw dataset
[output/raw/dataset_YYYYMMDD.json]
        │
[process_results.py]
        ├── Deduplicate by placeId
        ├── Normalize fields
        ├── Map categoryName → internal type
        ├── Filter: UAE addresses only
        └── Write outputs
            ├── output/OUTPUT.csv
            └── output/OUTPUT.json
```

---

## 7. Output Schema

### CSV Columns

| Column | Source Field | Example |
|--------|-------------|---------|
| `place_id` | `placeId` | `ChIJN1t_tDeuEmsRUsoyG83frY4` |
| `Name` | `title` | `McDonald's Dubai Mall` |
| `Address` | `address` | `Dubai Mall, Downtown Dubai` |
| `Contact_No` | `phone` | `+971 4 341 0000` |
| `Website` | `website` | `https://mcdonalds.com/ae` |
| `Geo_Lat` | `location.lat` | `25.1972` |
| `Geo_Lng` | `location.lng` | `55.2744` |
| `Google_Maps_URL` | `url` | `https://maps.google.com/?cid=...` |
| `Category` | `categoryName` | `Fast food restaurant` |
| `Category_Type` | mapped | `QSR` / `Full-Service` / `Cafe` / `Bakery` / `Cloud Kitchen` |
| `Rating` | `totalScore` | `4.2` |
| `Review_Count` | `reviewsCount` | `1542` |
| `Area` | injected from query | `Downtown Dubai` |
| `Permanently_Closed` | `permanentlyClosed` | `false` |

### JSON Structure (per entry)

```json
{
  "place_id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
  "Name": "McDonald's Dubai Mall",
  "Address": "Dubai Mall, Downtown Dubai, Dubai, UAE",
  "Contact_No": "+971 4 341 0000",
  "Website": "https://www.mcdonalds.com/ae",
  "Geo_Coordinates": { "lat": 25.1972, "lng": 55.2744 },
  "Google_Maps_URL": "https://maps.google.com/?cid=...",
  "Category": "Fast food restaurant",
  "Category_Type": "QSR",
  "Rating": 4.2,
  "Review_Count": 1542,
  "Area": "Downtown Dubai",
  "Permanently_Closed": false
}
```

---

## 8. Category Type Mapping

The actor returns Google's native `categoryName` (e.g., "Fast food restaurant").
We map it to our internal types for the app:

| Google Category | Our `Category_Type` |
|----------------|---------------------|
| Restaurant, Indian restaurant, etc. | `Full-Service` |
| Fast food restaurant, Hamburger restaurant | `QSR` |
| Cafe, Coffee shop, Tea house | `Cafe` |
| Bakery, Patisserie, Dessert shop | `Bakery` |
| Meal delivery, Delivery restaurant | `Cloud Kitchen` |
| Ice cream shop, Juice bar | `Dessert & Drinks` |
| Bar, Pub | `Bar` |
| Other / Unknown | `Other` |

---

## 9. File Structure

```
apify/
├── PLAN.md                     ← this file
├── generate_input.py           ← builds apify_input.json from areas × categories
├── apify_input.json            ← actor input (auto-generated, safe to re-generate)
├── run_scraper.py              ← triggers Apify run, polls, downloads results
├── process_results.py          ← dedup, normalize, export CSV + JSON
├── requirements.txt
├── output/
│   ├── raw/                    ← raw Apify dataset downloads
│   ├── OUTPUT.csv
│   └── OUTPUT.json
└── test/
    ├── test_generate_input.py  ← verifies query generation
    ├── test_processor.py       ← verifies dedup + field mapping
    └── test_data/
        └── sample_raw.json     ← 5-record sample for unit tests
```

---

## 10. Pre-Run Checklist

- [ ] `APIFY_API_TOKEN` set in `../.env` (get from https://console.apify.com → Settings → Integrations)
- [ ] `pip install -r apify/requirements.txt`
- [ ] Run `python generate_input.py` → verify `apify_input.json` looks correct
- [ ] Check Apify account balance: https://console.apify.com/billing
- [ ] Run `python run_scraper.py --dry-run` to estimate cost without spending credits
- [ ] Run `python run_scraper.py` for the real run (~20–30 min wall-clock time)
- [ ] Run `python process_results.py` to clean and export

---

## 11. Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Proxy cost exceeds $5 | Use `--max-results 25` flag to halve proxy cost |
| Apify run times out | Actor auto-retries; checkpointing saves progress |
| Duplicate places across areas | Deduplicated by `placeId` in `process_results.py` |
| Places with no phone/website | Kept in output with empty string — still useful for leads |
| Google changes Maps structure | Actor is maintained by `compass` — updates tracked via Apify store |
| Places permanently closed | Filtered out in `process_results.py` unless `--include-closed` flag set |

---

## 12. Next Steps After Scrape

1. **Merge with Google Places enrichment** — use `scripts/fetch_places.py` to fill gaps  
2. **Load into Supabase** — for the full-stack app backend  
3. **Dedup against RESTRO_LIST.csv** — cross-reference existing known listings  
4. **Segment for outreach** — filter by `Category_Type`, `Rating < 4.0`, `Review_Count < 50`
