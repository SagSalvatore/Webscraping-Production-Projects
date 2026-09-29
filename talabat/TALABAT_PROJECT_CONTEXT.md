# Talabat UAE — Full Project Context
**Owner:** Sagar Singh | **Org:** Mordor Intelligence  
**Supabase Project:** `cbagujraolgpcjmnpplg` | Region: `ap-southeast-2` (Sydney)  
**Last Updated:** 2026-06-18

---

## 1. Goal

Build a **data-driven market intelligence platform** for UAE food aggregators targeting Talabat, Zomato UAE, Deliveroo, Noon Food, HungerStation, Uber Eats, and Google Maps. The system must:

- Scrape complete restaurant listings: menu items, prices, categories, geo-coordinates, timestamps
- Track **price and menu changes over time** via delta comparison
- Export clean datasets for management reporting (CSV / Excel)
- Scale to all UAE platforms via a unified `fact_restaurants` cross-platform bridge table

---

## 2. Tech Stack

| Layer | Tools / Libraries |
|---|---|
| HTTP scraping | `curl_cffi` (TLS fingerprint spoof), `httpx`, `requests` |
| Browser automation | `playwright`, `camoufox`, `undetected-chromedriver` |
| HTML parsing | `BeautifulSoup4`, `lxml` |
| Crawl framework | `Scrapy` + `scrapy-playwright` |
| Concurrency | `asyncio` + `httpx`, `concurrent.futures.ThreadPoolExecutor` |
| Proxy | Oxylabs Residential Proxies (UAE geo-targeted) |
| Bot bypass | TLS impersonation (`chrome124`), rotating IPs, header mimicry |
| Hidden API | `__NEXT_DATA__` JSON extraction from Talabat Next.js pages |
| Data storage | `pandas`, Supabase (PostgreSQL via REST + MCP) |
| Exports | `openpyxl`, CSV with `utf-8-sig` BOM encoding |
| AI enrichment | OpenAI GPT-4o-mini (planned: ingredient extraction) |
| Google Maps | Apify actor `compass/crawler-google-places` |
| Secrets | `python-dotenv`, `.env` file (never committed) |

---

## 3. Project Evolution — Phase by Phase

### Phase 1 — Initial Selenium Scraper (Kuwait)
**File:** `talabat/talabat-selenium/talabat_Kuwait.py`  
First attempt: Selenium with undetected-chromedriver scraping Talabat Kuwait listings. Worked but slow and brittle for scale.

### Phase 2 — Scrapy Listing Scraper (UAE)
**Files:** `talabat/listing_scraper/`, `talabat/url_collector/`  
Scrapy spider targeting UAE area-based listing pages. Collected all restaurant URLs, branch IDs, area IDs. Output to JSON/CSV. Gathered ~15,773 unique restaurants across all UAE areas.

**Key discovery:** Talabat loads restaurant data via `__NEXT_DATA__` — a JSON blob embedded in the HTML that contains the full menu without any separate API call. No XHR interception needed.

### Phase 3 — Hidden API / `__NEXT_DATA__` Extraction
**Files:** `talabat/talabat_2_0/`, `talabat/TAB_MENU_VALIDATION/TALABAT_FAST_MENU.py`  
Switched from Scrapy to direct HTTP requests using `curl_cffi` with `impersonate="chrome124"` to bypass Cloudflare. Parses `__NEXT_DATA__` JSON for complete menus:
- `menu_categories[]`
- `items[].item_name`, `items[].price_aed`, `items[].item_id`, `items[].description`
- JSON-LD structured data: `lat`, `lon`, `ld_name`

**Performance:** ~2-3s per restaurant with residential proxy vs. 15-30s with Selenium.

### Phase 4 — Full Pipeline: Menu Tracker + Supabase (Current)
**Files:**  
- `talabat/menu/talabat_menu_tracker.py` — main orchestration script
- `talabat/supabase/supabase_writer.py` — DB write layer
- `talabat/menu/export_to_csv.py` — management export
- `talabat/menu/seed_from_excel.py` — enrichment seeder

Full pipeline: crawl all restaurants → extract menus → compare to previous snapshot → detect deltas → write to Supabase → export CSV/Excel.

---

## 4. Supabase Database Schema

**Project ID:** `cbagujraolgpcjmnpplg`  
**Region:** `ap-southeast-2` (Sydney)  
**Status:** `ACTIVE_HEALTHY`

### Tables (9 total)

#### `scrape_runs` — Run log
Every pipeline execution is logged here first.

| Column | Type | Notes |
|---|---|---|
| `run_id` | TEXT PK | e.g. `20260618_142446` |
| `platform` | TEXT | `talabat` / `zomato` / etc. |
| `mode` | TEXT | `TEST` / `FULL` |
| `restaurants_scraped` | INTEGER | |
| `restaurants_changed` | INTEGER | |
| `restaurants_no_change` | INTEGER | |
| `restaurants_failed` | INTEGER | |
| `items_total` | INTEGER | |
| `items_added` | INTEGER | |
| `items_removed` | INTEGER | |
| `items_price_changed` | INTEGER | |
| `items_desc_changed` | INTEGER | |
| `items_cat_changed` | INTEGER | |
| `started_at` | TIMESTAMPTZ | |
| `completed_at` | TIMESTAMPTZ | NULL until done |

#### `talabat_restaurants` — Restaurant master (15,773 rows)
One row per `branch_id`. Updated on each re-scrape.

| Column | Type | Source | Status |
|---|---|---|---|
| `branch_id` | BIGINT PK | Scraper | ✓ Full |
| `restaurant_id` | BIGINT | Scraper | ✓ Full |
| `restaurant_name` | TEXT NOT NULL | Scraper | ✓ Full |
| `ld_name` | TEXT | JSON-LD / Excel | ✓ Full |
| `restaurant_type` | TEXT | API → reclassified from Excel | 15,773 "Restaurant"; 197 with FSR/QSR/Cafe/Cloud Kitchen |
| `outlet_type` | TEXT | Derived from restaurant_type | 197 populated (FSR/QSR/Cafe/Cloud Kitchen) |
| `chained_outlet_type` | TEXT | **Future** | NULL |
| `std_terms` | TEXT | **Future** | NULL |
| `contact` | TEXT | **Future** (needs page scrape) | NULL |
| `map_url` | TEXT | Scraper | ✓ Full |
| `serves_cuisine` | TEXT[] | Scraper (Talabat tags) | ✓ Full |
| `key_cuisines` | TEXT[] | Excel "KEY CUISINES" | ✓ 5,275 populated (2026-06-18) |
| `ld_lat` | NUMERIC(10,7) | JSON-LD | ✓ Full |
| `ld_lon` | NUMERIC(10,7) | JSON-LD | ✓ Full |
| `area_id` | INTEGER | Scraper | ✓ Full |
| `area_name` | TEXT | Scraper | ✓ Full |
| `first_scraped_at` | TIMESTAMPTZ | Scraper | ✓ Full |
| `updated_at` | TIMESTAMPTZ | Scraper | ✓ Full |

#### `talabat_menu_items` — Current menu snapshot (386,079 rows)
Upserted on each scrape. Only the **latest state** lives here — full history in deltas.

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `branch_id` | BIGINT FK | → talabat_restaurants |
| `run_id` | TEXT FK | → scrape_runs |
| `item_id` | TEXT | Talabat internal ID |
| `item_name` | TEXT NOT NULL | |
| `item_key` | TEXT NOT NULL | Normalized: lower + stripped (match key) |
| `menu_category` | TEXT | Section name from __NEXT_DATA__ |
| `price_aed` | NUMERIC(10,2) | |
| `description` | TEXT | |
| `image_url` | TEXT | |
| `scraped_at` | TIMESTAMPTZ | |
| UNIQUE | (branch_id, item_key) | |

#### `talabat_menu_deltas` — Price/menu change history (append-only)
Every detected change since first scrape. Never updated, never deleted.

| Column | Type | Notes |
|---|---|---|
| `branch_id` | BIGINT FK | |
| `run_id` | TEXT FK | |
| `change_type` | TEXT | `ADDED` / `REMOVED` / `CHANGED` |
| `field_changed` | TEXT | `price` / `description` / `menu_category` / `new_item` / `removed_item` |
| `old_value` / `new_value` | TEXT | String representation |
| `old_price_aed` / `new_price_aed` | NUMERIC(10,2) | |
| `price_change_pct` | NUMERIC(8,2) | Positive = price increase |
| `detected_at` | TIMESTAMPTZ | |
| `baseline_run_id` | TEXT | Which run was the comparison baseline |

#### `fact_restaurants` — Cross-platform entity bridge
When Zomato/Deliveroo/Noon scraping starts, this is the join point. One row per platform+branch, grouped by `entity_group_id` (same physical restaurant across platforms).

#### `ingredients_taxonomy` — Ingredient classification (~1,000+ entries)
Loaded from `FoodAnalytics.Ingredient_category_taxonomy.json`. Used by future GPT-based ingredient extraction.

#### `menu_item_ingredients` — AI-extracted ingredients (future)
Links menu items to taxonomy. Populated by GPT-4o-mini parsing item descriptions.

#### `chain_brands` + `chain_locations` — Google Maps chain data (future)
One row per chain (KFC, McDonald's) in `chain_brands`. One row per physical outlet in `chain_locations` (from Apify Google Maps scraper). Geo-matched to `fact_restaurants` via haversine < 150m + name similarity > 0.85.

---

## 5. Core Scripts

### `talabat/menu/talabat_menu_tracker.py`
Main orchestration script. Key behaviors:

- **Checkpoint system:** saves processed `branch_id`s to `checkpoint.json` so interrupted runs resume
- **`--all` flag:** scrapes all 15,773 restaurants; on clean completion, auto-deletes checkpoint so next `--all` starts fresh
- **`--test N` flag:** scrapes first N restaurants for testing
- **Proxy:** Oxylabs residential proxy with `--proxy` flag; tracks exhaustion → saves checkpoint on exhaustion, terminates cleanly
- **Concurrency:** `asyncio.Semaphore` with configurable workers
- **Delta detection:** hash comparison on item_key → price/description/category diff on mismatch
- **Supabase write:** 4 RPC calls per restaurant (restaurant record → fact bridge → bulk menu items → bulk deltas)

**Run commands:**
```powershell
# Full scrape
python talabat_menu_tracker.py --all --proxy

# Test mode (first 50)
python talabat_menu_tracker.py --test 50

# Resume after proxy exhaustion (checkpoint auto-loaded)
python talabat_menu_tracker.py --all --proxy
```

### `talabat/supabase/supabase_writer.py`
RPC-first DB write layer. Why RPC:
- 150 menu items = 1 HTTP call via `fn_bulk_upsert_menu_items` vs. 150 REST calls
- Atomic per-restaurant commit
- Built-in retry on transient Windows network errors (`WinError 10035`, `WinError 10054`, HTTP/2 resets)

**RPC functions used:**
| Function | Purpose |
|---|---|
| `fn_upsert_scrape_run` | Register run at start |
| `fn_complete_scrape_run` | Write final stats at end |
| `fn_upsert_talabat_restaurant` | NULL-safe smart upsert (COALESCE keeps existing geo/ld data) |
| `fn_upsert_fact_restaurant` | Cross-platform entity bridge entry |
| `fn_bulk_upsert_menu_items` | Entire restaurant menu in ONE call |
| `fn_bulk_insert_menu_deltas` | All delta rows in ONE call |

### `talabat/menu/export_to_csv.py`
Exports full dataset to CSV + Excel for management.

**Output columns (per spec):**
`branch_id | restaurant_id | RESTRO NAME | ld_name | Type of Restaurants | map_url | serves_cuisine | ld_lat | ld_lon | area_id | area_name | KEY CUISINES | Menu category | Menu item(name) | Price | DESCRIPTION | Outlet Type | Chained Outlet Type | Std terms | INGREDIENTS | contact | scraped_at`

**Output files:** `talabat/menu/data/exports/talabat_full_YYYYMMDD_HHMMSS.csv` + `.xlsx`  
**Current CSV size:** ~143 MB, 386,079 rows

**Run command:**
```powershell
python export_to_csv.py --format both     # CSV + Excel
python export_to_csv.py --format csv      # CSV only (faster)
python export_to_csv.py --restaurants     # Restaurant-level summary only
```

### `talabat/menu/seed_from_excel.py`
Seeds `restaurant_type`, `outlet_type`, `key_cuisines` from `talabat/Data_menus.xlsx` into Supabase.

Uses concurrent `UPDATE` (20 threads) — NOT upsert — to avoid NOT NULL constraint on `restaurant_name`.

**Outlet type mapping (from Excel):**
| Excel value | `restaurant_type` | `outlet_type` |
|---|---|---|
| Full-Service Restaurant (FSR) | Full-Service Restaurant (FSR) | FSR |
| Quick-Service Restaurant (QSR) | Quick-Service Restaurant (QSR) | QSR |
| Cafes | Cafes | Cafe |
| Cloud Kitchen | Cloud Kitchen | Cloud Kitchen |

### `talabat/sanitization/`
Three scripts for text cleaning:
- `text_cleaner.py` — standalone regex cleaner
- `apply_to_supabase.py` — applies cleaning via REST API (hit statement timeout on large tables)
- `async_sanitize.py` — async version (blocked by IPv6 connection issue)

**Final approach:** Supabase MCP `execute_sql` with regex `[^ -~؀-ۿ\t\n]+` — strips emojis/special chars, preserves ASCII printable + Arabic Unicode block (U+0600–U+06FF).

---

## 6. Data Sources

| Source | What it provides | Volume |
|---|---|---|
| Talabat UAE (live scrape) | restaurant_name, menu_items, prices, serves_cuisine, geo-coords, area | 5,043 scraped branches, 386,079 menu items |
| `talabat/Data_menus.xlsx` | restaurant classifications (FSR/QSR/Cafe), KEY CUISINES | 5,275 unique branch_ids |
| Talabat listing pages | All 15,773 restaurant URLs, branch_ids, area_ids | 15,773 entries in talabat_restaurants |
| Apify Google Maps (planned) | chain outlets, ratings, reviews, phone, full address | TBD per chain |

### Data_menus.xlsx — actual columns
```
branch_id | restaurant_id | RESTRO NAME | ld_name | Type of Restaurants |
map_url | serves_cuisine | ld_lat | ld_lon | area_id | area_name |
match_method | KEY CUISINES | Menu category | Menu item(name) | Price | DESCRIPTION
```

**Important:** Excel does NOT contain `outlet_type`, `chained_outlet_type`, `std_terms`, `INGREDIENTS`, or `contact`. These are future data columns.

---

## 7. Challenges & Resolutions

### Challenge 1 — Credential Leak
**What happened:** Real Oxylabs credentials were committed to git in `.env.example`.  
**Resolution:** Rewrote git history with `git-filter-repo`, force-pushed all branches. All credentials rotated. Added to `.gitignore`.

### Challenge 2 — Supabase IPv6-Only Host (Windows Blocker)
**Problem:** `db.cbagujraolgpcjmnpplg.supabase.co` resolves only to an AAAA (IPv6) record. Python's `socket.getaddrinfo()` fails with `WinError 1231` (IPv6 network unreachable on this Windows machine). `asyncpg` direct DB connections are impossible.  
**What was tried:** asyncpg connection, Session Pooler (`ap-southeast-1`, `ap-southeast-2`, `us-east-1`, `eu-west-1`) — all returned "tenant not found". Session Pooler is not enabled on this project tier.  
**Resolution:** Use Supabase REST API (supabase-py) for all reads/writes. For bulk SQL that REST can't do efficiently, use **Supabase MCP `execute_sql`** which runs direct SQL server-side with no statement timeout.

### Challenge 3 — PostgREST Statement Timeout (8s)
**Problem:** Large table sanitization via REST `.range()` SELECT on 386K rows hit PostgREST's 8-second statement timeout (`57014` error).  
**Resolution:** Use MCP `execute_sql` which bypasses PostgREST entirely — runs direct SQL with no timeout. Used for:
- Sanitization UPDATE on `talabat_menu_items` (2,145 dirty rows)
- Sanitization UPDATE on `talabat_menu_deltas` (17,214 dirty rows)

### Challenge 4 — Emoji / Mojibake Sanitization
**Problem:** Menu item names contained emojis (e.g., `"CQ's New Sandwich Collection 🥖🍳"`) and mojibake (`"ðŸ¥–ðŸ³"` = UTF-8 emoji stored as Latin-1).  
**Resolution:** PostgreSQL regex UPDATE via MCP:
```sql
UPDATE talabat_menu_items
SET item_name = regexp_replace(item_name, '[^ -~؀-ۿ\t\n]+', '', 'g')
WHERE item_name ~ '[^ -~؀-ۿ\t\n]';
```
Pattern `[^ -~؀-ۿ\t\n]+` strips everything outside ASCII printable (U+0020–U+007E) and Arabic block (U+0600–U+06FF). Required TWO passes on `talabat_menu_deltas` to clear residual mojibake rows.

### Challenge 5 — scrape_runs All Zeros + completed_at NULL
**Problem:** Run `20260618_142446` showed all-zero stats and `completed_at: null`. Root cause: two bugs:
1. Checkpoint was fully saturated → `all_jobs = []` → no scraping happened → stats = 0
2. `complete_run()` RPC failed silently (no pending jobs = empty counters)

**Resolution:**
1. Added early-exit path in `talabat_menu_tracker.py` when `all_jobs` is empty — calls `fn_complete_scrape_run` with explicit zeroes before returning
2. Added auto-clear checkpoint logic: after a successful `--all` run without proxy exhaustion, deletes `checkpoint.json` so next `--all` starts fresh
3. Patched the stale DB record manually via `execute_sql`

### Challenge 6 — Windows UTF-8 Console Encoding
**Problem:** `UnicodeEncodeError: charmap can't encode '→'` — the `→` arrow in print statements couldn't encode to Windows cp1252 console. The CSV was actually written successfully before the crash.  
**Resolution:** Added to all scripts that print non-ASCII:
```python
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
```

### Challenge 7 — Supabase Upsert NOT NULL Constraint
**Problem:** When seeding `key_cuisines` via batch upsert, Supabase's upsert does INSERT first. Some Excel branch_ids (e.g., 606) were NOT in `talabat_restaurants`, so it tried to INSERT a row without `restaurant_name` (NOT NULL) → constraint violation on all 11 batches.  
**Resolution:** Switched from `.upsert()` to concurrent `.update().eq("branch_id", bid)` calls with `ThreadPoolExecutor(max_workers=20)` — pure UPDATE, only touches existing rows, skips missing ones. 5,275 rows updated in 100s with 0 errors.

### Challenge 8 — Export Script Unicode Crash
**Problem:** `export_to_csv.py` crashed with `UnicodeEncodeError` on `→` in a print statement. The 143 MB CSV had already been written successfully before the crash.  
**Resolution:** Added `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` at the top of the script.

---

## 8. Current Data State (as of 2026-06-18)

| Table | Rows | Notes |
|---|---|---|
| `talabat_restaurants` | 15,773 | All UAE restaurants from listing scrape |
| `talabat_menu_items` | 386,079 | Menus for 5,043 scraped branches |
| `talabat_menu_deltas` | (varies) | All detected changes since first scrape |
| `scrape_runs` | (varies) | One row per run |

### Column population in talabat_restaurants
| Column | Populated | Source |
|---|---|---|
| restaurant_name, map_url, serves_cuisine, geo, area | 15,773 / 15,773 | Scraper |
| key_cuisines | 5,275 / 15,773 | Excel seed (2026-06-18) |
| restaurant_type (FSR/QSR/Cafe/CK) | 197 / 15,773 | Excel seed (2026-06-18) |
| outlet_type | 197 / 15,773 | Derived from Excel (2026-06-18) |
| chained_outlet_type | 0 | Future |
| std_terms | 0 | Future |
| contact | 0 | Future (needs page scrape) |

---

## 9. Export Files

Latest export (post-seeding): `talabat/menu/data/exports/`
- `talabat_full_YYYYMMDD_HHMMSS.csv` — 143+ MB, 386,079 rows, 22 columns
- `talabat_full_YYYYMMDD_HHMMSS.xlsx` — 2 sheets: "Full Menu" + "Restaurants"

**Re-run export any time:**
```powershell
cd talabat\menu
python export_to_csv.py --format both
```

---

## 10. What Is Still Left / Roadmap

### Immediate / Short Term
| Task | Priority | Notes |
|---|---|---|
| Scrape remaining ~10,730 restaurants | HIGH | Only 5,043 of 15,773 have menus. Run `python talabat_menu_tracker.py --all --proxy` |
| Set up scheduled re-scrape (weekly) | HIGH | Windows Task Scheduler or cron — re-runs tracker, auto-clears checkpoint, picks up price changes |
| `contact` field | MEDIUM | Needs scrape of individual restaurant pages for phone/WhatsApp |
| `chained_outlet_type` | MEDIUM | Manual classification or chain name matching: "International Chain" / "Local Chain" / "Independent" |
| `std_terms` | MEDIUM | Standardized cuisine terminology — can be derived from `key_cuisines` via mapping table |

### Medium Term
| Task | Notes |
|---|---|
| Zomato UAE scraper | GraphQL API + `zomato_session` cookie + Oxylabs proxy |
| Deliveroo UAE scraper | REST API + JWT + playwright-stealth |
| Noon Food scraper | REST API + `x-noon-auth` header |
| `fact_restaurants` population | Talabat rows → `fact_restaurants` for cross-platform matching |
| `chain_brands` population | Extract unique chain names from restaurant names / `chained_outlet_type` |
| Apify Google Maps scrape | Per chain: input `"KFC UAE"` → `chain_locations` |
| `chain_locations` ↔ `fact_restaurants` geo-match | Haversine < 150m + name similarity > 0.85 → write `entity_group_id` |

### Long Term / AI Enrichment
| Task | Notes |
|---|---|
| `INGREDIENTS` column | GPT-4o-mini parsing menu item `description` → ingredient list → `menu_item_ingredients` |
| `ingredients_taxonomy` seeding | Load from `FoodAnalytics.Ingredient_category_taxonomy.json` |
| Price alert system | Delta table → email/Slack notification when price change > X% |
| Client-facing app | React/Next.js dashboard querying Supabase; add RLS when building auth layer |

### Columns That Need Future Data Sources
| Column | What it needs |
|---|---|
| `outlet_type` (remaining 15,576) | Classify all restaurants as FSR/QSR/Cafe/Cloud Kitchen — either manual, rule-based from restaurant name, or GPT |
| `chained_outlet_type` | Brand database: match against known chain names list |
| `std_terms` | Cuisine normalization: "Biryani, South Indian" → "Indian" |
| `contact` | Scrape individual restaurant detail pages (phone/WhatsApp visible on Talabat page) |
| `INGREDIENTS` | GPT-4o-mini extraction from `description` |

---

## 11. Key Architecture Decisions

### RPC-First Supabase Writes
Instead of one REST call per menu item (150 items = 150 HTTP calls), all menu items for a restaurant go in a single `fn_bulk_upsert_menu_items` RPC call. Result: ~150x fewer round-trips, atomic per-restaurant commit.

### `talabat_menu_items` = Current State Only
Menu items table holds only the **current snapshot** (upserted by item_key). Full history lives in `talabat_menu_deltas` (append-only). This keeps the items table lean and fast for joins.

### Checkpoint Pattern
`checkpoint.json` tracks processed `branch_id`s. On proxy exhaustion or Ctrl+C, saves state → next run resumes. On successful `--all` completion, auto-deletes so next run starts fresh (detects new restaurants, re-scrapes changed menus).

### `item_key` as Match Key
`item_key = item_name.lower().strip().replace(" ", "_")` — normalized name used for delta detection across scrapes. More stable than `item_id` (which can change if Talabat reorganizes menus).

### No Direct DB Connection (Windows IPv6 Limitation)
Supabase's direct host is IPv6-only; this Windows machine can't route IPv6. All DB access goes through:
1. Supabase REST API (supabase-py) — for reads/writes in Python scripts
2. Supabase MCP `execute_sql` — for bulk SQL (sanitization, ad-hoc queries, migrations)

### Export Encoding
CSV uses `utf-8-sig` (UTF-8 with BOM) so Excel opens Arabic + emoji content correctly without needing to specify encoding manually.

---

## 12. File Structure

```
talabat/
├── .env                          # Credentials — NEVER commit
├── .env.example                  # Redacted template
├── Data_menus.xlsx               # Management export + source of truth for classifications
├── TALABAT_PROJECT_CONTEXT.md    # This file
│
├── menu/
│   ├── talabat_menu_tracker.py   # Main pipeline orchestrator
│   ├── export_to_csv.py          # CSV + Excel export for management
│   ├── seed_from_excel.py        # Backfill enrichment from Data_menus.xlsx
│   ├── check_progress.py         # Quick DB stats check
│   ├── test_proxy.py             # Proxy connectivity test
│   └── data/
│       ├── checkpoint.json       # Resume state (auto-managed)
│       └── exports/              # Generated CSV + Excel files
│
├── supabase/
│   ├── PLAN.md                   # Full DB schema documentation
│   ├── supabase_writer.py        # RPC-based DB write layer
│   ├── seed_data.py              # Initial data seeder
│   └── backfill_existing.py      # Backfill for pre-existing data
│
├── sanitization/
│   ├── text_cleaner.py           # Standalone emoji/whitespace cleaner
│   ├── apply_to_supabase.py      # REST-based sanitization (hit timeout on large tables)
│   └── async_sanitize.py        # Async version (blocked by IPv6 on Windows)
│
├── listing_scraper/              # Scrapy spider — collected all 15,773 restaurant URLs
├── url_collector/                # URL collection utilities
├── talabat_2_0/                  # Next.js __NEXT_DATA__ extraction (v2 approach)
├── TAB_MENU_VALIDATION/          # Validation scripts + fast menu scraper
├── talabat-selenium/             # Initial Selenium scraper (Kuwait, deprecated)
├── Restaurant Identifier/        # Entity resolution utilities
├── tests/                        # Unit + proxy tests
│
├── enrich_left.py                # Enrichment for remaining unscraped restaurants
├── create_entity_resolution_excel.py
└── add_formula_sheet.py
```

---

## 13. Secrets & Security

| Secret | Location | Status |
|---|---|---|
| `SUPABASE_URL` | `talabat/.env` | Never committed |
| `SUPABASE_ANON_KEY` | `talabat/.env` | Never committed |
| `OXYLABS_USERNAME` | `talabat/.env` | Never committed (rotated after leak) |
| `OXYLABS_PASSWORD` | `talabat/.env` | Never committed (rotated after leak) |
| `OPENAI_API_KEY` | `talabat/.env` | Never committed |

**Git rules (enforced in `.gitignore`):**  
Never push: `.env`, `*.json`, `*.jsonl`, `*.csv`, `*.xlsx`, `*.md` (except `README.md`)

**Incident:** Real Oxylabs credentials were committed in `.env.example`. Remediation: `git-filter-repo` rewrote entire git history, force-pushed all branches, credentials rotated.

---

## 14. Quick Reference — Common Commands

```powershell
# Navigate to menu folder
cd "talabat\menu"

# Full scrape with proxy
python talabat_menu_tracker.py --all --proxy

# Test run (first 10 restaurants)
python talabat_menu_tracker.py --test 10

# Export fresh CSV + Excel
python export_to_csv.py --format both

# Export CSV only (faster)
python export_to_csv.py --format csv

# Re-seed restaurant classifications from Excel
python seed_from_excel.py --dry-run   # preview
python seed_from_excel.py             # live

# Sanitize emoji/special chars (via Supabase MCP execute_sql)
# Pattern: regexp_replace(col, '[^ -~؀-ۿ\t\n]+', '', 'g')
```

---

*This document covers the full project history, architecture, challenges, and roadmap as of 2026-06-18.*
