# Talabat UAE Scraping Pipeline — Complete Project Context

> Last updated: 2026-06-18  
> Owner: Sagar Singh  
> Purpose: UAE food aggregator market intelligence for data-driven client app

---

## 1. Project Goal

Build a scalable, automated data pipeline that scrapes **complete restaurant + menu data** from UAE food aggregators (starting with Talabat) and stores it in a structured database enabling:

- **Price change tracking** — detect when any item price/description changes between scrape runs
- **Menu delta analysis** — track items added, removed, repriced across time
- **Market intelligence exports** — management-ready CSV/Excel with restaurant classifications
- **Cross-platform entity resolution** — match the same physical restaurant across Talabat, Zomato, Deliveroo, etc.

Target platforms (in order of priority):
1. **Talabat** — primary (UAE's largest food delivery platform) ✅ DONE
2. Zomato UAE — next
3. Deliveroo UAE — next
4. Noon Food, HungerStation, Uber Eats UAE — future

---

## 2. Tech Stack

### Scraping Layer
| Tool | Purpose |
|------|---------|
| `curl_cffi` (impersonate=chrome124) | Bypass Cloudflare TLS fingerprinting |
| `httpx` / `aiohttp` | Async HTTP for concurrent area scanning |
| Hidden XHR/Fetch API | Talabat's internal REST API (reverse-engineered from DevTools) |
| `asyncio` + `asyncio.Semaphore` | Concurrent network I/O (up to 20 concurrent requests) |

### Anti-Bot / Proxy
| Tool | Purpose |
|------|---------|
| Oxylabs Residential Proxies | UAE geo-targeted IP rotation |
| Rotating proxy pool | Auto-rotation on 429/ban |
| Proxy exhaustion detection | `PROXY_EXHAUSTED` flag — triggers checkpoint save |

### Data & Storage
| Tool | Purpose |
|------|---------|
| `pandas` | Data cleaning, deduplication, export |
| `supabase-py` | REST API to PostgreSQL (Supabase cloud) |
| `openpyxl` | Styled Excel export with 2 sheets |
| `rapidfuzz` | Fuzzy name matching for entity resolution |
| JSON checkpoint | Resume interrupted scrape runs |

### Infrastructure
| Component | Detail |
|-----------|--------|
| Supabase project | `cbagujraolgpcjmnpplg` — region: `ap-southeast-2` (Sydney) |
| DB direct connect | IPv6-only host — **cannot connect from Windows** (IPv6 unreachable) |
| Supabase MCP | Used for direct SQL execution bypassing REST timeout limits |
| REST API (supabase-py) | Used for all scraping writes + paginated reads (1,000 rows/page) |

---

## 3. Architecture

```
[Talabat Hidden API]
        │
        ▼ curl_cffi + Oxylabs residential proxy
[talabat_menu_tracker.py]  ←── asyncio Semaphore(20)
  │  Async scrape loop per area
  │  • Area scan → restaurant list (branch_ids)
  │  • Per branch: restaurant metadata + menu categories + items
  │  • Hash comparison → detect changes
  │  • checkpoint.json → resume on interruption
        │
        ▼
[Supabase PostgreSQL]
  ├── talabat_restaurants    (15,773 rows)
  ├── talabat_menu_items     (386,079 rows)
  ├── talabat_menu_deltas    (price/description change log)
  └── scrape_runs            (per-run stats + timing)
        │
        ▼
[export_to_csv.py]
  └── CSV (153 MB) + Excel (47 MB)
      data/exports/talabat_full_YYYYMMDD_HHMMSS.*
```

---

## 4. Supabase Database Schema

### `talabat_restaurants`
| Column | Type | Source | Status |
|--------|------|--------|--------|
| `branch_id` | bigint PK | Scraped | ✅ |
| `restaurant_id` | bigint | Scraped | ✅ |
| `restaurant_name` | text NOT NULL | Scraped | ✅ |
| `ld_name` | text | JSON-LD / Excel | ✅ |
| `restaurant_type` | text | Scraped ("Restaurant") + Excel (FSR/QSR/Cafe...) | ✅ partial |
| `outlet_type` | text | TYPE_CHAIN_DATA.xlsx (Independent/Chain) | ✅ 133 rows |
| `chained_outlet_type` | text | TYPE_CHAIN_DATA.xlsx (Local/MNC/N/A) | ✅ 133 rows |
| `std_terms` | text | Future (Tavily/EXA + GPT classification) | ❌ empty |
| `contact` | text | Future (scrape restaurant detail page) | ❌ empty |
| `map_url` | text | Scraped | ✅ |
| `serves_cuisine` | text[] | Scraped | ✅ |
| `key_cuisines` | text[] | Excel (KEY CUISINES column) | ✅ 5,275 rows |
| `ld_lat` / `ld_lon` | numeric | Scraped / Excel | ✅ |
| `area_id` / `area_name` | int/text | Scraped | ✅ |
| `first_scraped_at` | timestamptz | Auto | ✅ |
| `updated_at` | timestamptz | Auto | ✅ |

### `talabat_menu_items`
| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | Auto |
| `branch_id` | bigint | FK → talabat_restaurants |
| `item_name` | text | Menu item name |
| `menu_category` | text | Category bucket |
| `price_aed` | numeric | Price in AED |
| `description` | text | Item description |
| `scraped_at` | timestamptz | When scraped |
| `price_hash` | text | SHA-256 of price snapshot |

### `talabat_menu_deltas`
Logs every detected change between scrape runs:
- `change_type`: price_changed | item_added | item_removed | desc_changed | cat_changed
- `old_value` / `new_value`: what changed
- `detected_at`: timestamp

### `scrape_runs`
One row per `python talabat_menu_tracker.py --all` execution:
- `run_id`: `YYYYMMDD_HHMMSS`
- `restaurants_scraped`, `items_total`, `items_added`, `items_price_changed`, etc.
- `started_at` / `completed_at`

---

## 5. Key Files

```
talabat/
├── .env                          # Supabase URL, keys, Oxylabs credentials — NEVER PUSH
├── Data_menus.xlsx               # 5,275 restaurant master list with KEY CUISINES
├── TYPE_CHAIN_DATA.xlsx          # Classification data (2 sheets)
│   ├── only_type  (197 rows)     # branch_id + Type of Restaurants
│   └── Both       (133 rows)     # URL + Type + Outlet Type + Chained Outlet Type
├── ENTITY_RESOLUTION_STRATEGY.md # Geo + fuzzy matching methodology doc
├── PROJECT_CONTEXT.md            # This file
│
├── menu/
│   ├── talabat_menu_tracker.py   # MAIN SCRAPER — async pipeline
│   ├── export_to_csv.py          # Export Supabase → CSV + Excel
│   ├── seed_from_excel.py        # Backfill restaurant_type + key_cuisines from Excel
│   ├── entity_resolution.py      # Map TYPE_CHAIN_DATA → branch_ids + write to DB
│   ├── check_progress.py         # Quick scrape progress check
│   ├── test_proxy.py             # Proxy health check
│   │
│   └── data/
│       ├── exports/              # talabat_full_YYYYMMDD.csv / .xlsx
│       └── entity_resolution/   # matched_results_YYYYMMDD.csv / .json
│
└── sanitization/
    └── async_sanitize.py         # Emoji/whitespace sanitization for DB columns
```

---

## 6. Scraping Pipeline Details

### Main Scraper: `talabat_menu_tracker.py`

**How it works:**
1. Loads all UAE areas from Talabat's hidden area API
2. For each area → fetches restaurant listing (paginated)
3. For each restaurant → fetches menu (categories + items)
4. Compares against previous scrape using SHA-256 price hash
5. If changed → writes delta to `talabat_menu_deltas`
6. Writes/updates restaurant + items to Supabase via REST

**Key commands:**
```bash
# Full scrape of all restaurants
python talabat_menu_tracker.py --all

# Resume interrupted run (checkpoint.json tracks progress)
python talabat_menu_tracker.py --all   # auto-resumes from checkpoint

# Clear checkpoint to start fresh
del talabat\menu\checkpoint.json

# Single area test
python talabat_menu_tracker.py --area-id 12345
```

**Checkpoint logic:**
- `checkpoint.json` records all `branch_id`s already processed
- On full successful run → checkpoint auto-deleted (next `--all` starts fresh)
- On proxy exhaustion → checkpoint saved (resume next time)

**Proxy rotation:**
- Oxylabs residential proxies via `OXYLABS_USER` + `OXYLABS_PASS` in `.env`
- UAE geo-targeted endpoint
- Auto-rotates on 429 / connection error
- `PROXY_EXHAUSTED = True` → triggers emergency checkpoint save

---

## 7. Classification System

### Restaurant Type (restaurant_type)
Source: `TYPE_CHAIN_DATA.xlsx` (only_type + Both sheets) + future Tavily/GPT enrichment

| Value | Description | Count in DB |
|-------|-------------|-------------|
| Full-Service Restaurants | Sit-down, waiter service, full menu | ~99 |
| Quick-Service Restaurants | Counter service, fast food | ~60 |
| Cafes | Coffee-first, light bites | ~35 |
| Bakery | Bread/pastry focused | (from Both sheet) |
| Cloud Kitchen | Delivery-only, no dine-in | ~3 |
| Restaurant | Generic placeholder from Talabat API | ~15,576 |

### Outlet Type (outlet_type)
| Value | Meaning |
|-------|---------|
| Independent | Single-location, owner-operated |
| Chain | Multiple locations |

### Chained Outlet Type (chained_outlet_type)
| Value | Meaning |
|-------|---------|
| Local Chain | UAE/GCC-based chain brand |
| MNC Chain | Multinational chain (McDonald's, KFC, Starbucks, etc.) |
| NULL | Independent (no chain type applicable) |

---

## 8. Entity Resolution Strategy

For matching TYPE_CHAIN_DATA rows to `talabat_restaurants`:

| Priority | Method | Confidence | Coverage |
|----------|--------|-----------|----------|
| 1 | URL branch_id extraction (`/restaurant/748535/`) | 100% | All 133 Both-sheet rows |
| 2 | Geo ≤ 30m + fuzzy name ≥ 0.85 | 99% | Fallback |
| 3 | Geo ≤ 100m + fuzzy name ≥ 0.90 | 95% | Fallback |
| 4 | Direct branch_id (only_type sheet) | 100% | 197 rows |

For future cross-platform matching (Talabat ↔ Zomato ↔ Deliveroo):
- Round lat/lon to 3 decimal places → geo_key (`25.205_55.274`)
- Same geo_key + name fuzzy ≥ 0.85 → same physical branch
- Full strategy documented in `ENTITY_RESOLUTION_STRATEGY.md`

---

## 9. Export Schema (Management Format)

Output columns for CSV/Excel exports:

| Column | DB Source | Notes |
|--------|-----------|-------|
| branch_id | talabat_restaurants | Talabat branch identifier |
| restaurant_id | talabat_restaurants | Talabat chain identifier |
| RESTRO NAME | restaurant_name | Display name |
| ld_name | ld_name | JSON-LD canonical name |
| Type of Restaurants | restaurant_type | FSR / QSR / Cafe / Bakery / Cloud Kitchen |
| map_url | map_url | Google Maps link |
| serves_cuisine | serves_cuisine | All cuisines (array → CSV string) |
| ld_lat / ld_lon | ld_lat / ld_lon | GPS coordinates |
| area_id / area_name | area_id / area_name | Talabat delivery zone |
| KEY CUISINES | key_cuisines | Primary cuisine tags |
| Menu category | menu_category | Menu section |
| Menu item(name) | item_name | Dish name |
| Price | price_aed | Price in AED |
| DESCRIPTION | description | Item description |
| Outlet Type | outlet_type | Independent / Chain |
| Chained Outlet Type | chained_outlet_type | Local / MNC / NULL |
| Std terms | std_terms | ❌ Future — standardized cuisine taxonomy |
| INGREDIENTS | — | ❌ Future — GPT-4o-mini extraction from description |
| contact | contact | ❌ Future — scrape restaurant detail page |
| scraped_at | scraped_at | UTC timestamp |

**Run export:**
```bash
python talabat\menu\export_to_csv.py --format both
# Outputs to: talabat/menu/data/exports/talabat_full_YYYYMMDD_HHMMSS.{csv,xlsx}
```

---

## 10. Challenges & Solutions

### 10.1 IPv6-only Supabase Direct Connection
**Problem:** `db.cbagujraolgpcjmnpplg.supabase.co` resolves only to an AAAA record. Windows cannot route IPv6 → asyncpg/psycopg2 direct connections fail with `WinError 1231`.

**Solution:** All DB operations go through:
- **Supabase REST API** (supabase-py) — for scraper writes and paginated reads
- **Supabase MCP `execute_sql`** — for bulk SQL operations without timeout

### 10.2 REST Statement Timeout (57014)
**Problem:** PostgREST enforces an 8-second SELECT timeout. Sanitization UPDATE on 386K rows via REST killed every query.

**Solution:** Supabase MCP `execute_sql` runs direct SQL with no statement timeout. Used for all bulk sanitization operations.

### 10.3 Emoji / Mojibake Sanitization
**Problem:** Emoji stored as garbled Latin-1 bytes (mojibake) and raw Unicode emoji in `item_name` and `description` columns. `talabat_menu_items`: 2,145 dirty rows; `talabat_menu_deltas`: 17,214 dirty rows.

**Solution:** PostgreSQL regex via MCP execute_sql:
```sql
UPDATE talabat_menu_items
SET item_name = regexp_replace(item_name, '[^ -~؀-ۿ\t\n]+', '', 'g')
WHERE item_name ~ '[^ -~؀-ۿ\t\n]';
```
Pattern allows: ASCII printable (U+0020–U+007E) + Arabic block (U+0600–U+06FF). Two passes needed for residual mojibake rows.

### 10.4 Checkpoint Saturation Bug
**Problem:** `scrape_runs` table showed all-zeros + `completed_at: null` after a run that processed no restaurants (checkpoint was fully saturated → `all_jobs = []`).

**Two bugs fixed in `talabat_menu_tracker.py`:**
1. Empty jobs early-exit now calls `fn_complete_scrape_run` RPC before returning
2. Successful full run (no proxy exhaustion) auto-deletes `checkpoint.json` so next `--all` starts fresh

### 10.5 Windows UTF-8 Console Crash
**Problem:** `UnicodeEncodeError: 'charmap' codec can't encode character '→'` — the `→` arrow in print statements can't encode to Windows cp1252 console.

**Solution:** Added at top of all scripts:
```python
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
```

### 10.6 Upsert vs. UPDATE (NOT NULL Constraint)
**Problem:** `seed_from_excel.py` used Supabase upsert (INSERT ON CONFLICT DO UPDATE). Branch_ids in Excel that weren't in DB caused INSERT attempts without `restaurant_name`, violating NOT NULL constraint. All 11 batches failed.

**Solution:** Switched to individual concurrent `.update().eq("branch_id", bid).execute()` calls with `ThreadPoolExecutor(max_workers=20)`. Updates only existing rows. 5,275 rows in 100 seconds.

### 10.7 outlet_type Seeded with Wrong Values
**Problem:** Initial `seed_from_excel.py` derived `outlet_type` as "FSR"/"QSR"/"Cafe" (short form of restaurant_type). User's actual schema defines `outlet_type` as "Independent"/"Chain".

**Solution:** `entity_resolution.py` now correctly sets `outlet_type` to "Independent"/"Chain" from `TYPE_CHAIN_DATA.xlsx Both` sheet and passes `None` for outlet_type on `only_type`-only rows (clearing wrong values).

---

## 11. Data Stats (as of 2026-06-18)

| Metric | Count |
|--------|-------|
| Total restaurants in DB | 15,773 |
| Restaurants with menu items scraped | 5,043 |
| Shell restaurants (no menu yet) | 10,730 |
| Total menu items | 386,079 |
| Restaurants with key_cuisines | 5,275 |
| Restaurants with classified restaurant_type | 197 |
| Restaurants with outlet_type | 133 |
| Latest CSV export size | 153.6 MB |
| Latest Excel export size | 46.8 MB |

---

## 12. What Is Still Left / Roadmap

### Immediate (Data Enrichment)
| Task | How | Priority |
|------|-----|----------|
| Classify remaining ~15,576 "Restaurant" type rows | Tavily/EXA API search + GPT-4o-mini classification | HIGH |
| Fill `outlet_type` for ~15,640 rows | Same classification pipeline | HIGH |
| Fill `chained_outlet_type` for ~15,640 rows | Same classification pipeline | HIGH |
| Scrape `contact` field | Scrape Talabat restaurant detail page | MEDIUM |
| Fill `std_terms` | Standardized cuisine taxonomy from key_cuisines | MEDIUM |
| Fill `INGREDIENTS` | GPT-4o-mini extraction from item description | LOW |

### Classification Pipeline (Future — Tavily/EXA + GPT)
```
For each restaurant without classification:
  1. Search Tavily API / EXA for restaurant name + UAE
  2. Extract: type (FSR/QSR/Cafe), chain status, brand info
  3. GPT-4o-mini: "Given this restaurant info, classify as FSR/QSR/Cafe/Bakery/Cloud Kitchen"
  4. Write back to talabat_restaurants
```

### Scraping (Next Platforms)
| Platform | Status | Blocker |
|----------|--------|---------|
| Talabat UAE | ✅ 5,043 branches with menus | Need to scrape remaining ~10,730 shells |
| Zomato UAE | ❌ Not started | GraphQL API, session cookies needed |
| Deliveroo UAE | ❌ Not started | JWT auth needed |
| Noon Food | ❌ Not started | x-noon-auth header pattern |
| HungerStation | ❌ Not started | Cloudflare bypass needed |
| Uber Eats UAE | ❌ Not started | Heavy GraphQL, strong bot detection |
| Google Maps | ❌ Not started | Apify actor ready to configure |

### Infrastructure
- Set up cron job / N8N for weekly re-scrape of Talabat
- Cross-platform entity resolution table (`restaurant_entities`) in Supabase
- Price change dashboard / alerts

---

## 13. Environment Setup

### `.env` file (talabat/.env — NEVER PUSH)
```
SUPABASE_URL=https://cbagujraolgpcjmnpplg.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_PUBLISHABLE_KEY=eyJ...
OXYLABS_USER=...
OXYLABS_PASS=...
```

### Install dependencies
```bash
pip install supabase pandas openpyxl rapidfuzz python-dotenv curl_cffi httpx
```

### Quick start
```bash
# Scrape all restaurants
python talabat/menu/talabat_menu_tracker.py --all

# Export to CSV + Excel
python talabat/menu/export_to_csv.py --format both

# Run entity resolution (classify outlet type etc.)
python talabat/menu/entity_resolution.py --write-db

# Seed key_cuisines from Excel
python talabat/menu/seed_from_excel.py
```

---

## 14. Security Rules

- **NEVER push** to GitHub: `.env`, `*.json`, `*.jsonl`, `*.csv`, `*.xlsx`, `*.md` (except README.md)
- **NEVER commit** credentials or API keys
- Oxylabs credentials in `talabat/.env` only
- Git history was rewritten with `git-filter-repo` after a credential leak (force-push to clean history)
- Checkpoint files (`checkpoint.json`) contain only branch_ids — safe but still gitignored

---

*Document maintained by Claude Code (claude-sonnet-4-6) | Updated: 2026-06-18*
