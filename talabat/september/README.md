# September Discovery Cycle

Finds restaurants added to Talabat since our last crawl, and — critically —
separates **genuinely new listings** from **listings we previously missed**.

## The question this exists to answer

June collected 15,198 restaurants. August found another 7,129. Management asked
whether Talabat gained 7,129 restaurants in two months or whether our June crawl
missed them. Nothing in the data answered it: every timestamp we store is our own
`scraped_at` — when *we* looked, never when *they* listed.

Applied retroactively to August, the answer is **851 newly listed (11.7%)** and
**6,410 newly found (88.3%)**. The market did not grow by 7,129; our coverage
did. This folder makes that split a first-class output so the number handed to
management is unambiguous from the start.

## Run order

```bash
python build_universe.py      # 1  assemble every branch_id ever evaluated
python probe_zones.py         # 2  probe all zones, snapshot, diff vs last month

#    3  crawl the changed zones, seeded from step 1
cd ../listing_scraper
python run2_collector.py --tier 100

cd ../september
python detect_relistings.py --new <crawl output>   # 4  same venue, new id?
python split_new_listings.py                       # 5  newly LISTED vs FOUND
```

## What each step does

**`build_universe.py`** — the crawler's dedup seed.

Seeds from **everything crawled (35,220)**, not everything shipped (22,327). The
12,893 difference is listings we found and rejected — groceries, pharmacies,
empty menus. Seeding from the exports alone would rediscover all of them and pay
the identification and classification cost again for a known answer.

One carve-out: the 36 `failed_urls` entries never *fetched*, so they were never
judged. They are removed from the seed and offered to the crawler again.

**`probe_zones.py`** — one request per zone returns its `totalVendors`, so a zone
whose total has not moved can be skipped. Writes a **dated** snapshot;
`listing_scraper/map_areas.py` writes a fixed `area_map.json` and would overwrite
the only baseline we hold (2026-08-10, 572 zones).

> `totalVendors` is a change **detector**, not a change **count**. It is net — a
> zone gaining 5 and losing 5 reads as zero — and it cannot be summed across
> zones, because delivery zones overlap and one restaurant appears in many.

**`detect_relistings.py`** — `branch_id` novelty is not venue novelty. Talabat
re-registers an existing venue under a fresh `branch_id` on re-onboarding or
ownership change: same place, same name, new id.

Each new listing is matched against the known universe with the cascading ladder
from `listing_comparison/match_strategies.py`, **imported rather than copied** so
the two can never drift:

| rule | confidence |
|---|---|
| `slug_exact` + coords ≤100 m | 0.99 |
| `restaurant_id` + coords ≤30 m | 0.98 |
| `name_exact` + coords ≤50 m | 0.95 |
| `name_exact` + coords ≤250 m | 0.85 |
| `fuzzy_name ≥90` + coords ≤150 m + cuisine | 0.80 |
| `slug_exact` + far | 0.25 — a different branch, not a re-listing |

Every rule pairs identity with **distance**, deliberately. `restaurant_id` names
a *chain*, not a venue — KFC carries one across 258 branches — and the URL slug
is brand-based too. An unguarded slug rule once matched an Abu Dhabi venue to one
155 km away in RAK at 0.99 confidence.

**`split_new_listings.py`** — the newly-listed / newly-found split.

`branch_id` is assigned sequentially, so an id above every id we have ever seen
cannot have existed when we last crawled. One inside our existing range existed
already and our earlier sweep did not reach it.

## Why there is no Talabat timestamp

Measured, not assumed:

- **HTTP carries none.** `cache-control: private, no-cache, no-store` and
  `cf-cache-status: DYNAMIC` — no `Last-Modified`, no `ETag`.
- **`__NEXT_DATA__` exposes `restaurant.createdAt`, but it is not a date.**
  Across 33 sampled restaurants it decreases *strictly* as `branch_id` rises, so
  it is derived from `branch_id` and carries no independent information.

That second measurement is what produced the working method: it is evidence that
`branch_id` is sequential.

## Honest limit

The split rests on `branch_id` being assigned sequentially, which the `createdAt`
relationship strongly implies but does not prove — Talabat could have back-filled
ids at some point. Report it as a well-evidenced estimate, not a certified count.

## What changes for October

`config.py` — set `MONTH` and `PREV_MONTH`. Everything else recomputes.
