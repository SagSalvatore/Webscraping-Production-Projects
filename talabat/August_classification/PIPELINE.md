# Restaurant Classification Pipeline

Fills three columns for a batch of UAE restaurants. Built August 2026 for the
7,244 `genuinely_new_brand` restaurants; designed to be re-run each month
against a new batch by pointing `config.RESTAURANTS` at the new file.

| column | values |
|---|---|
| `restaurant_type` | Full-Service Restaurants \| Quick-Service Restaurants \| Cafes \| Bakery \| Cloud Kitchen |
| `outlet_type` | Chain \| Independent |
| `chained_outlet_type` | Local Chain \| MNC Chain \| N/A |

---

## The rule that drives everything

**Chain status is judged by Google Maps presence, not by Talabat branch count.**

A client looking a brand up on Google Maps and seeing 2–3+ locations regards it
as a chain. That is the standard the pipeline matches.

```
distinct same-brand UAE locations on Google Maps
    >= 2  ->  Chain        ->  Local Chain | MNC Chain
    <= 1  ->  Independent  ->  chained_outlet_type = "N/A"
```

**No exception, from September 2026 (Sagar).** `coerce()` used to keep the
model's `Chain` at exactly 1 location whenever it had also filled in a sub-type
— but the model fills that in whenever it says Chain, so the gate never rejected
anything, and its "if the web says so" justification was never met: Tavily only
runs for footprints of ≥2. September had **109 single-pin "chains" of 158**.
Enforcing the rule gave Chain **44** / Independent **1,003**.

**Forward only.** August's shipped numbers stand and are not re-derived.

**The rule is re-asserted in `write_final`, not just `coerce`.** The LLM cache
holds the already-coerced record, so a regenerate never calls `coerce` again —
without this, changing the rule looks like it did nothing.

Two things qualify the count itself, and both are part of the definition rather
than hygiene — either one can manufacture a chain out of nothing:

* **Every counted location must be inside the UAE.** A bounding box on the
  coordinates, plus a hard `SystemExit` before either Serper stage writes.
* **The brand must be the title's LEADING name.** Containment alone counted
  other businesses as branches — `Juice Center` collected Haji Ali, Bombay Star
  and Mumbai Masti Juice Center, three unrelated shops reported as one 8-branch
  chain. Words *after* the brand are branch/legal suffixes and stay allowed;
  words *before* it mean a different business.

Talabat's `branch_id` count is deliberately **not** the signal. Talabat lists
**12 of KFC's 247 real UAE locations** — it counts delivery listings, not
footprint, so it under-reports chains badly.

`chained_outlet_type` is **derived, never asked of the model**. Independent ⇒
`N/A` mechanically. The previous run let the model emit it free-form and the
output carried `'N/A '` ×135 and `'Local Chain '` ×4.

---

## Stages

```
rules.py           stage 1  FREE   cuisine + menu-profile hints, prior reuse
serper_places.py   stage 2  ~free  Google Maps footprint + enrichment
tavily_fallback.py stage 3  free   narrative evidence, CHAINS ONLY
classify_llm.py    stage 4  $1.16  gpt-4.1-mini decides, then derives
```

### Stage 1 — `rules.py`
No API calls. Produces hints, not verdicts (except prior reuse).
* **Menu profile** — 506,575 items with `std_term`/`taxonomy` for these exact
  restaurants. What a place sells beats a web snippet: ≥50% `beverage` ⇒ Cafes,
  dessert/bakery-dominant ⇒ Bakery.
* **Cuisine rules** — bakery cuisines >50% ⇒ Bakery; beverage/dessert cuisines
  with no blocker (`indian`, `biryani`, `grills`…) ⇒ Cafes; cloud-kitchen name ⇒
  Cloud Kitchen.
* **Prior reuse** — `tavily/FINAL_CLASSIFY.csv` (15,768 rows) by brand name.

### Stage 2 — `serper_places.py`
One `/places` query per **distinct brand name**, 1 credit each.
MEASURED: 6,715 brands in **10 min**, concurrency 30 at 40 qps, zero failures.

**Query template is load-bearing:** `"{name} uae restaurant"` scored **15/15**
on a labelled set vs **11/15** for `"{name} UAE"`. Adding `restaurant` makes
Google resolve the business rather than word-match — it finds real branches
(Ravi Restaurant 1 → 7) *and* removes false positives (Sour Mango 6 → 1).
Do not "simplify" it.

Three filters, each needed because raw counts are wrong without it:
1. **EXACT whole-word name match — never fuzzy.** `token_set_ratio>=85` scored
   17/23 on a labelled set and inflated Chain by ~30%: it matched
   "The One Restaurant" to "A One Restaurant" and "THE OBA RESTAURANT", and
   "Kathmandu Restaurant" to Kathmandu Darbar/Palace/Sayapatri — separate
   businesses sharing a city name. Exact containment scores **21/23**.
   `&` is normalised to `and` first (recovers 50 brands); connectives
   "the"/"of" are NOT dropped, because that makes "The One Restaurant" match
   "A One Restaurant".
2. **Arabic stripped before comparison** — `token_set_ratio("Karak Basha",
   "كرك باشا") ≈ 11`, so Arabic titles look like different brands. An
   Arabic-only title counts when it is the sole result.
3. **Non-UAE dropped** — `gl=ae` biases but does not restrict. Karak Basha's
   only exact match is in **Bahrain**; another result was in **Oman**.

Serper **caps at 10** places (`num` is ignored), so 10 means "≥10" — still
unambiguously a chain.

**Enrichment, same call, no extra cost:** `latitude`, `longitude`, `cid` →
`https://www.google.com/maps?cid=...`, `category` (Google's own business type —
an independent cross-check on `restaurant_type`), `rating`, `ratingCount`.
`address`, `phone` and `website` are **best-effort** — on a 4-branch sample,
address was present on 1 of 4 and phone/website on none. Fill rates are
reported at the end of every run; do not promise coverage.

### Stage 3 — `tavily_fallback.py`
**Run with `--only-reason chain_needs_mnc_vs_local`.** Two triggers exist, but
only one is worth paying for:

| trigger | Aug count | does Tavily change the answer? |
|---|---|---|
| `chain_needs_mnc_vs_local` | 624 | **Yes** — the only evidence for MNC vs Local |
| `no_maps_presence` | 3,571 | **No** — Independent either way by the Maps rule |

Searching the 3,571 would have taken ~1.7 h to change nothing.

**Concurrency 4, not 12.** At 12 throughput collapsed to ~0.2/s and every key
got rate-limited. At 4: 584 brands in **9.9 min**, 606 × 429 absorbed by
backoff, **0 keys retired**, 0 failures.

**Preflight drops only 401/403.** A 429 means rate-limited *right now*, not
dead. An earlier version dropped any non-200 and declared "every tavily key is
dead" after a burst, refusing to run with 18 healthy keys.
**5 of 23 keys are genuinely dead** (401); 18 usable.

Query is `"{name} uae restaurant"` — the **same** template as Serper, so both
sources describe one entity. (The older `tavily/reclass_low_confidence.py` used
`"{name} Dubai"` because the UAE phrasing pulled generic market-research
articles. On the August run all 814 snippets came back brand-specific, so the
shared template held — but if snippets ever look generic, this is the first
thing to revisit.)

### Stage 4 — `classify_llm.py`
`gpt-4.1-mini`, batches of 30, concurrency 12 (tier 2 allows 4,300 RPM, so the
model is never the bottleneck).

Every response is validated and coerced:
* invalid `restaurant_type` → falls back to the rule hint
* `outlet_type` disagreeing with the Maps count → **overridden by the evidence**
* brand in the MNC seed → forced `MNC Chain`
* Chain with no sub-type → `Local Chain` (documented default)

Assertions before writing: no Independent without `N/A`, no Chain without a
sub-type, no whitespace-padded values. All three held on the August run.

**Gate 3 is REVIEW-ONLY from September 2026.** It asks for
`operates_outside_uae` as a fact, but for unfamiliar brands it invents one.
Measured across two months: **11 promotions, 1 upheld** (Kyan Cafe). August gave
"Happy Restaurant → Bulgaria" and three brands claiming foreign operations while
reporting `origin=unknown`. September gave `GRATEFUL CAFE`, whose own reasoning
read *"Cafe Gratitude is a US-based chain"* — a different brand entirely.

Sagar's test is **prominence**: MNC means KFC/McDonald's-tier. So a gate-3 hit
now ships as **Local Chain** and is written to `mnc_claims_for_review.csv` with
the model's origin, `operates_outside_uae` and `reasoning` — that last column is
the only place a brand-confusion fabrication is visible.

**Only the MNC seed list or a human entry in `config.CHAIN_TYPE_OVERRIDE` can
set MNC Chain.** Both are re-asserted as invariants in `write_final()`, not only
in `coerce()`, because the LLM cache stores already-coerced records — a record
written under an older rule would otherwise survive a regenerate untouched.
To promote a reviewed brand: add it to `CHAIN_TYPE_OVERRIDE` and regenerate,
which costs **$0.00**.

---

## Running it

Run in this order. Each stage caches to `data/` and resumes, so re-running never
re-bills — proved on the August run: a full `serper_places.py` re-invocation
made **0 queries**.

```bash
python test_pipeline.py                 # 59 offline tests, no spend
python test_pipeline.py --live          # + ~3 real calls

python rules.py                         # stage 1, free, prints hint coverage
python serper_places.py --dry-run       # shows balance + planned spend
python serper_places.py --limit 100     # small live test
python serper_places.py                 # full, ~10 min

python tavily_fallback.py --dry-run     # shows the split by trigger
python tavily_fallback.py --only-reason chain_needs_mnc_vs_local   # ~10 min

python classify_llm.py --dry-run        # cost estimate + sample payload
python classify_llm.py --limit 60       # one small batch, ~$0.01
python classify_llm.py                  # full, ~11 min, writes final outputs
```

**Then review the MNC list before shipping** and record decisions in
`config.CHAIN_TYPE_OVERRIDE`:

```bash
python -c "import json;[print(r['restaurant_name'],r['google_locations_uae'],r['country_of_origin']) for r in map(json.loads,open('data/restaurants_classified.jsonl',encoding='utf-8')) if r['chained_outlet_type']=='MNC Chain']"
```

Re-run `classify_llm.py` afterwards — it regenerates from cache for **$0.00**
and applies the overrides.

**Watching a run:** the log prints every 100 brands (Serper/Tavily) or 10
batches (LLM), so gaps of several minutes are normal and do not mean a stall.
Check liveness by the cache file's timestamp instead:

```bash
jq 'length' data\tavily_cache.json; (Get-Item data\tavily_cache.json).LastWriteTime
```

---

## ACTUAL costs and timings (August 2026: 7,244 restaurants / 6,715 brands)

| stage | volume | time | cost |
|---|---|---|---|
| rules | 7,244 | instant | **$0.00** |
| Serper | 6,715 credits of 358,431 (1.9%) | 10 min | **~$0.00** |
| Tavily | 584 chains, 18 live keys | 9.9 min | **$0.00** |
| gpt-4.1-mini | 241 batches | 10.8 min | **$1.16** |
| **total** | | **~35 min** | **$1.16** |

## August results

Independent **5,956** / Chain **1,288** (Local 1,286, MNC 2).
FSR 3,296 · Cafes 1,657 · QSR 1,447 · Bakery 840 · Cloud Kitchen 4.
Confidence 4,907 High / 2,290 Medium / 47 Low.
Plus `google_places_enrichment.jsonl` — 4,419 Google Maps locations with geo,
Maps URL, category and rating (address 12%, phone 5%, website 3% — best-effort).

---

## Expected shape of the output

**Almost everything will be Independent or Local Chain.** Zero of the 6,683
brands match the 28 known MNCs — structural, since these are
`genuinely_new_brand` and by definition not McDonald's or Starbucks. The MNC
seed stays in as a safety net and for future batches containing established
brands.

For reference, the previous 15,768-restaurant run produced FSR 6,187 / QSR
5,539 / Cafes 2,682 / Bakery 891 / Cloud Kitchen 469, and Independent 12,563 vs
Chain 3,205. Expect this batch to skew further toward Independent.

---

## September 2026 — the monthly re-run, worked

```bash
python ../september/build_classification_input.py     # CSV -> the JSONL shape
python test_pipeline.py --cohort sep                  # 99 tests, no spend
python rules.py            --cohort sep
python serper_places.py    --cohort sep               # 1,047 credits, 0.8 min
python serper_maps_details.py --cohort sep --all-brands   # 1,047 credits, 2.4 min
python tavily_fallback.py  --cohort sep --only-reason chain_needs_mnc_vs_local
python classify_llm.py     --cohort sep               # $0.16, 2.5 min
python ../september/join_google_details.py            # per-branch enrichment
```

`--cohort sep` writes to `data_sep/` and is bound in `config.py` before the
constants, so a bare run still reproduces August. The Serper and Tavily
**caches stay in `data/`** and are shared — repeat brands cost nothing.
`PRIOR_CLASSIFY` is a list, read in order, later files winning.

Result: Independent 1,003 / Chain 44 (Local 43, MNC 1). FSR 415 · Cafes 290 ·
QSR 204 · Bakery 137 · Cloud Kitchen 1. Prior reuse **0 of 1,047** — independent
confirmation the dedup was right.

### `/maps` beats `/places` for enrichment, at the same price

Same API, same credit, same query — `/places` returns the search *card* (8
fields), `/maps` the *listing* (19 fields, up to 20 results):

| | `/places` | `/maps` |
|---|---|---|
| address | 12% | **99.6%** |
| phone | 5% | **94.4%** |
| website | 3% | **41%** |
| place_id · opening_hours · price_level | — | 100% · 86% · 40% |

Run it monthly with `--all-brands`. Join the details back to a **branch** by
nearest coordinate, never by brand alone — a brand holds several locations and
brand-only joining hands a Dubai branch the Sharjah phone number. September's
median match distance was **0.01 km**; beyond `MAX_KM` the row is left empty.

## Re-running next month

1. Point `config.RESTAURANTS` at the new batch file.
2. Point `config.MENU_ITEMS` at that month's mapped menu output.
3. Add the current month's `FINAL_*` to `PRIOR_CLASSIFY` so brands already
   decided are reused free.
4. Keep the caches — `serper_places_cache.json` and `tavily_cache.json` are
   keyed by brand name, so repeat brands cost nothing.
5. Run the tests first. The regression cases encode real bugs; if one fails,
   something upstream changed.
