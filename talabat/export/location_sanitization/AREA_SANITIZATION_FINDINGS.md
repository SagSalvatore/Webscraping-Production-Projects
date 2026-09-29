# Area Sanitization — Findings & Plan

**Status:** EXAMINED, NOT EXECUTED. No `area_2` has been written.
**Examined:** 2026-07-28 / 29
**Inputs:** `LOCATION_FIND.csv` (92 unique locations), `restaurant_locations.json` (17,164 rows)

---

## 1. Headline: two different jobs, very different coverage

These were being conflated. They are **separate**.

| Job | What it means | Coverage |
|---|---|---|
| **A — Classify** | map an area to one of the 92 names in `LOCATION_FIND.csv` | **~40%** (6,840 rows / 6,263 restaurants) |
| **B — Standardize** | clean `"Level 1, Mirdif City Centre"` → `"Mirdif City Centre"` | **~99%** (16,991 rows / 15,030 restaurants) |

Counted three ways (all valid, pick per use case):
- **17,164** — physical outlets at distinct addresses (`source_id + address`) ← right for location work
- **15,198** — Talabat listings (`source_id`)
- **9,906** — brands/chains (`chain_id`)

> `chain_id + restaurant_name` gives only **9,936** — it *groups* branches, it does not
> separate them. KFC's 247 rows share one `chain_id` and one `source_id` but have 247
> distinct addresses. **`address` is what makes a row unique.**

---

## 2. MEASURED: standardization does NOT lift the match rate

This disproved the initial hypothesis. Do not repeat the mistake.

| Stage | Community matches | % |
|---|---|---|
| raw `area` as-is | 6,763 | 39.4% |
| + structural cleaning (strip floor/unit/mall noise, last segment, spelling fixes) | 6,735 | 39.2% |
| + fuzzy matching (`rapidfuzz` token_sort_ratio ≥ 88) | 6,840 | **39.9%** |

**Cleaning bought nothing.** The unmatched areas are not dirty — they are **absent from
the list**. `Al Dhagaya` (924 rows), `Zayed Sports City` (728), `Deira` (394),
`Al Karama` (356), `Al Quoz` (249), `Mirdif` (148) are already clean and correctly
spelled. They fail because the list has 92 entries and the data has ~1,900 real areas.

**The only lever that raises coverage is expanding `LOCATION_FIND.csv`:**

| Entries added | Coverage |
|---|---|
| +5 (Al Dhagaya, Zayed Sports City, Deira, Zone 1, Al Karama) | 56% |
| +20 | 65% |
| +50 | 73% |
| +100 | 79% |
| +200 | 85% |

---

## 3. Problems in `LOCATION_FIND.csv` itself

- 93 lines → **92 unique** (`Dubai Sports City` appears twice)
- Three variants of the same place: `Sobha Hartland` / `Sobha Hartland II` / `Sobha Hartland 2`
- Spelling pairs: `Jabal Ali First` / `Jebel Ali First`
- Overlapping: `Downtown` and `Downtown Dubai`
- **25 of 92 never match anything** — Palm Jebel Ali, The Valley, World Islands,
  Damac Lagoons, Siniya Island etc. (new/under-construction, no restaurants yet)
- 4 entries are `Emirate of X` — these match ~3,400 rows but add **nothing** over the
  existing `city` field (every address ends `"- Abu Dhabi - United Arab Emirates"`).
  **Exclude them from any "classified" count** or the number is inflated to a false 60%.

Alias formats needing expansion before matching:
`JBR - Jumeirah Beach Residence`, `DSO - Dubai Silicon Oasis`,
`Dubai International Financial Centre | DIFC`, `Damac Hills II ( Akoya )`,
`Dubai Design District (d3)`

---

## 4. Matching method that works

**Segment-based, not substring.** Split area/address on `" - "`, `","` **and `"،"`
(U+060C Arabic comma)**, then exact-match whole segments against the canonical forms.

Why: naive substring matching produced false positives — `"Al Meydan Rd"` (a street in
Al Quoz) matched `MBR City - Meydan` because the alias splitter created a bare
`"Meydan"` token. Segment equality eliminates this class of error entirely
(Tier-3 matches fell from 3,420 → 73 once fixed).

Match tiers, longest-canonical-first so the most specific name wins:
1. **Tier 1** — an `area` segment *is* a canonical name → 3,314 rows (high confidence)
2. **Tier 2** — canonical name found inside the `area` string → 3,449 rows
3. **Tier 3** — found only in the full `address` → 73 rows (review these)

---

## 5. Profile of the `area` column

```
rows 17,164 | non-empty 17,046 | empty 118 | distinct 5,034
length min/median/max: 1 / 23 / 157
contains " - ": 4,320 distinct | contains ",": 525 | contains a digit: 2,420
```

Noise tokens present in distinct values:
`street/road` 1,595 · `building/tower` 918 · `near/inside` 550 · `floor/level` 313 ·
`unit/shop` 308 · `block/plot` 66

**The key lever for standardization:** taking the last meaningful segment collapses
**5,034 distinct strings → 1,928** (62% cardinality reduction), and those last segments
are clean names (`Al Dhagaya`, `Zayed Sports City`, `Mirdif`, `Dubai Silicon Oasis`).

Spelling-variant families to normalize: `Al Qouz`↔`Al Quoz` (353 rows),
`Jumeirah Lakes Towers`↔`Jumeirah Lake Towers`, `Jabal`↔`Jebel Ali`,
`Ras Al Khaimah`↔`Ras Al-Khaimah`, `Ind.first/second/third` → `Industrial <n>`

Fuzzy matching (`rapidfuzz`, ≥88) recovers ~105 rows of genuine variants:
`Al Jadaf`→`Al Jaddaf` (49), `Al Hamriya`→`Al Hamriyah` (31),
`Dubai Investment Park`→`Dubai Investments Park` (16)

---

## 6. Encoding / Arabic — audited

**It is genuine Arabic, not mojibake.** Scale is small: 307 rows (1.8%).

| Issue | Rows |
|---|---|
| contains Arabic script | 307 |
| **true mojibake** (`ï»¿` = UTF-8 BOM read as cp1252) | **2** |
| zero-width / bidi control marks | 3 |
| curly apostrophes (`Al ‘Uwaid`) — `ftfy` fixes | 48 |

**303 of 307 already work** because the rows are bilingual — Arabic sits *next to* the
English name, so dropping it leaves the real area intact:
`'Yas Island - ياس غرب'` → `Yas Island` ✓

Three real bugs to fix in the cleaning pipeline:
1. **Arabic comma `،` (U+060C) is not treated as a separator** — 54 rows use it instead
   of `,`. One-character regex fix.
2. **Digit leakage** — `'Al Bustan - ليوارة 2'` normalizes to `'al bustan 2'`; the `2`
   survives from the Arabic segment and corrupts the token.
3. **4 rows are pure Arabic** and normalize to empty. Their `city` is still recoverable
   from the address. Need transliteration or manual review:
   - `'شارع الشيخ عمار بن حميد، - جسر الشيخ عمار'` (×2)
   - `'شارع - المَقطَع'`
   - `'٩'`

Pipeline order: `ftfy.fix_text()` → strip bidi/zero-width → split on `[-,،]` →
drop noise segments → spelling normalization → match.

---

## 7. Recommended plan

Treat as **two independent workstreams** — do not couple them:

1. **`area_2` standardization** — covers 99% today, independent of the CSV list.
   This is what solves `"Level 1, Mirdif City Centre"`.
   *(Note: `Mirdif` is not in `LOCATION_FIND.csv` at all, so classification can never
   fix that example — only standardization can.)*
2. **Expand `LOCATION_FIND.csv`** — the only way past the 40% classification ceiling.
   Start with the 20 highest-volume missing areas listed in §2.

### Cautions
- **`Zone 1` (380 rows) and `Industrial Area` (130)** are ambiguous — meaningless
  without city context (Abu Dhabi's Zone 1 vs elsewhere). Do **not** add as standalone
  canonical entries; they need city-scoped handling.
- **Sub-communities collapse upward.** `Al Barsha 1/3/South` all fold into `Al Barsha`
  (2,968 rows — 43% of all matches). Fine for community-level grouping, but sub-area
  granularity is lost permanently. `Barsha Heights` correctly stays separate (49 rows).

---

## 8. Available tooling (verified installed)

`pandas` 2.2.3 · `polars` 1.37.1 · `pyarrow` 21.0.0 · `rapidfuzz` 3.13.0 ·
`ftfy` 6.3.1 · `unidecode` · `regex` · `nltk` 3.9.1 · `scikit-learn` 1.7.2 ·
`orjson` 3.11.3 · `psycopg2` 2.9.10 · `sqlalchemy` 2.0.43

Not installed: `duckdb`, `pandera`, `spacy`, `janitor`

**LibreOffice is NOT installed** → the xlsx skill's `recalc.py` cannot run on this
machine. Write literal values into spreadsheets, not formulas, or they read back blank
in pandas/previewers.
