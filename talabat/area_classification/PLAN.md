# Area Classification — Plan of Action

**Input:** `ri-db.restaurants_full.json` — 24,689 records from Tech
**Output:** same file, same schema, `location.area` cleaned in place
**Status:** for review, nothing built yet

---

## 1. What we are starting from

```
records                          24,689
distinct area values              7,000     -> collapse to ~716 real areas
distinct source_id               22,327     (July 15,198 + August 7,129)
rows with no area                   122
```

The values are raw address fragments, not areas:

```
GCQG+62J Shakespeare and co - St. Regis Hotel, Beside   Plus Code + landmark
9C5V+7JC - Wasit Suburb                                 Plus Code + real area
Opposite City Corner Supermarket - Al Karama            directions + real area
68J9+3M5 - Food Court, first floor, WAFI                Plus Code + floor
Jumeirah Lake - Jumeirah Lake Towers (JLT)              area + its own tower
```

Most carry a genuine area buried in noise, usually as the last dash-separated
segment. That is exactly the shape July's rule engine was built for.

## 2. The join key is (source_id, area text) — NOT source_id

Measured, and it decides the whole design:

```
_id       overlap with July's cleaned file :        0     Tech regenerated them
source_id overlap                          :   15,198     all of July
source_ids with >1 row                     :      342     covering 2,704 rows
  siblings with IDENTICAL area text        :        0
  siblings with DIFFERENT area text        :      342
```

Every multi-row `source_id` has *different* area text per row — they are chain
branches at genuinely different addresses:

```
sid=1000053  Nad Al Hamar Baker   "Al Muntazi 2"  /  "Nadd Al Hamar"
sid=1101959  Arafa Restaurant     "Al Owan - Al Nakhil 1" / "Hor Al Anz - Deira"
```

A `source_id` join would stamp one branch's area onto its siblings. Reconciliation
is therefore keyed on the **raw area text**, which is what July's mapping tables
are keyed on anyway. This is the same rule `geo_resolve.py` applied in July, for
the same reason.

## 3. How much comes free from July's work

The original raw input to July's pipeline is gone, but its **caches and mapping
tables survive**, and those are keyed by raw text:

```
llm_area_cache.json          1,388     the LLM's answers per raw string
geocode_cache.json           4,643     coordinate -> area
addr_area_cache.json           380
tavily_area_cache.json         225
websearch_area_cache.json       57
manual_stage_provenance.csv    344     area_before -> area_after
9 from/to mapping tables       238     canonicalisation, Al-prefix, spellings
                             ─────
raw -> clean lookup          2,025
areas_list.csv                 716     the canonical vocabulary
```

Applied to the new file:

| | rows | share |
|---|---:|---:|
| already a canonical area | 7,150 | 29.0% |
| resolvable from the raw→clean lookup | 8,908 | 36.1% |
| **either — free reconciliation** | **10,726** | **43.4%** |
| **needs new work** | **13,963** | **56.6%** |
| distinct values needing work | 5,523 | |

**43.4% costs nothing.** That is the reconciliation you asked for.

## 4. Stages

Ordered cheapest-first, each seeing only what the previous could not resolve.
Same principle as July, which kept the paid stage at 22% of rows.

| # | stage | method | cost | expected |
|---|---|---|---|---|
| 0 | **test suite** | offline fixtures | none | gate |
| 1 | reconcile | raw→clean lookup + vocabulary | none | 43.4% |
| 2 | deterministic rules | port `clean_rules.py` | none | ~30% of the rest |
| 3 | coordinate resolve | `geocode_cache` + Nominatim | free / 1 req/s | tail |
| 4 | LLM | gpt-4.1-mini, distinct values only, batched, cached | small | residue |
| 5 | canonicalise | Al-prefix, spellings, casing | none | all rows |
| 6 | validate | automated gate | none | gate |
| 7 | emit | same schema, area in place | none | deliverable |

### Stage 2 — the rules that already work

Ported from `clean_rules.py`, not reinvented:

- strip Plus Codes (`GCQG+62J`), floor/unit noise, directional prefixes
  (`Opposite …`, `Beside …`, `Near …`)
- take the **most specific** known segment: `Al Barsha First - Al Barsha` →
  `Al Barsha First`, per the granularity rule set in July
- return `None` rather than guess — anything unresolved falls to stage 3

### Stage 3 — coordinates, if we can join them

`geocode_cache.json` holds 4,643 coordinate→area entries, and reverse geocoding
was July's most reliable fallback because coordinates are per-branch and were
never part of the copied address text.

**Open question:** the new file carries no `geo`. Coordinates exist in
`talabat_unified_202608.jsonl`, but joining them needs a row-level key and
`source_id` is not one. First task of stage 3 is to establish whether
`(source_id, name)` or `(source_id, city)` resolves the 342 multi-row cases. If
it does not, stage 3 is skipped and stage 4 absorbs the volume.

### Stage 5 — canonicalisation, conservatively

Re-uses July's decisions rather than re-deriving them:

- **frequency elects the surface form**, not a casing rule — so `DIFC` and
  `IMPZ` survive instead of becoming `Difc`
- **`Al ` prefix** applied only where both forms already exist; `Mirdif`,
  `Business Bay` and `Jumeirah` legitimately have no article
- **no fuzzy merging** — `Al Barsha 1` and `Al Barsha 3` score 91% similar and
  are different places

## 5. Test suite — before any production run

`test_area_pipeline.py`, three layers, all offline and free:

**SMOKE** — every module imports; the input parses; the lookup builds; schema
matches the expected 6 keys and `location` sub-keys.

**SANITY** — hand-built cases with known answers:

```
'9C5V+7JC - Wasit Suburb'                      -> 'Wasit Suburb'
'Opposite City Corner Supermarket - Al Karama' -> 'Al Karama'
'Al Barsha First - Al Barsha'                  -> 'Al Barsha First'
'Sheikh Zayed Road'                            -> None    (runs through many)
'DIFC'                                         -> 'DIFC'   (not 'Difc')
'Mirdif'                                       -> 'Mirdif' (no Al- prefix)
```

**REGRESSION** — every trap already paid for once:

- a `source_id` join must NOT be applied across the 342 multi-row ids
- `Al Barsha 1` must never merge with `Al Barsha 3`
- an arterial road returns null, never a plausible district
- unresolved rows keep their original text rather than being nulled

A `--dry-run` on every stage reports what it would change, with cost, before
spending anything.

## 6. Validation gate

Runs on the **output file**, not the inputs:

| check | rule |
|---|---|
| record count | exactly 24,689 |
| schema | 6 top-level keys, 4 `location` keys, unchanged order |
| untouched fields | `_id`, `mordor_restaurant_id`, `source_id`, `name`, `chain_id`, `location.country`, `location.city`, `location.sublocality` byte-identical |
| area populated | 100%, no nulls introduced |
| vocabulary | every value in the canonical list, or explicitly flagged as new |
| no noise | zero Plus Codes, floor references, directional prefixes |
| casing | one surface form per area |
| sibling safety | the 342 multi-row `source_id`s still hold distinct areas |

## 7. Deliverable

`ri-db.restaurants_full.json` — same file, same schema, `location.area`
overwritten in place. No `area_2`, no preserved original, per Tech's instruction.

A separate `area_resolution_map.csv` (raw → clean → method) stays on our side for
audit, and becomes next month's reconciliation input — the same way July's
caches made 43.4% of this month free.

## 8. Decisions — settled

### D1 — the join key is `source_id`, and 61.4% reconciles directly

```
1:1 source_id pairs                    15,170   61.4%
  name + chain + city all agree        12,742   84.0%   safe
  city disagrees                        2,337   15.4%   see D2
  typography-only name differences         95    0.6%   same restaurant
```

`_id` overlap is **zero** — Tech regenerated them. `source_id` overlaps on all
15,198 July restaurants, and the cleaned file has only 28 non-unique ids.

### D2 — the 2,337 city disagreements are July's coordinate corrections

Verified: **98.9%** of them appear in `coordinate_correction_map.csv`.

```
sid=1000003  Abd El Wahab   defect=city_mismatch
             city  Abu Dhabi -> Dubai
             area  Corniche  -> Burj Khalifa      lat 25.19463 (Dubai)
```

July's value is corrected **from the branch's own coordinates**; Tech's file
carries the uncorrected original, because it derives from `talabat_export.json`
which never received those fixes. July's area is therefore the better value.

Only `location.area` is written. The city stays as Tech sent it, and the 2,337
discrepancies are reported to Tech in a side file so they can apply the same
correction on their side.

### D3 — the input audit

```
24,567  (99.51%)  real text
   122  ( 0.49%)  JSON null
     0            empty strings, "NA" placeholders, wrong types, missing keys
```

No `"NA"` strings — the only absent form is a real JSON null.

```
has ' - ' separator   13,522   55.0%    <- what the rules key on
street token           4,130   16.8%
directional prefix       951    3.9%
plus code                536    2.2%
digits only               44    0.2%
Arabic script              0    0.0%    <- no translation stage needed
```

### D4 — the 44 digit-only values (`'0'`, `'2'`, `'C3'`)

Not junk to discard. Routed through coordinate resolution, then **Serper**:
query `"{restaurant name} uae restaurant"` — the template already measured 15/15
in the classification pipeline — and an LLM extracts the area from the result.
Null only if that also fails.

### D5 — the 122 nulls

Same treatment: Serper on `"{name} uae restaurant"` -> LLM extraction. **If no
clear area emerges, they stay null.** No guessing.

### D6 — new areas must be UAE

Genuine areas outside July's 716 will appear (August covers new ground). Any
candidate is validated as a real UAE location and **flagged for approval** —
never auto-accepted. Non-UAE values are rejected outright.

### D7 — canonical casing is a hard gate

One surface form per area, enforced as a validation failure rather than a
warning. Election order, per `canonicalize.py`:

1. the authoritative `areas_list.json` (716 areas) where the area exists there
2. frequency within the data — **never `.title()`**, which turns `DIFC` into
   `Difc`
3. numbered form wins over the ordinal word (`Al Barsha 1`, not `Al Barsha First`)

Fuzzy similarity finds candidates; it never decides a merge. `Al Barsha 1` and
`Al Barsha 3` score 91% and are different places.
