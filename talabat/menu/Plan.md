# UAE Menu Standardization & Ingredient Mapping Engine (V1 — Updated)

## Objective

Map 1,123,963 UAE menu rows to curated std_terms and inherit ingredients from the Dubai
knowledge base.

Final columns written to **talabat_menu_items**:
- std_term
- taxonomy
- confidence
- method
- review_required

---

## Available Datasets

### UAE Dataset (talabat_menu_items — already cleaned)

| Column | Notes |
|--------|-------|
| item_key_clean | Slugified (spaces→underscores). Un-slug before matching. |
| menu_category_clean | Slugified. Un-slug before matching. |
| description_clean | Free text, use as secondary signal only. |
| price_aed | Do NOT use for matching. |

Unique item_key_clean values: **355,727**
Total rows: **1,123,963**

---

### Dubai Knowledge Base (Dubai_knwl.csv)

214,757 rows | 13 columns

Quality breakdown:
- Usable rows (non-null, non-N/A std_term): **106,743**
- Rows whose std_term exactly matches our 572 taxonomy: **58,231**
- Rows with both a matching std_term AND valid ingredients: **40,097** ← Phase 2 gold data

KB std_terms NOT in our taxonomy (top examples): curry, fries, cold drink,
wraps and rolls, coffee, biryani — these are valid terms, just named differently.
They are fuzzy-mapped to our taxonomy automatically.

Integration strategy:
- Phase 1: build name→std_term lookup from 58K quality rows (extra matching tier)
- Phase 2: aggregate top-10 ingredients per std_term from 40K quality rows

---

### Curated Taxonomy Dataset (final_std_terms_taxonomy.csv)

572 curated std_terms with taxonomy assignments.
This is the **source of truth**. All matching targets this list only.

Taxonomy categories: core_food, beverage, dessert, side, addon, combo,
accompaniment, breakfast, marketing/non-standard menu

---

## Tech Stack

- Python, Polars, NumPy, PyArrow
- RapidFuzz (fuzzy matching, C++ parallel)
- scikit-learn (TF-IDF cosine similarity)
- sentence-transformers / intfloat/multilingual-e5-small (semantic embeddings)
- asyncpg (bulk DB writes via COPY + UPDATE)
- NO OpenAI (budget constraint)

---

## DB Output Schema

New columns added to **talabat_menu_items** (NOT talabat_restaurants):

```sql
ALTER TABLE talabat_menu_items
ADD COLUMN IF NOT EXISTS std_term       TEXT,
ADD COLUMN IF NOT EXISTS taxonomy       TEXT,
ADD COLUMN IF NOT EXISTS confidence     FLOAT,
ADD COLUMN IF NOT EXISTS method         TEXT,
ADD COLUMN IF NOT EXISTS review_required BOOLEAN DEFAULT FALSE;
```

**talabat_restaurants.std_terms** is a separate restaurant-level cuisine column
(future use, not touched in this pipeline).

Phase 2 writes to: **menu_item_ingredients** (already has schema, currently empty).

---

## Architecture

```
UAE Data (item_key_clean, menu_category_clean, description_clean)
         ↓
  Deduplicate on item_key_clean → 355K unique rows
         ↓
  Un-slug: replace _ with space → "french_fries" → "french fries"
         ↓
  Combined text: item_key_text + " " + category_text
         ↓
  ┌─────────────────────────────────────────────────────────┐
  │ PHASE 1: MATCHING (run on 355K unique keys only)        │
  │                                                          │
  │  Step 1: Rule Engine         ~10-15% coverage           │
  │  Step 2: Exact Match         ~15-20% additional         │
  │  Step 3: KB Name Lookup      ~10-15% additional         │
  │  Step 4: RapidFuzz           ~30-40% additional         │
  │  Step 5: TF-IDF Cosine       ~5-10%  additional         │
  │  Step 6: Embeddings          ~10-15% additional         │
  │  Remainder → method=unknown, review_required=True        │
  └─────────────────────────────────────────────────────────┘
         ↓
  Join unique mapping back to 1.1M rows (Polars join)
         ↓
  asyncpg COPY + UPDATE → talabat_menu_items
         ↓
  Save master_mapping.parquet (cache for future runs)
         ↓
  PHASE 2 (separate run): Ingredient inheritance from Dubai KB
```

---

## Step 1: Text Normalization

- Un-slug: `item_key_clean.replace("_", " ")` → matching text
- Lowercase for matching
- Strip punctuation for fuzzy/TF-IDF/embedding comparisons
- Build combined_text = `unslug(item_key) + " " + unslug(category)`
- DO NOT include description in combined_text (too noisy for matching)
- DO NOT remove Arabic from original columns (already handled in clean phase)

---

## Step 2: Rule Engine

Handles clear-cut cases before any ML. Pattern match on combined_text.

| Pattern | std_term | taxonomy |
|---------|----------|---------- |
| Category contains best_seller / featured / recommended / special_offer | marketing/non-standard menu | marketing/non-standard menu |
| Item contains stationery / packaging_fee / delivery_fee / service_charge | marketing/non-standard menu | marketing/non-standard menu |
| Item starts with extra_ / add_on_ | addon | addon |
| Item is exactly combo / family_meal / value_meal | combo | combo |
| Item is a pure beverage marker (water only / juice only) | soft drink / juice | beverage |

Method: `rule` | Confidence: 0.99

---

## Step 3: Exact Match

Compare `normalize(item_key_text)` against normalized 572 std_terms.

Also try:
- First word of item_key_text
- Category text alone (when item is ambiguous)

Method: `exact` | Confidence: 1.00

---

## Step 4: KB Name Lookup

From 58,231 Dubai KB quality rows: build dict
`{normalized_menu_item_name → (std_term, taxonomy)}`

Check UAE item_key_text against this dict (exact normalized match).

This captures items like "Classic Burger" → "burger", "Orange Juice" → "juice"
that exist in the KB even if not in our 572 taxonomy by exact name.

Method: `kb_exact` | Confidence: 0.95

---

## Step 5: RapidFuzz

Bulk token_set_ratio + partial_ratio matching.
- `process.cdist(queries, std_terms, scorer=token_set_ratio, workers=-1)`
- Processed in 5K-row chunks to limit memory
- score_cutoff = 70

| Score | confidence | review_required |
|-------|-----------|----------------|
| >= 88 | score/100 | False |
| 70-87 | score/100 | True |

Method: `fuzzy`

Handles: typos, qualifiers ("classic burger" → "burger"), plurals ("burgers" → "burger")

---

## Step 6: TF-IDF Cosine

sklearn TfidfVectorizer (1-2 ngrams) on unmatched items.
Sparse matrix cosine similarity in 5K-row batches.

| Cosine | confidence | review_required |
|--------|-----------|----------------|
| >= 0.45 | cosine | False |
| 0.30-0.44 | cosine | True |
| < 0.30 | skip | — |

Method: `tfidf`

Handles: lexical similarity not caught by fuzzy (different word forms).

---

## Step 7: Semantic Embeddings

Model: `intfloat/multilingual-e5-small` (384-dim, ~117MB, multilingual)
- Encode 572 std_terms once (fast)
- Encode remaining unmatched items in batches of 256
- Cosine similarity → best std_term match

| Cosine | confidence | review_required |
|--------|-----------|----------------|
| >= 0.82 | cosine | False |
| 0.68-0.81 | cosine | True |
| < 0.68 | method=unknown | True |

Method: `embedding`

Handles: semantic relationships ("dark chocolate latte" → "hot beverage")

---

## Step 8: Confidence Rules (Summary)

| method | auto_accept threshold | review threshold |
|--------|-----------------------|-----------------|
| rule | — (always accept) | — |
| exact | — (always accept) | — |
| kb_exact | — (accept) | — |
| fuzzy | score >= 88 | 70-87 |
| tfidf | cosine >= 0.45 | 0.30-0.44 |
| embedding | cosine >= 0.82 | 0.68-0.81 |
| unknown | — | always |

---

## Step 9: Join Back + DB Write

1. unique_mapping (355K rows) joined to full 1.1M rows via item_key_clean
2. asyncpg COPY to public._entity_staging (id, std_term, taxonomy, confidence, method, review_required)
3. UPDATE talabat_menu_items FROM _entity_staging WHERE id = s.id
4. DROP staging table

---

## Step 10: Method Tracking

Every row has method ∈ {rule, exact, kb_exact, fuzzy, tfidf, embedding, unknown}.
Critical for debugging and understanding coverage.

---

## Step 11: Master Mapping Cache

`master_mapping.parquet` — columns:
- item_key_clean, combined_text, std_term, taxonomy, confidence, method, review_required

Future runs: if item_key_clean exists in cache → skip pipeline, use cached result.

---

## Phase 2: Ingredient Inheritance (separate script)

From 40,097 Dubai KB rows with valid std_term + ingredients:
1. Map KB std_terms → our taxonomy via fuzzy (auto)
2. Aggregate ingredient lists per std_term (frequency-ranked, top 10)
3. After Phase 1 assigns std_term to UAE rows → look up aggregated ingredients
4. Write to menu_item_ingredients table (platform, branch_id, item_key, ingredient_name, etc.)
5. Only inherit for rows with confidence >= 0.85

Method: `kb_aggregate`

---

## Important Principles

1. Never remove UAE rows — every row gets a result (even if method=unknown).
2. Deduplicate before matching — process 355K unique keys, not 1.1M.
3. No OpenAI — rule + fuzzy + TF-IDF + embeddings only.
4. Un-slug before any text comparison.
5. Dubai KB is a secondary source — our 572 taxonomy is the source of truth.
6. Human corrections (review_required=True) become the most valuable asset over time.

---

## Future V2

- Restaurant context (cuisine type → boosts certain std_terms)
- Active learning from corrected review_required rows
- Arabic dictionary expansion
- Supervised classifier trained on matched data
- Better ingredient inheritance using description_clean
