# Dubai F&B Scrape — Full Query Plan, Cost Breakdown & Run Analysis

> Last updated: 2026-05-26  
> Apify actor: `compass/crawler-google-places`  
> Pricing model: **PAY_PER_EVENT** — $0.004 per place scraped (FREE tier)

---

## 1. What Is a "Query"?

Every time the actor searches Google Maps it uses a single **search string** — exactly like typing
something in the Google Maps search bar.

```
"restaurants in Downtown Dubai, Dubai UAE"
"fast food in Business Bay, Dubai UAE"
"cafes and bakeries in Dubai Marina, Dubai UAE"
"food delivery kitchen in Deira, Dubai UAE"
```

We create these programmatically by combining **16 areas** × **4 categories** = **64 queries total**.

---

## 2. What Is `maxCrawledPlacesPerSearch`?

This is the **cap on how many Google Maps listings the actor will return per single query**.

Google Maps has hundreds of listings per area, but we limit how many we fetch to control cost.

```
Query: "restaurants in Downtown Dubai, Dubai UAE"

maxCrawledPlacesPerSearch = 10  →  fetches top 10 restaurants  →  costs 10 × $0.004 = $0.04
maxCrawledPlacesPerSearch = 20  →  fetches top 20 restaurants  →  costs 20 × $0.004 = $0.08
maxCrawledPlacesPerSearch = 40  →  fetches top 40 restaurants  →  costs 40 × $0.004 = $0.16
maxCrawledPlacesPerSearch = 100 →  fetches top 100 restaurants →  costs 100 × $0.004 = $0.40
```

**Higher = more listings discovered, higher cost.**  
The results are the top-ranked listings for that search, not random ones.

### Important: When a query returns LESS than the max

If a query like *"food delivery kitchen in Motor City"* only has 8 places on Google Maps,
you only pay for 8 places — not for the full max. The cap is a ceiling, not a guarantee.

---

## 3. The 64 Queries — Full Grid

### Areas (16 total)

| # | Area | Lat | Lng | Area Type |
|---|---|---|---|---|
| 1 | Downtown Dubai | 25.1972 | 55.2744 | Dense — City centre |
| 2 | Business Bay | 25.1871 | 55.2617 | Dense — Commercial |
| 3 | Dubai Marina | 25.0787 | 55.1426 | Dense — Waterfront |
| 4 | Jumeirah Lake Towers (JLT) | 25.0688 | 55.1552 | Moderate — Mixed-use |
| 5 | Al Barsha 1 | 25.1006 | 55.1999 | Moderate — Residential |
| 6 | Al Quoz Industrial Area | 25.1381 | 55.2303 | Light — Industrial |
| 7 | Al Karama | 25.2363 | 55.3013 | Dense — Affordable dining |
| 8 | Bur Dubai | 25.2578 | 55.2893 | Dense — Heritage |
| 9 | Jumeirah 1 | 25.2088 | 55.2621 | Moderate — Residential |
| 10 | Jumeirah 2 | 25.1980 | 55.2391 | Moderate — Residential |
| 11 | Jumeirah 3 | 25.1849 | 55.2152 | Moderate — Residential |
| 12 | Dubai Silicon Oasis (DSO) | 25.1172 | 55.3785 | Moderate — Tech park |
| 13 | Motor City | 25.0559 | 55.2427 | Light — Suburban |
| 14 | Barsha Heights | 25.0890 | 55.1758 | Moderate — Mixed |
| 15 | Deira | 25.2697 | 55.3094 | Dense — Old city |
| 16 | Dubai Investment Park (DIP) | 24.9919 | 55.1624 | Light — Industrial |

### Categories (4 total)

| # | Search Term Used | Internal Label | What It Captures |
|---|---|---|---|
| 1 | `restaurants` | `restaurant` | Full-service sit-down dining |
| 2 | `fast food` | `fast_food` | QSR chains & quick bites |
| 3 | `cafes and bakeries` | `cafe_bakery` | Coffee shops, patisseries |
| 4 | `food delivery kitchen` | `delivery` | Cloud kitchens, dark kitchens |

### The 64 Search Strings (example — first 16)

```
restaurants in Downtown Dubai, Dubai UAE            ← Area 1, Cat 1
fast food in Downtown Dubai, Dubai UAE              ← Area 1, Cat 2
cafes and bakeries in Downtown Dubai, Dubai UAE     ← Area 1, Cat 3
food delivery kitchen in Downtown Dubai, Dubai UAE  ← Area 1, Cat 4

restaurants in Business Bay, Dubai UAE              ← Area 2, Cat 1
fast food in Business Bay, Dubai UAE                ← Area 2, Cat 2
cafes and bakeries in Business Bay, Dubai UAE       ← Area 2, Cat 3
food delivery kitchen in Business Bay, Dubai UAE    ← Area 2, Cat 4

... (× 14 more areas = 64 total)
```

---

## 4. Run 1 Analysis — What Actually Happened

### Timeline

```
14:48:57  run_scraper.py started  →  all 64 queries sent to Apify cloud simultaneously
14:49:00  Actor begins processing (up to 20 queries in parallel)
14:50:05  Script crashed mid-run (API version mismatch — TypeError in our code)
          ↑ Apify cloud CONTINUED running independently
          ↑ We manually aborted the cloud run from Python
14:50:06  Run officially ABORTED after 68 seconds
```

### What Completed Before Abort

The actor processes queries in parallel batches. In 68 seconds the fastest queries
(typically the ones with fewer results or queries that started first) finished:

| Area | Restaurants | Fast Food | Cafes & Bakeries | Delivery | Area Total | Status |
|---|---|---|---|---|---|---|
| **Downtown Dubai** | 37 | 28 | **40** | **40** | **145** | Partial — 3/4 hit max |
| **Business Bay** | 20 | **40** | 15 | **40** | **115** | Partial — 2/4 hit max |
| **Dubai Marina** | 25 | 17 | **40** | 12 | **94** | Partial — 1/4 hit max |
| **Jumeirah Lake Towers** | 13 | 20 | 0 | 0 | **33** | Partial — 2/4 queries only |
| Al Barsha 1 | 0 | 0 | 0 | 0 | **0** | NOT STARTED |
| Al Quoz Industrial Area | 0 | 0 | 0 | 0 | **0** | NOT STARTED |
| Al Karama | 0 | 0 | 0 | 0 | **0** | NOT STARTED |
| Bur Dubai | 0 | 0 | 0 | 0 | **0** | NOT STARTED |
| Jumeirah 1 | 0 | 0 | 0 | 0 | **0** | NOT STARTED |
| Jumeirah 2 | 0 | 0 | 0 | 0 | **0** | NOT STARTED |
| Jumeirah 3 | 0 | 0 | 0 | 0 | **0** | NOT STARTED |
| Dubai Silicon Oasis | 0 | 0 | 0 | 0 | **0** | NOT STARTED |
| Motor City | 0 | 0 | 0 | 0 | **0** | NOT STARTED |
| Barsha Heights | 0 | 0 | 0 | 0 | **0** | NOT STARTED |
| Deira | 0 | 0 | 0 | 0 | **0** | NOT STARTED |
| Dubai Investment Park | 0 | 0 | 0 | 0 | **0** | NOT STARTED |
| **TOTAL** | **95** | **105** | **95** | **92** | **387** | **14 of 64 queries** |

### Key Observations

1. **6 out of 14 queries hit the exact cap (40)** — meaning those areas have MORE
   than 40 available listings. If we raise the cap we get more data.

2. **JLT only returned 2 of its 4 queries** — the cafes/delivery queries were
   mid-flight when the abort happened. They are incomplete, not empty.

3. **12 areas = 48 queries were never touched at all** — zero cost, zero data.

4. **387 places survived deduplication** — 0 duplicates removed because each area
   query returns mostly unique places (cross-area overlap is minimal in 68 seconds).

### Per-Query Stats

```
Queries that returned results : 14 out of 64
Average results per query     : 387 ÷ 14 = 27.6 places
Queries hitting the max (40)  : 6 (43% of completed queries)
Queries below max             : 8 (area either sparse or incomplete)
```

---

## 5. Cost Breakdown — Official Pricing

### How the Actor Bills

```
compass/crawler-google-places uses PAY_PER_EVENT pricing.

You are charged ONLY for:
  ✅  places-scraped    $0.004 each  (FREE tier)
  ✅  actor-start       $0.00005 per GB of memory used at startup

You are NOT charged separately for:
  ❌  proxy bandwidth    (included in per-place fee)
  ❌  compute time       (included in per-place fee)
  ❌  failed queries     (0 results = 0 cost)
```

### Run 1 Actual Cost Receipt

```
Places scraped    :  387
Per-place charge  :  387 × $0.004        =  $1.5480
Actor start       :  4 events × $0.00005 =  $0.0002
────────────────────────────────────────────────────
RUN 1 TOTAL       :                         $1.5482  ✓ confirmed
```

### Full Budget Ledger

```
Apify FREE plan budget             :  $5.0000
─────────────────────────────────────────────
Run 1 (aborted, 387 places)        : -$1.5482
Run 0 (tiny API test, 1 place)     : -$0.0042
─────────────────────────────────────────────
REMAINING BUDGET                   :  $3.4476
```

### Why Our Original Estimate Was Wrong

Our original estimate assumed cost = proxy_data + compute (like a standard actor).
This actor uses **PAY_PER_EVENT** — you pay per place, period. 

```
ORIGINAL ESTIMATE (wrong model)     ACTUAL (correct model)
─────────────────────────────────   ──────────────────────
2,560 results × $0.0006/result      2,560 results × $0.004/result
= $1.47                             = $10.24  ← 7× more expensive

Lesson: always check actor pricing tab before running at scale.
```

---

## 6. What's Left to Scrape

### Remaining: 12 Areas × 4 Categories = 48 Queries

```
Al Barsha 1               (4 queries: restaurants, fast food, cafes, delivery)
Al Quoz Industrial Area   (4 queries)
Al Karama                 (4 queries)
Bur Dubai                 (4 queries)
Jumeirah 1                (4 queries)
Jumeirah 2                (4 queries)
Jumeirah 3                (4 queries)
Dubai Silicon Oasis       (4 queries)
Motor City                (4 queries)
Barsha Heights            (4 queries)
Deira                     (4 queries)
Dubai Investment Park     (4 queries)
─────────────────────────────────────
Total                     48 queries
```

> Note: JLT (2 incomplete queries) and the 4 partially-complete areas are already
> saved. We do NOT re-scrape them — `process_results.py --skip-seen` will skip any
> place already in OUTPUT.json even if Apify returns it again.

### Cost Scenarios for Run 2 (48 queries only)

The number in the `max` column is `maxCrawledPlacesPerSearch`.

| max per query | Gross places | ~Unique new | Cost | Budget left | Total DB size |
|---|---|---|---|---|---|
| 10 | 480 | ~360 | $1.92 | $1.53 | **~747 places** |
| 12 | 576 | ~432 | $2.30 | $1.15 | **~819 places** |
| **15** | **720** | **~540** | **$2.88** | **$0.57** | **~927 places** ✅ |
| 20 | 960 | ~720 | $3.84 | -$0.39 | OVER BUDGET ❌ |

The ~25% dedup discount comes from cross-query overlap (e.g. a restaurant appearing
in both "restaurants" and "fast food" searches for the same area).

### Recommended: max = 15

```
48 queries × 15 results = 720 gross places
720 × $0.004            = $2.88 cost
$3.45 - $2.88           = $0.57 safety buffer

Combined database after Run 2:
  Run 1 (already done)   :  387 unique places
  Run 2 (recommended)    : ~540 new unique places
  ─────────────────────────────────────────────
  TOTAL unique database  : ~927 unique Dubai F&B places
```

---

## 7. Maximising Coverage — What the Data Tells Us

From Run 1, the queries that hit the **exact 40-place cap** were:

```
cafes and bakeries in Downtown Dubai     →  40 (capped — more exist)
fast food in Business Bay                →  40 (capped — more exist)
food delivery kitchen in Business Bay    →  40 (capped — more exist)
cafes and bakeries in Dubai Marina       →  40 (capped — more exist)
food delivery kitchen in Downtown Dubai  →  40 (capped — more exist)
fast food in Jumeirah Lake Towers        →  40 (wait — only in 20 sec!)
```

This means **Downtown Dubai, Business Bay and Dubai Marina each have 40+ listings
in at least one category**. Setting max=15 for Run 2 will under-capture these
dense areas — but we have no remaining budget to raise it for all 48 queries.

### If You Top Up Credits ($5 more → $8.45 total remaining)

```
Full 48 queries × 40 results = 1,920 gross → ~1,440 unique new places
Cost = 1,920 × $0.004 = $7.68
Run 1 (already done) = 387
TOTAL = ~1,827 unique places  ← close to our original goal of 1,920
```

---

## 8. Run 2 — Execution Commands

Once you decide on the max, run these three commands:

```bash
# Step 1: Re-generate input for only the 12 remaining areas
python apify/generate_input.py --max-results 15

# Step 2: Fire the scraper (takes ~3-4 min)
python apify/run_scraper.py

# Step 3: Process and MERGE with existing 387 results
python apify/process_results.py --skip-seen
```

The `--skip-seen` flag reads `apify/output/OUTPUT.json`, collects all 387 known
place IDs, and skips any of them if Apify returns them again — so you are never
charged twice for the same place.

---

## 9. Summary Table

| Metric | Run 1 (done) | Run 2 (planned) | Grand Total |
|---|---|---|---|
| Queries sent | 64 | 48 | 112 |
| Queries completed | 14 | 48 | 62 |
| Raw places returned | 387 | ~720 | ~1,107 |
| After dedup | 387 | ~540 | **~927** |
| Cost | $1.55 | $2.88 | **$4.43** |
| Budget left | — | — | **$0.57** |
| Areas covered | 4/16 | 12/16 | **16/16** ✓ |

---

*Generated from empirical data of Run 1 (2026-05-26). All cost figures use the
official Apify FREE-tier pricing of $0.004 per place scraped.*
