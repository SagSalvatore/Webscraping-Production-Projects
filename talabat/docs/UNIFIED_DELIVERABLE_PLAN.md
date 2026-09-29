# Unified JSONL Deliverable — Implementation Plan

**Status: APPROVED 2026-08-26. D1–D6 settled from measurement; implementing.**

One JSONL file carrying July's ~17k restaurants (with the refreshed July menus)
plus August's ~8k, in the existing schema, repeatable every month.

---

## 1. What the inspection established

Measured across all three inputs, not assumed.

### Schemas already agree — nothing needs reshaping

```
July-only record fields    []          July-only item fields    []
August-only record fields  []          August-only item fields  []
shared record fields       19          item fields identical    True
```

Both exports carry the same 18 fields, plus `is_verified_location` on location
records only (19th). `July_menu_update.json` is a 3-field carrier
(`source_name`, `source_id`, `menu_items`) whose item schema matches exactly.

### The join keys are clean

```
July export restaurants present in the refresh   15,198 / 15,198  (100%)
July  n  August  source_id                       0
August n  refresh source_id                      0
```

### Merge arithmetic

| side | records | items after merge |
|---|---:|---:|
| July main | 15,198 | 1,247,765 |
| July location | 1,989 | 246,493 |
| **July total** | **17,187** | **1,494,258** (was 1,327,460) |
| August | 8,268 | 600,255 |
| **OUTPUT** | **25,455** | **2,094,513** |

All 1,989 location records resolve to a parent in the refresh — zero misses.

### Size

Probed with `orjson` on 400 records of each, scaled:

```
July    ~25.0 KB/record  ->  ~497 MB  (after refresh, +12.6% items)
August  ~20.8 KB/record  ->  ~176 MB
                             ~673 MB total JSONL
```

Against ~800 MB+ for the same content as indented JSON. `gzip -9` would land
near 100–150 MB — worth offering, since JSON compresses extremely well and Tech
can stream `.jsonl.gz` directly.

Disk free: 32.4 GB. `orjson 3.11.3` and `ijson 3.4.0` both installed.

---

## 2. The six decisions — settled

Each was investigated before deciding. The first three changed on measurement.

### D1 — normalise to `"NA"`, and say so honestly in the report

`null` and `"NA"` did **not** mean the same thing. Splitting the null rate by
record kind proves it:

```
field            MAIN null              LOCATION null
contact_phone    6,459/15,198  42.5%    110/1,989   5.5%
website          7,765/15,198  51.1%     89/1,989   4.5%
maps_url         5,346/15,198  35.2%       0/1,989   0.0%
```

`maps_url` is null on **0.0%** of location records — those were sourced *from*
Google Maps, so they always have one. Main records largely never went through
that enrichment.

| | meaning |
|---|---|
| July `null` | **we never looked** — no enrichment ran |
| August `"NA"` | **we looked, found nothing** — Serper ran, no value |

**DECIDED: normalise all three to `"NA"`.** One type, one convention; Tech's
consumer only needs "is there a value". But the run report must state that
July's coverage is lower because those restaurants were never enriched, *not*
because the data is confirmed absent — otherwise the file asserts a check we
never performed. Real parity would need a Google enrichment pass over July's
cohort: separate task, separate cost.

`chain_type` stays `null` in both — it is a real "not a chain" signal, not a
missing value.

### D2 — the city is genuinely absent; fill it from coordinates

```
records with no city                    4,690  (27.3%)
  raw == area (the fallback signature)  4,498  (95.9%)
```

`export_to_json` parses `city` from the last address segment and falls back to
`area_name` when there is no address. 95.9% carry that fallback signature —
Talabat never supplied an address:

```
('876',    'Al Barsha 3', 'Al Barsha 3', None)   <- only an area label
('703524',  None,          None,          None)   <- nothing at all
```

The other 204 are damaged addresses — truncated (`...Abu Dhab`) or with an
Arabic city segment that was stripped.

**DECIDED: run the coordinate kNN over July.** `geo` exists on 16,983 of 17,187
records, and the method measured 99.45% held-out on August. Deriving city from
coordinates we already hold is legitimate; the `MAX_KM = 15` guard leaves it
NULL rather than guessing.

**Labelled points must come from verified-location records only.** July's own
`city` values are parsed from address text while `geo` comes from Talabat, and
they disagree badly (100% of July's "Al Ain" rows sit >40 km from Al Ain).
Training on them would reproduce that error.

### D3 — traced to `scrub()`; translate, do not drop

```
scrape rows scanned              1,246,162
item_keys that are Arabic-only         204
```

Cause: `build_july_update.py:107`, `out = out or None`. `scrub()` strips Arabic
per the no-Arabic rule; when a name is *entirely* Arabic nothing remains and it
becomes `None`. The July export has 0 such nulls because it never scrubbed.

The items are real:

```
بيبسي                    = Pepsi
توشكا                    = Toshka
كيكة جوز الهند الطازجة    = Fresh Coconut Cake
غداء إس كيه إم دي باور    = SKMD Power Lunch
```

**DECIDED: translate them.** Dropping real menu items over a formatting rule
loses genuine data. `August_menu/translate_arabic.py` already provides a batched
translator that transliterates brand names and verifies the output contains no
non-Latin characters. ~334 strings, 1–2 API calls.

**Upstream fix:** when `scrub()` empties a string, translate the original
instead of returning `None`. Also `validate_july_update.py` must fail on null
`name`/`section` — its text loop only inspects values that are already `str`,
so nulls were skipped rather than checked. That gap is why this shipped.

### D4 — location records take the parent's refreshed menu

Confirmed: location records reuse the parent's `source_id` **and** `chain_id`
(one KFC keeps one `chain_id` throughout). Same `source_id` means the same
restaurant, so one price per item per chain. Produces the 246,493 figure above,
up from 220,089.

Pre-flight asserts the `chain_id` claim rather than trusting it.

### D5 — `710110`: leave alone, log it

In the refresh, in no export, so there is no metadata row to attach its 2 items
to. Nothing is synthesised; it is named in the run report so it is visible
rather than silent.

### D6 — no vintage field

A `data_month` field would be **ambiguous**: in the July cohort the metadata is
from July but the menus were refreshed in August, so one field cannot describe
both. If Tech needs the split later, a side-file mapping `source_id` → cohort is
the honest form.

---

## 3. Implementation

Four scripts in `talabat/unified/`. The two fix steps need an API and produce
small **sidecar** files; the builder is then pure and deterministic, which is
what makes a monthly job reproducible and byte-diffable.

Sidecars also mean **no input is mutated**. `July_menu_update.json` has already
been handed to Tech; patching it now would leave two files with one name.

| # | script | needs API | writes |
|---|---|---|---|
| 1 | `fix_arabic_names.py` | yes (OpenAI) | `data/arabic_fixes.json` |
| 2 | `fill_july_cities.py` | no (kNN) | `data/july_city_fixes.json` |
| 3 | `build_unified_export.py` | no | `data/talabat_unified_202608.jsonl` |
| 4 | `validate_unified_export.py` | no | `data/unified_validation.json` |

Steps 1 and 2 are independent and each `--dry-run`-able. Step 3 refuses to run
if a sidecar is missing, rather than silently shipping the defect.

### Design

- **Read** with `ijson` — streaming, constant memory. The box has run these
  files at under 1 GB RAM before and must keep doing so.
- **Write** with `orjson.dumps(rec)` + `b"\n"` per record, opened in binary.
  `orjson` is ~5× faster than `json` and emits compact separators by default.
- **One pre-pass** builds `{source_id: menu_items}` from the refresh. Peak
  memory is that dict — the largest single structure, and the reason the refresh
  is indexed rather than the exports.
- **Field order preserved** by mutating the record read from the export rather
  than rebuilding it, so `orjson` emits keys in the source order. Byte-level
  diffability against last month matters for a recurring deliverable.

### Stages

```
1  index refresh          source_id -> menu_items          (~2 min)
2  stream July export     swap menu_items, normalise D1/D2, write   (~6 min)
3  stream August export   normalise D1, write                       (~3 min)
4  validate the OUTPUT    fresh streaming pass                      (~5 min)
```

Nothing mutates in place. Output is a new file; all three inputs stay untouched.

### CLI

```bash
python build_unified_export.py --dry-run     # counts + decisions, writes nothing
python build_unified_export.py               # build
python build_unified_export.py --gzip        # also emit .jsonl.gz
python validate_unified_export.py            # standalone gate
```

Month-to-month reuse is via a config block at the top — `COHORTS` as a list of
`(export_path, refresh_path_or_None)`. September adds one line.

---

## 4. Test suite

### Pre-flight (before any write)

| # | assertion |
|---|---|
| 1 | every refresh `source_id` resolves to an export record, or is named as an orphan |
| 2 | record field sets ⊆ the 19 known fields; no unexpected field |
| 3 | item field sets == the 7 known fields, exactly |
| 4 | `source_id` sets across cohorts are disjoint |
| 5 | no cohort is empty (a mis-set path fails loudly, not silently) |

### In-flight

Running counters, asserted before the file is promoted from `.tmp`:
records written == records read; items written == the predicted 2,094,513;
zero records with an empty `menu_items`.

### Post-flight (`validate_unified_export.py`, streaming)

| group | check |
|---|---|
| structural | every line parses as JSON; exactly one object per line |
| identity | `(source_id, location.raw)` unique — location records legitimately repeat `source_id`, so `source_id` alone is **not** a key here |
| schema | field presence and type per field, against a frozen contract |
| convention | zero `null` in the D1 fields once normalised; `chain_type` still allows null |
| menu | no null `name`; `price` non-null and >= 0; `std_term` non-null |
| text | zero emoji / mojibake / Arabic / CJK / control / zero-width |
| labels | same item name never carries two `std_term`s or two ingredient sets |
| counts | 25,455 records, 2,094,513 items, reconciled against the run report |
| chain | one `chain_id` per brand name; no brand split across two ids |

The label check is the invariant you set — it currently holds at 0/0 within
July, and merging August is exactly where it could break, since both cohorts
carry overlapping item names (46,611 shared).

### Round-trip

Sample 200 records, re-parse from the written JSONL, and assert deep equality
against the in-memory record. Catches any encoding or float-precision drift
`orjson` might introduce.

---

## 5. Risks

| risk | mitigation |
|---|---|
| Cross-cohort `std_term` conflict on the 46,611 shared names | post-flight check; resolve by the same winner-takes-most rule already used |
| 2,370 non-food rows propagate into the unified file | known and accepted for now; the fix is scripted and can run before this build |
| `"NA"` normalisation changes data Tech already holds | D1 is an explicit decision, recorded in the run report |
| Peak memory on the refresh index | measured before the build; falls back to an on-disk shelf if it exceeds budget |
| Output partially written on a crash | write `.tmp`, validate, then atomic rename |

---

## 6. Still open

- **Filename** — using `talabat_unified_202608.jsonl` unless told otherwise.
- **`.jsonl.gz`** — `--gzip` flag built in; whether to ship it is Tech's call.
- **The 2,370 non-food rows** propagate into the unified file unless the fix is
  run first. Deferred by explicit decision; the script is ready.
- **Google enrichment parity for July** (see D1) — separate costed task.

## 7. Carried forward — defects this exposed

1. `scrub()` returns `None` on an all-Arabic string instead of translating it
   (`build_july_update.py:107`). Sidecar fixes the output; the function should
   be fixed for September.
2. `validate_july_update.py` never checks for null `name`/`section` — its text
   loop only inspects values that are already `str`. This is why 140 nulls
   reached a file that passed every gate.
3. `July_menu_update.json` as delivered contains those 140 nulls. If Tech has
   already ingested it, they need either the corrected file or the sidecar.
