# TF-IDF + kNN Under the Hood

How the Talabat pipeline actually assigns `std_term`, `city`, and listing
matches — the mechanics, the measured numbers, and the traps we hit.

Every figure here comes from a run report or a held-out test in this repo, not
from theory. Where something was measured and rejected, it is recorded so it is
not retried blind next month.

---

## Where kNN appears

Three places, and it matters that they are **not the same algorithm**. Two
different feature spaces are in play — one built from text, two from geography.

| # | Job | Feature space | Index | k | File |
|---|---|---|---|---|---|
| 1 | menu item → `std_term` | TF-IDF sparse vectors | brute-force sparse matmul | 5 | `August_menu/map_std_terms.py` |
| 2 | coordinates → `city` | raw lat/lng | `scipy.spatial.cKDTree` | 3 | `August_menu/fill_cities.py` |
| 3 | new listing → existing branch | lat/lng in radians | `sklearn.neighbors.BallTree`, haversine | 5 | `map_version2/New_Restro/match_new_listings.py` |

The word "kNN" is doing very different work in each. Only #1 involves TF-IDF at
all; #2 and #3 are spatial lookups where the "distance" is physical metres.

---

# Part 1 — TF-IDF kNN for `std_term`

## The problem

A menu item arrives as a short string plus a category:

```
item_key  "chicken shawarma sandwich"
category  "Sandwiches"
```

It needs one label from a **closed vocabulary** — 95 terms in the August
reference, 570 in the July-inherited set. The vocabulary is fixed; we are not
inventing labels, we are reproducing the ones a human reviewer already gave to
355,726 rows in `menu/Restaurant_Menu_With_Ingredients (2).xlsx`.

That framing is the single most important thing on this page. The task is
**not** "what food is this". It is:

> *Which label did the reviewer give to items that look like this one?*

Those two questions have different answers, and the gap between them is exactly
why the semantic methods lost.

## Tiering: kNN is the last resort, not the first

kNN is expensive and only ~76.5% accurate, so it runs on the leftovers. From
`menu_refresh/map_std_terms_august.py`, ordered by how much human judgement sits
behind each tier:

| tier | source | keys | rows | share of rows |
|---|---|---:|---:|---:|
| 1 | NDJSON — human-reviewed | 874 | 2,513 | 0.2% |
| 2 | July export — what Tech already holds | 310,061 | 1,158,911 | **93.0%** |
| 3 | Excel exact match | 10,026 | 12,013 | 1.0% |
| 4 | **TF-IDF kNN** | 41,967 | 72,725 | **5.8%** |

Tier 2 is doing the heavy lifting, and it exists for a correctness reason, not a
performance one: **an unchanged menu item must not silently acquire a different
`std_term` this month than it had last month.** Reusing July's label guarantees
month-over-month stability for anything that did not change.

In the earlier August-only run (`August_menu/data/mapping_report.json`) the
split was harsher — 46,034 exact keys against 164,467 kNN keys — but exact
matches still covered **60.2% of rows**, because the keys that match are the
common dishes that appear in hundreds of restaurants.

> **The asymmetry to remember:** exact matching covers a small share of *keys*
> but a large share of *rows*. Rare keys are rare precisely because few
> restaurants sell them.

## TF-IDF, precisely as configured

```python
# August_menu/map_std_terms.py:183-191
ref_text = [f"{n} || {c}" for n, c in zip(names, cats)]
q_text   = [f"{k} || {key_cat[k].most_common(1)[0][0]}" for k in todo]

vc = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3), min_df=3,
                     sublinear_tf=True, dtype=np.float32)
vw = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=3,
                     sublinear_tf=True, dtype=np.float32)

Xr = normalize(hstack([vc.fit_transform(ref_text),
                       vw.fit_transform(ref_text)]).tocsr())
Xq = normalize(hstack([vc.transform(q_text), vw.transform(q_text)]).tocsr())
```

Six decisions are encoded there. Each one earns its place.

### 1. `"{name} || {category}"` — the category is fused into the text

The category is not a separate feature or a filter. It is concatenated into the
same string, so its characters and words become part of the same vector.

This is worth **+18 accuracy points**:

| features | accuracy |
|---|---|
| name + category | **76.5%** |
| name only | 58.6% |

Why so large? `chicken` is desperately ambiguous on its own — it appears in
sandwiches, curries, grills, soups, pizzas. `|| Sandwiches` collapses most of
that ambiguity in one token. The `||` separator itself becomes a stable
high-frequency n-gram that appears in every document, so IDF drives its weight
to near zero and it costs nothing.

### 2. `analyzer="char_wb"` with `ngram_range=(2,3)`

Character n-grams, computed **within word boundaries** (`_wb`). For
`"karak chai"` the analyzer pads each word and slides a window:

```
' k', 'ka', 'ar', 'ra', 'ak', 'k ',  ' ka', 'kar', 'ara', 'rak', 'ak '
' c', 'ch', 'ha', 'ai', 'i ',        ' ch', 'cha', 'hai', 'ai '
```

This is what makes the method robust to how menus are actually written.
`shawarma` / `shawrma` / `shwarma` share nearly all their 3-grams, so they land
in the same neighbourhood without a spell-checker, a synonym list, or an
embedding model. Arabic transliteration varies wildly across 15,000
restaurants; character n-grams absorb that variance for free.

`char_wb` rather than plain `char` matters: it refuses to build n-grams that
straddle a space, so `"beef burger"` never generates `"f b"`. Cross-word noise
would otherwise let unrelated items match on their spacing patterns.

### 3. `analyzer="word"` with `ngram_range=(1,2)`

Unigrams and bigrams. This block supplies what characters cannot: **word
order and multi-word identity**. `"chicken burger"` and `"burger chicken"` have
nearly identical character profiles but different bigrams. Terms like
`red velvet`, `soft drink`, `ice cream` are single concepts, and the bigram
captures that a character n-gram would shred.

### 4. `hstack([...])` — the two blocks are concatenated, not averaged

```
[ char_wb features ][ word features ]
```

One vector per item, spanning both blocks, L2-normalised **jointly** after
stacking. Measured on the real reference:

```
char block :  18,090 features
word block : 117,059 features
combined   : 135,149 features
```

The word vocabulary is 6.5× larger — there are far more distinct words and
bigrams across 355,726 menu items than there are 2–3 character sequences, which
are bounded by the alphabet. But **per item** the ratio inverts:
`chicken shawarma sandwich || Sandwiches` emits **57 character n-grams and only
7 word terms**.

So the two blocks contribute differently rather than one dominating:

- **char_wb** — many low-precision features per item. Dense, partial-credit
  signal that degrades gracefully across spelling variants.
- **word** — few high-precision features per item, each with high IDF because
  the vocabulary is large and each term is rare. Sharp, near-binary evidence.

Character n-grams decide *how close* things are; word terms decide *what counts
as the same thing*.

### 5. `sublinear_tf=True` — dampen repetition

Term frequency becomes `1 + log(tf)` instead of raw `tf`. In a string like
`"chicken chicken chicken pizza"`, the third `chicken` should not count as much
as the first. On menu items — which are short — this mostly guards against
items that repeat a word for emphasis or padding.

### 6. `min_df=3` — drop hapax noise

A feature must appear in at least 3 reference documents to get a column. Across
355,726 rows this discards a long tail of one-off typos and SKU fragments that
would otherwise create spurious exact-match spikes. It also keeps the
vocabulary — and therefore the matrix — tractable.

### Why `normalize(...)` is what makes this fast

After L2 normalisation every row vector has length 1. For unit vectors:

```
cosine(a, b) = (a · b) / (||a|| ||b||) = a · b
```

The division disappears. **Cosine similarity becomes a plain dot product**,
which means the entire neighbour search is one sparse matrix multiply — a
single highly-optimised BLAS-style operation instead of a per-pair distance
function in Python. This one line is the difference between minutes and hours.

## The neighbour search

```python
# August_menu/map_std_terms.py:195-213
for i in range(0, Xq.shape[0], CHUNK):          # CHUNK = 250
    S = (Xq[i:i + CHUNK] @ Xr.T).toarray()      # 250 × 355,726 similarities
    top = np.argpartition(-S, KNN_K, axis=1)[:, :KNN_K]
    for j in range(S.shape[0]):
        order = top[j][np.argsort(-S[j, top[j]])]
        sim = float(S[j, order[0]])
        vote, agree = Counter(labels[o] for o in order).most_common(1)[0]
        ing = next((ings[o] for o in order if labels[o] == vote and ings[o]), "")
```

**Why chunked at 250.** The full similarity matrix would be
`164,467 × 355,726` float32 ≈ **234 GB**. Chunking at 250 queries keeps each
`.toarray()` at roughly `250 × 355,726 × 4 bytes ≈ 356 MB` — large but
survivable on a box with under 1 GB spare. `CHUNK` is a pure memory dial; it
does not affect results.

**Why `argpartition` and not `argsort`.** Sorting 355,726 similarities per query
is `O(n log n)` and we only need the top 5. `argpartition` is `O(n)` — it
guarantees the k best land in the first k slots without ordering the rest. The
tiny `argsort` on the next line then orders just those 5. On 164,467 queries
this is the difference between a coffee break and an afternoon.

**Ingredient inheritance** (line 205) is a detail worth noting: the item adopts
ingredients from the nearest neighbour that *actually carries the winning term*
— not simply the nearest neighbour. Without that `labels[o] == vote` condition,
an item labelled `Biryani` by majority vote could inherit a sandwich's
ingredients from a closer but out-voted neighbour.

## A real trace, end to end

Four items pushed through the exact code path. Not illustrative — this is the
actual reference file and the actual vectorisers.

**Stage 1 — what the analyzers emit** for
`'chicken shawarma sandwich || Sandwiches'`:

```
char_wb(2,3) -> 57 n-grams
  ' c'  ' ch'  ' s'  ' sa'  ' sh'  ' |'  ' ||'  'a '  'an'  'and'  'ar'  'arm'
  'aw'  'awa'  'ch'  'ch '  'che'  'chi'  'ck'  'cke'  'dw'  'dwi'  'en'  'en '

word(1,2) -> 7 terms
  chicken · chicken shawarma · sandwich · sandwich sandwiches · sandwiches
  shawarma · shawarma sandwich
```

Note `sandwich sandwiches` — a bigram straddling the `||` separator, fusing the
item name to its category. That is the +18 points made visible.

**Stage 2 — the matrix**

```
matrix    (355726, 135149)
density   0.0536%   (25,787,559 non-zeros)
dense     192 GB float32   ->   sparse 208 MB
||q||     1.000000  after normalize  (so q @ r == cosine)
```

A 923× reduction from sparsity alone. This is why the whole thing runs on a
laptop with no GPU.

**Stage 3 — neighbours and votes**

```
QUERY  'chicken shawarma sandwich' || Sandwiches
  0.906  sandwich    shawarma sandwich
  0.885  sandwich    spicy chicken shawarma sandwich
  0.873  sandwich    large chicken shawarma sandwich
  0.841  sandwich    big chicken shawarma sandwich
  0.823  sandwich    saaj chicken shawarma sandwich
  -> 'sandwich'  5/5  sim 0.906  ACCEPT

QUERY  'karak chai' || Hot Beverages
  0.738  hot & cold beverages    lemon chai
  0.726  hot & cold beverages    kesar chai
  0.700  hot & cold beverages    mahrajan chai
  0.699  hot & cold beverages    karachi karak chai
  0.669  hot & cold beverages    ginger chai
  -> 'hot & cold beverages'  5/5  sim 0.738  ACCEPT
```

`karak chai` is the instructive one. The nearest neighbour is `lemon chai` — a
*different drink*. The method never claims otherwise; it claims that items whose
strings look like this were labelled `hot & cold beverages`, and it is right.
Five different chai variants, five identical labels. Similarity 0.738 is
mediocre, but agreement is perfect, which is exactly the case where the vote
signal outperforms the distance signal.

### The finding that explains the non-food problem

The fourth query was run deliberately:

```
QUERY  'white roses bouquet' || Flowers
  0.909  marketing/non-standard menu    white roses bouquet-1
  0.889  marketing/non-standard menu    41pcs white roses bouquet
  0.825  marketing/non-standard menu    31pcs white roses bouquet
  0.798  marketing/non-standard menu    100 white roses bouquet
  0.784  marketing/non-standard menu    12 white roses bouquet
  -> 'marketing/non-standard menu'  5/5  sim 0.909  ACCEPT
```

**The kNN gets bouquets right — 5/5, sim 0.909.** The reference file already
contains correctly-labelled bouquets.

So the 2,370 non-food items found in the July deliverable (roses labelled
`Makhaniya Biscuit`, mugs labelled `Bulbul Sweet`) did **not** come from this
classifier. They came from **tier 2** — the 310,061 keys inherited verbatim from
the July export, which the classifier never saw. Tier 2 exists to guarantee
month-over-month label stability, and it faithfully preserved errors along with
everything else.

That reframes the fix: those items do not need new logic or an LLM. They need
to be *demoted out of tier 2* and run through the kNN tier that would have
labelled them correctly in the first place. (Based on one probe, not a full
evaluation — worth validating on a sample of the 2,370 before relying on it.)

## Confidence: vote agreement beats similarity

This was the most useful empirical finding of the whole exercise, and it was not
in the original plan. Two candidate confidence signals were measured against a
2,000-row holdout:

| agreement | share | accuracy | | cosine sim | accuracy |
|---|---:|---:|---|---|---:|
| 5/5 | 42.4% | **97.5%** | | ≥ 0.8 | 85.7% |
| 4/5 | 18.8% | 84.0% | | 0.7–0.8 | 77.9% |
| 3/5 | 18.0% | 64.7% | | 0.6–0.7 | 71.4% |
| 2/5 | 16.2% | **38.0%** | | < 0.5 | 41.0% |

**How many of the 5 neighbours agree predicts correctness far better than how
close the nearest one is.** The spread on agreement is 97.5% → 38.0%; on
similarity it is only 85.7% → 41.0%.

The intuition: a high cosine score says "something in the reference looks like
this string". Five independent neighbours agreeing says "this *region* of the
reference is labelled consistently" — which is a statement about the labelling,
and the labelling is what we are trying to reproduce.

### The accept rule

```python
review_required = not (agree >= 4 and sim >= 0.60)
```

Both conditions, not either. This keeps **57.1% of items at 93.8% accuracy**;
the flagged 42.9% carries roughly **85% of all errors**. That concentration is
what makes a targeted human review affordable — you review 43% of the rows and
catch the large majority of the mistakes.

In the refresh run this flagged 37,911 rows out of 1,246,162.

## Everything else we measured — and rejected

Do not retry these blind. Every number is held-out accuracy on the same data.

| method | accuracy | verdict |
|---|---:|---|
| **TF-IDF kNN, name + category** | **76.5%** | shipped |
| TF-IDF kNN, name only | 58.6% | category is worth +18 pts |
| + description (both sides) | 74.2% | prose dilutes the item name |
| + ingredients (reference side only) | 72.9% | asymmetric — the query has none |
| OpenAI `text-embedding-3-small` | 62.3% | ties at k=1, loses at k=5, ~100× slower |
| HF `multilingual-e5-small` | 56.8% | worse and slowest |
| `gpt-4o-mini` direct classification | 42.5% | knows food, not our conventions |
| `rapidfuzz` `token_set_ratio` | — | 22 hours **and** wrong |

### Why the semantic methods lost

Menu names are short keyword strings, not sentences. Embeddings are trained to
compress strings into *meaning* — and meaning is precisely what misleads here.
Asked about `hummus dish`, an LLM answers `appetizer`. Defensible! But the
reference says `hummus`, because that is the convention the reviewer used.

Character n-grams match `shawarma` to `shawarma` because they share characters,
with no opinion about what shawarma *is*. For "reproduce the reviewer's label",
having no opinion is an advantage.

The `+ description` result is the same lesson from the other direction. Adding
descriptions *lowered* accuracy from 76.5% to 74.2% — a paragraph of marketing
prose swamps the handful of characters that actually identify the dish.

### The `rapidfuzz` trap — read this before reaching for fuzzy matching

`token_set_ratio` scores `"butter chicken biryani"` against `"butter"` at
**100**. A perfect score. The metric treats a token *subset* as a complete
match, so every multi-word dish matches its own shortest ingredient at full
confidence.

This is the flaw behind the bad mappings in the Postgres `std_term` set
(`Spicy Loaded Fries` → `spicy dry`), and it is why we use the Excel's 95
human-reviewed terms rather than the DB's 572. The two agree on 94.1% of shared
keys; where they disagree, the Excel is the consolidation and the DB is machine
noise.

---

# Part 2 — Coordinate kNN for `city`

Same algorithm family, completely different feature space. No TF-IDF here:
the features are literally latitude and longitude.

```python
# August_menu/fill_cities.py
MAX_KM = 15.0
K = 3

tree = cKDTree(X)                                     # X = labelled lat/lng
dist, idx = tree.query(Q, k=K)                        # Q = unlabelled coords
km = dist[:, 0] * 111
preds = [Counter(row).most_common(1)[0][0] for row in y[idx]]
```

A k-d tree partitions space by alternating axes, giving `O(log n)` lookups
instead of `O(n)` scans. `k=3` with a majority vote — the same
neighbours-agree logic as the text version.

`dist[:, 0] * 111` converts degrees to kilometres (~111 km per degree of
latitude). It is an approximation that overstates east-west distance at UAE
latitudes by roughly 10%, which is acceptable because it is used only against a
15 km threshold, never reported as a measurement.

## The result, and why the labels were everything

| method | accuracy |
|---|---:|
| **kNN k=3 on same-listing labels** | **99.45%** |
| kNN on brand-level Google labels | 72–81% |
| kNN on July's labelled coordinates | 69–79% |
| geometric rule on coordinates | 62% |
| `gpt-4.1-mini` given the coordinates | **47.2%** |

Identical algorithm, 69% → 99.45% purely from **label provenance**. Every losing
variant trained on labels that did not belong to the coordinate they were paired
with:

- **July's labels** — `city` is parsed from address *text* while `geo` comes
  from Talabat. They disagree badly: **100% of July's "Al Ain" rows sit more
  than 40 km from Al Ain.**
- **Google's labels** — `google_maps_details.jsonl` is keyed by *brand*, so
  every branch of a chain inherits whichever outlet Google returned first.
- **The 3,628 verified-location records** — address *and* lat/lng come from the
  **same Google listing**, so label and position agree by construction. Median
  distance to a labelled neighbour: **0.10 km**.

> The lesson generalises past this pipeline: when a kNN underperforms, suspect
> the labels before the algorithm. We changed no hyperparameter to go from 69%
> to 99.45%.

### Why the LLM lost, and it is not stupidity

It was handed the coordinates and explicitly told to trust them, and still
answered from the **name**: it placed `King Faisal St` (25.3884, 55.4521 —
plainly Ajman) in Sharjah, and `Al Jimi - Slemi` (24.2452, 55.7530 — Al Ain) in
Sharjah. Precise coordinate geometry is not what language models do. Cost $0.03
to establish; recorded so it is not re-tested.

### The guard

```python
filled = [(r, p) for r, p, k in zip(need, preds, km) if k <= MAX_KM]
```

If the nearest labelled point is more than 15 km away, `city` is left **NULL**.
A wrong emirate is worse than an absent one. Result: 99.9% filled, 10 rows left
null.

---

# Part 3 — Haversine BallTree for listing matching

The third variant, used to decide whether a newly-discovered listing is one we
already have:

```python
# map_version2/New_Restro/match_new_listings.py
EARTH_KM = 6371.0
GEO_MATCH_THRESHOLD_M = 100
K = 5

tree = BallTree(talabat_rad, metric="haversine")
dist, idx = tree.query(new_rad, k=K)
dist_m = dist * EARTH_KM * 1000
```

Two differences from `fill_cities`:

- **`BallTree`, not `cKDTree`** — k-d trees split on axis-aligned planes, which
  degrades on a curved surface. Ball trees use nested hyperspheres and support a
  true `haversine` metric, so distances are real great-circle distances rather
  than the flat 111 km/degree approximation.
- **Coordinates in radians** — `haversine` requires it; passing degrees yields
  silently wrong distances rather than an error.

Here the neighbours are not voted on. The distance is a hard gate: within 100 m
it is a candidate for the same branch, and the name comparison decides. This is
kNN as a *blocking* step — cheaply reducing 15,000 candidates to 5 before an
expensive comparison, rather than as a classifier.

---

# Operating notes

**Runtime.** Vectorising 355,726 reference rows takes ~1–2 minutes. The kNN
itself ran ~56 minutes for 164,467 keys in the August batch, ~17 minutes for
41,967 keys in the refresh. `--reuse-mapping` exists in
`map_std_terms_august.py` specifically so a failed *write* does not force you to
pay the kNN cost again — a lesson learned the hard way after a transaction error
discarded 1,009 seconds of completed work.

**Cost.** Zero. No API calls, no GPU. That is not incidental: the two paid
alternatives were *less* accurate.

**Reuse compounds.** `std_term_mapping.json` is keyed by `item_key`. Each month
joins against the accumulated mapping first, so the expensive tier shrinks over
time — August inherited 60.2% of rows free; the refresh inherited 93.0%. The
kNN tier is a shrinking tail, not a fixed cost.

**Determinism.** Given the same reference file and the same inputs, output is
identical. There is no sampling, no temperature, no model version to drift.
For a deliverable regenerated monthly and diffed against last month, that
property is worth more than a few points of accuracy.

## Honest limitations

- **76.5% top-1 is not good enough to ship unreviewed.** It is good enough to
  *rank* work for review, which is how it is used. The accept rule is the real
  product, not the classifier.
- **The reference is the ceiling.** kNN can only reproduce labels it has seen.
  A genuinely novel dish has no neighbour and gets the nearest wrong answer with
  low agreement — correctly flagged, but still wrong until reviewed.
- **`min_df=3` can hurt rare-but-real terms.** A legitimate dish appearing twice
  in the reference contributes no features of its own.
- **Vote agreement can be confidently wrong.** When five neighbours are all the
  same *mislabelled* reference item, agreement is 5/5 and the error ships
  unflagged. Agreement measures consistency of the labelling, not its
  correctness — if the reference is consistently wrong about something, so is
  the output, at maximum confidence.
- **Inheritance propagates errors silently.** Tier 2 is 93% of rows and is never
  re-examined. That is deliberate and correct for month-over-month stability,
  but it means a bad label entering the export once persists indefinitely — the
  classifier never gets a chance to disagree. The 2,370 non-food items are
  exactly this failure mode, not a classifier failure.

---

*Source files: `August_menu/map_std_terms.py`,
`menu_refresh/map_std_terms_august.py`, `August_menu/fill_cities.py`,
`map_version2/New_Restro/match_new_listings.py`.
Reports: `August_menu/data/mapping_report.json`,
`menu_refresh/data/std_term_report_august.json`.*
