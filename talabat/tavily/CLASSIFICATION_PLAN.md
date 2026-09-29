# Talabat Restaurant Classification Pipeline — Plan

**Goal:** Fill 3 columns for ~15,532 unclassified restaurants  
**Output:** JSON + CSV (no DB writes at this stage)  
**Date:** 2026-06-22

---

## 1. What We're Filling

| Column | Possible Values | Currently Filled | Need to Fill |
|--------|----------------|-----------------|-------------|
| `restaurant_type` | Full-Service Restaurants, Quick-Service Restaurants, Cafes, Bakery, Cloud Kitchen | 241 (already classified) | **15,532** (currently "Restaurant") |
| `outlet_type` | Independent, Chain | 44 | **15,729** |
| `chained_outlet_type` | Local Chain, MNC Chain, N/A | 35 | **15,738** |

**Key insight from DB:**
- 15,532 restaurants have `restaurant_type = 'Restaurant'` (raw Talabat label, not classified)
- 10,294 unique names → 5,238 duplicate names = definite Chain candidates
- Starbucks (62), McDonald's (47), Subway (35) are the top chain counts

---

## 2. Resources Available

| Resource | Capacity |
|----------|----------|
| Tavily API keys | 23 keys × ~1,000 searches/month = **~23,000 searches** |
| OpenAI budget | **$5** (GPT-4o-mini @ $0.15/1M in + $0.60/1M out → covers ~30,000 restaurants easily) |
| Input data | `restaurant_name`, `ld_name`, `serves_cuisine[]`, `key_cuisines[]` from DB |

---

## 3. Three-Stage Classification Strategy

### Stage 1 — Rule-Based Pre-Classification (FREE, No API)

Run locally against the 15,532 names + their cuisine tags. Reduces API calls by ~40-50%.

#### 3.1.A  outlet_type — Name Frequency Rule
```
Count branches per restaurant_name in our DB:
  ≥ 2 branches → outlet_type = Chain
  = 1 branch   → PENDING (needs Tavily verification)
```
- Estimated instant Chain labels: **~5,238 restaurants**
- Remaining to verify via Tavily: ~10,294 (single-name restaurants)

#### 3.1.B  restaurant_type — Cuisine Tag Rules
Apply in order, stop at first match:

```
IF serves_cuisine/key_cuisines contains any of:
  ["Coffee", "Cafe", "Tea", "Bubble Tea", "Juices", "Juice", "Smoothies",
   "Desserts", "Ice Cream", "Gelato", "Milkshake", "Shakes"]
  AND does NOT contain ["Indian", "Arabic", "Grills", "Rice", "Biryani"]
  → restaurant_type = Cafes

IF serves_cuisine/key_cuisines contains primarily:
  ["Bakery", "Pastries", "Cakes", "Bread", "Croissants"]
  (cuisine count for baked goods > 50% of total tags)
  → restaurant_type = Bakery

IF restaurant_name contains (case-insensitive):
  ["Cloud Kitchen", "Dark Kitchen", "Virtual Kitchen", "Delivery Only", "Delivery Kitchen"]
  → restaurant_type = Cloud Kitchen

ELSE → PENDING (needs Tavily + OpenAI)
```
- Estimated rule-based labels: ~1,500–2,000 restaurants
- Remaining for API pipeline: ~13,500

#### 3.1.C  chained_outlet_type — Known MNC Seed List
Cross-reference against a hardcoded global MNC list:
```python
KNOWN_MNC = {
    "McDonald's", "Starbucks", "Subway", "KFC", "Pizza Hut", "Burger King",
    "Tim Hortons", "Dunkin", "Shake Shack", "Five Guys", "Raising Cane's",
    "Wendy's", "Popeyes", "Domino's", "Papa John's", "Hardee's", "Baskin Robbins",
    "Dairy Queen", "Cinnabon", "Krispy Kreme", "IHOP", "TGI Fridays", "Chili's",
    "The Cheesecake Factory", "P.F. Chang's", "Nando's", "Wagamama", "Pret A Manger",
    "Costa Coffee", "Caribou Coffee", "Peet's Coffee", "% Arabica", "Paul Bakery",
    "Pinkberry", "Oakberry Acai", "Kcal", "Operation Falafel", "ALBAIK",
    "Juan Valdez", "Vapiano", "Wingstop", "Jollibee", "Taco Bell", ...
}

IF restaurant_name in KNOWN_MNC → chained_outlet_type = MNC Chain
IF outlet_type = Chain AND name NOT in KNOWN_MNC → chained_outlet_type = Local Chain
IF outlet_type = Independent → chained_outlet_type = N/A
```

---

### Stage 2 — Tavily Web Search (Per Remaining Restaurant)

For every restaurant NOT resolved by Stage 1 rules, fire one Tavily search.

#### Search Query Template
```
"{restaurant_name}" UAE restaurant
```
For outlet_type unknowns, append: `how many locations UAE`

#### What to Extract from Results
| Signal | How to Detect | Maps To |
|--------|--------------|---------|
| "delivery only", "no dine-in", "virtual brand" | Keywords in snippets | Cloud Kitchen |
| "coffee", "cafe", "tea house", "juice bar" | Name + snippet | Cafes |
| "bakery", "patisserie" | Snippet keywords | Bakery |
| "multiple locations", "branches in UAE", "X outlets" | Snippet count phrases | Chain |
| "only location", "one branch", Google Maps single pin | Snippet phrases | Independent |
| Global brand presence (UK, US, KSA etc.) | Snippet origin country | MNC Chain |
| UAE-only mention, local brand | Snippet geo references | Local Chain |

#### Key Rotation Logic
```
23 keys stored in list:  [key_0, key_1, ..., key_22]
current_key_index = 0

On each search:
  try: tavily.search(query, api_key=keys[current_key_index])
  on HTTP 429 / credits exhausted:
      current_key_index += 1
      if current_key_index >= 23: STOP — all keys exhausted
      retry with new key

Checkpoint saved every 100 searches → resume safely on restart
```

#### Cache Strategy
- Save every Tavily response to `data/tavily_cache/{branch_id}.json`
- On restart: skip if cache file exists → zero re-billing

---

### Stage 3 — OpenAI GPT-4o-mini Classification

Feed batches of 30 restaurants + their Tavily snippets into GPT-4o-mini.

#### Batch Prompt Structure
```
System: You are a foodservice market research analyst for UAE restaurants.
        Classify each restaurant using the decision tree provided.
        [Insert full prompt from restaurant_format_classification_prompt.md]

User:
Classify these 30 UAE restaurants. For each return JSON:
{
  "branch_id": <int>,
  "restaurant_type": "Full-Service Restaurants|Quick-Service Restaurants|Cafes|Bakery|Cloud Kitchen",
  "outlet_type": "Independent|Chain",
  "chained_outlet_type": "Local Chain|MNC Chain|N/A",
  "confidence": "High|Medium|Low",
  "reasoning": "<one line>"
}

Restaurants:
1. branch_id=12345 | Name: "Spice Garden" | Cuisines: Indian, Biryani, Grills
   Tavily: "Spice Garden is a traditional Indian restaurant in Al Barsha with dine-in..."
2. ...
```

#### Budget Estimate
```
15,532 restaurants ÷ 30 per batch = 518 batches

Per batch tokens:
  Input:  system prompt (~600) + 30 × avg 180 tokens (name+cuisine+snippet) = 6,000 tokens
  Output: 30 × ~80 tokens (JSON) = 2,400 tokens

Total:
  Input:  518 × 6,000 = 3.1M tokens × $0.15/1M = $0.47
  Output: 518 × 2,400 = 1.2M tokens × $0.60/1M = $0.75
  TOTAL:  ~$1.22  (well under $5 budget, ~$3.78 buffer)
```

---

## 4. Full Pipeline Flow

```
[DB] Pull 15,532 restaurants → restaurants.csv
         │
         ▼
[Stage 1] Rule-based pre-classification
   ├── outlet_type: name frequency (≥2 = Chain)
   ├── restaurant_type: cuisine tag rules
   └── chained_outlet_type: MNC seed list
         │
         ▼
[Stage 2] Tavily search for remaining ~13,500
   ├── 23 key rotation with auto-failover
   ├── Cache to data/tavily_cache/{branch_id}.json
   └── Extract signals per restaurant
         │
         ▼
[Stage 3] OpenAI GPT-4o-mini batch classification
   ├── 30 restaurants per batch
   ├── Input: name + cuisines + Tavily snippet
   ├── Output: restaurant_type, outlet_type, chained_outlet_type, confidence
   └── Save per-batch results immediately (crash-safe)
         │
         ▼
[Output] Merge all results
   ├── data/classified/classified_YYYYMMDD.json
   └── data/classified/classified_YYYYMMDD.csv
```

---

## 5. File Structure

```
talabat/tavily/
├── CLASSIFICATION_PLAN.md                         ← this file
├── restaurant_format_classification_prompt (1).md ← classification rules
│
├── classify_restaurants.py    ← main orchestrator (Stage 1 → 2 → 3)
├── tavily_client.py           ← key rotation + search + cache
├── rules_engine.py            ← Stage 1 rule-based pre-classification
├── openai_classifier.py       ← Stage 3 GPT-4o-mini batching
│
└── data/
    ├── input/
    │   └── restaurants_to_classify.csv    ← pulled from Supabase
    ├── tavily_cache/
    │   └── {branch_id}.json              ← one file per restaurant
    ├── classified/
    │   ├── classified_YYYYMMDD.json
    │   └── classified_YYYYMMDD.csv
    └── checkpoint.json                   ← tracks progress by branch_id
```

---

## 6. Output Schema (CSV + JSON)

| Column | Example |
|--------|---------|
| `branch_id` | 749114 |
| `restaurant_name` | La Saladita |
| `restaurant_type` | Quick-Service Restaurants |
| `outlet_type` | Independent |
| `chained_outlet_type` | N/A |
| `confidence` | High |
| `classification_source` | rule / tavily+openai / openai_only |
| `reasoning` | Healthy bowl concept, counter ordering, no table service mentioned |
| `classified_at` | 2026-06-22T10:00:00Z |

---

## 7. Estimated API Usage Summary

| Stage | Restaurants | Tavily Calls | OpenAI Tokens | Cost |
|-------|------------|--------------|--------------|------|
| Stage 1 (rules) | ~2,000–3,000 | 0 | 0 | $0 |
| Stage 2 (Tavily) | ~13,500 | 13,500 | 0 | 0 (API keys) |
| Stage 3 (OpenAI) | ~13,500 | 0 | ~4.3M | ~$1.22 |
| **Total** | **15,532** | **13,500** | **~4.3M** | **~$1.22** |

Tavily key consumption: 13,500 ÷ 23 keys = ~587 searches/key (well within 1,000/month limit)

---

## 8. Edge Cases & Rules

1. **Restaurant name has 1 branch in our DB but Tavily says "multiple UAE locations"** → Chain (Tavily overrides frequency rule)
2. **Restaurant name has 2+ branches but Tavily says "delivery only"** → Cloud Kitchen still valid; outlet_type = Chain still valid (they can be chained cloud kitchens)
3. **Conflicting signals (Tavily says dine-in, name says "Cloud Kitchen")** → Trust Tavily, flag with confidence = Low
4. **Tavily returns no results** → Classify with OpenAI only using name + cuisines; confidence = Low
5. **All 23 Tavily keys exhausted mid-run** → Continue with Stage 3 only (OpenAI uses just name + cuisine signals, marks source = openai_only)
6. **Bakery in `restaurant_type` classification** — Note: the prompt file uses FSR/QSR/Cafes/Cloud Kitchen. Bakery is a 5th category we've been using. We keep Bakery as a valid label (added to the prompt).

---

## 9. What's NOT in Scope (This Run)

- Writing results to Supabase (will do separately after review)
- `contact` column scraping (separate pipeline via Talabat detail pages)
- `INGREDIENTS` column (GPT-4o-mini from menu descriptions — separate task)
- `std_terms` standardized cuisine taxonomy (separate task)
- Other platforms (Zomato, Deliveroo, Noon, etc.)

---

## 10. Open Questions Before Starting

1. **Bakery category** — Keep as 5th `restaurant_type` value alongside FSR/QSR/Cafes/Cloud Kitchen? *(Yes/No)*
2. **Cafes label** — The prompt says "Cafes & Bars" but our DB uses "Cafes". Which to use? *(Keep "Cafes"?)*
3. **Tavily keys** — Are all 23 keys in the `.env` already or in a separate file? Where to load from?
4. **MNC list completeness** — Should we add UAE-specific global chains (e.g., Kcal, Wrap & Roll, etc.) to the seed list?
5. **Confidence threshold** — For Low confidence classifications, should we skip writing to DB and flag for manual review, or write them with a `low_confidence` flag?
