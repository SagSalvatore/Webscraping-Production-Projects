# Address Enrichment Plan — `maping_address.csv` → JSON + PostgreSQL

**Status:** PLANNED, NOT EXECUTED. Nothing has been written yet.
**Blocked on:** 2 decisions from Sagar (see [Open Decisions](#open-decisions)).
**Examined:** 2026-07-29

---

## 1. What this CSV actually is

`talabat/export/location_sanitization/maping_address.csv` is a **repair file**, not a
generic enrichment feed.

It covers 441 restaurants whose address in `talabat_export.json` was never a real
address — `location.raw` is *byte-identical to* `location.area` for **all 423** of them
that exist in the export. Someone manually sourced the real addresses, websites,
phone numbers and Google Maps URLs.

Evidence:

| | CSV-listed (423) | Everything else (14,775) |
|---|---|---|
| median `location.raw` length | **11 chars** | 58 chars |
| contains `"United Arab Emirates"` | **0%** | 47% |
| `location.raw == location.area` | **423 / 423** | — |

So the "423/423 address conflicts" seen on a naive diff are **not conflicts** —
the CSV address is the fix.

---

## 2. Key identity — CONFIRMED

`csv.branch_id` **IS** `talabat_export.json.source_id` **IS**
`talabat_restaurants.branch_id`.

Verified by cross-checking restaurant names on all 423 overlapping rows:

- exact name match: **423 / 423**
- mismatches: **0**
- near-misses: **0**

Do not re-litigate this.

---

## 3. Coverage

| Target | Matched | Note |
|---|---|---|
| `talabat_restaurants` (Postgres) | **441 / 441** | all 4 target columns currently `NULL` |
| `talabat_export.json` | **423 / 441** | 18 IDs absent — **Sagar said: ignore them for JSON** |

Each matching export ID touches **exactly 1 record** (no chain fan-out — confirmed).

### The 18 not in the export JSON
```
668456 714989 716212 717898 745048 745528 754263 757185 757242
765732 765988 766163 768891 770592 778109 778273 785178 799628
```
Real restaurants, first seen 2026-06-13, nearly all `verification_status = "Not Found"`
— the scrape never confirmed them so they were excluded from the export.
They **do** exist in Postgres (`talabat_restaurants` has 15,768 rows vs the export's
15,198 unique source_ids — a 570-row gap).
**Decision taken: write these to Postgres, skip for JSON.**

---

## 4. Field mapping

| CSV column | → `talabat_export.json` | → `talabat_restaurants` | CSV values present |
|---|---|---|---|
| `address` | `location.raw` | `address` | 441 |
| `website_link` | `website` | `website` | 237 (204 are `NA`) |
| `contact` | `contact_phone` | `contact` | 388 (36 are `NA`) |
| `google_map_url` | `maps_url` | `google_maps_url` | 384 (41 are `NA`) |
| `map_url` | — | `map_url` | 441 (talabat page URL) |
| `restaurant_name` | — | — | join-verification only |
| `google_key_word_search` | — | — | not worth persisting |

**Never touched:** `menu_items`, `location.area`, `geo`, `chain_id`, `cuisine`,
and every other key.

---

## 5. Gotchas found during examination — all must be handled

1. **Header has stray spaces.** Raw header is
   `['branch_id ', ' restaurant_name ', 'google_key_word_search', ' map_url', ...]`.
   → `.strip()` every column name on read, or the lookups silently return `None`.

2. **`NA` is the null marker** (not empty string). Treat
   `NA / N/A / NULL / - / ""` as missing. **Never write the literal string `"NA"`.**

3. **25 addresses have their Arabic destroyed** → literal ASCII `?` runs, e.g.
   `Shop: - 3 ???? ???? ????? - Jumeirah - Jumeirah 3 - Dubai - United Arab Emirates`
   Cause: the CSV was saved from Excel as ANSI/cp1252, which cannot represent Arabic.
   **This is permanent loss in the CSV** (confirmed at byte level — they are 0x3F,
   not an encoding artifact recoverable by `ftfy`).
   *The original Arabic still exists in `talabat_export.json`.*
   **→ Ask for the CSV to be re-saved as UTF-8 for future rounds.**

4. **2 rows where `map_url` contains a different id than `branch_id`:**
   - `branch_id=689470` (Proper steak Sandwiches), url says `689469` (Project Noodles)
   - `branch_id=689479` (Taro Sushi), url says `689473` (One More Taco)

   Resolved by name lookup against the DB: **`branch_id` is correct, the URL is stale.**
   Trust `branch_id`; do not parse ids out of `map_url`.

5. **Cloud-kitchen brands** — e.g. `689470` "Proper Steak Sandwiches" gets
   `website: oneatery.com` and a Maps URL for **"One Eatery"**. For virtual brands the
   website/maps URL describes the *host kitchen*, not the brand. Expected, not a bug,
   but do not treat these as name-mismatch errors.

---

## 6. Write totals (JSON, the 18 excluded)

```
records modified        423   of 17,187
  contact_phone set     388
  maps_url set          384
  website set           237
  location.raw set      423   (25 need ???? cleanup)
menu_items touched        0
records left alone   16,764
```

---

## 7. Implementation

### 7.1 PostgreSQL — `talabat_restaurants`, all 441

Connection — credentials come from `talabat/.env` via `talabat/db_config.py`, never
inlined. `PG_DSN` url-encodes the password, so the `@` that used to break URL parsing
is no longer a reason to avoid the URL form:

```python
from db_config import PG_PARAMS, PG_DSN

psycopg2.connect(**PG_PARAMS)      # keyword args
await asyncpg.connect(PG_DSN)      # or the URL
```

All four target columns are `NULL` on all 441 rows → pure gap-fill, zero overwrite risk.

```sql
UPDATE talabat_restaurants SET
    address         = COALESCE(NULLIF(address,''),         %(address)s),
    website         = COALESCE(NULLIF(website,''),         %(website)s),
    contact         = COALESCE(NULLIF(contact,''),         %(contact)s),
    google_maps_url = COALESCE(NULLIF(google_maps_url,''), %(gmap)s),
    updated_at      = now(),
    data_source     = 'google_manual_mapping_2026_07',
    enrichment_notes = COALESCE(enrichment_notes,'') || ' | address enriched from maping_address.csv 2026-07-29'
WHERE branch_id = %(branch_id)s;
```

- `COALESCE` keeps it **idempotent** — safe to re-run, never clobbers a value that
  already exists.
- Batch with `psycopg2.extras.execute_batch`, single transaction, explicit
  `commit()` / `rollback()`.
- Stamp `data_source` + `enrichment_notes` so the batch is traceable and reversible.

**Take a backup first:**
```
pg_dump -h localhost -U postgres -d RestaurantIntelligence -t talabat_restaurants \
        -f talabat_restaurants_backup_YYYYMMDD.sql
```
(`pg_dump` lives at `C:\Program Files\PostgreSQL\18\bin\`)

### 7.2 JSON — `talabat_export.json`, 423 records

Use **orjson** (Sagar's explicit request; the file is ~390 MB, orjson parses it in a
few seconds vs ~30s+ for stdlib `json`).

```python
import orjson
data = orjson.loads(open(PATH, "rb").read())
# ... mutate the 423 records in place ...
tmp = PATH + ".tmp"
with open(tmp, "wb") as f:
    f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
os.replace(tmp, PATH)          # atomic — a crash cannot leave a half-written master
```

**Back up `talabat_export.json` before writing.** It is the master file.

Note: `orjson.dumps` with `OPT_INDENT_2` will reformat the whole file (the current file
is single-line-per-record). If preserving the existing formatting matters, stream
record-by-record instead. Confirm before assuming.

---

## 8. Open Decisions

**These block execution. Do not guess.**

1. **`location.raw` — overwrite, or add a new key?**

   - **Option A — overwrite `location.raw`.** Nothing is lost: the current value is a
     byte-identical duplicate of `location.area`, which stays untouched.
     ```json
     "location": { "raw": "Al Danah - Zone 1 - Abu Dhabi - United Arab Emirates",
                   "country": "UAE", "city": null,
                   "area": "Zayed Sports City", "sublocality": null }
     ```
   - **Option B — keep `raw`, add `location.address_verified`.** Non-destructive but
     leaves two address fields for IT to reason about.

   *Recommendation: **A**.*

2. **The 25 `????` addresses** — (a) strip the destroyed runs and keep the good Latin
   parts, (b) skip those 25 rows entirely, or (c) write verbatim.

   Option (a) yields:
   `Shop: - 3 - Jumeirah - Jumeirah 3 - Dubai - United Arab Emirates`

   *Recommendation: **(a)**. (c) poisons the field.*

---

## 9. Reproducing the examination

Dry-run preview script (writes nothing, prints before/after on real records):
`scratchpad/dry_run_preview.py`

Rerun it after any change to the CSV to re-verify counts before executing.
