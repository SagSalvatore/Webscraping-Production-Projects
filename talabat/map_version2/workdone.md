# UAE Restaurant Directory Project — Work Done

## The Goal

Build a complete, accurate business directory for all **15,768 Talabat restaurant branches** in the UAE by matching them against real Google Maps data — getting each one's address, phone, website, rating, and review count — and along the way, discover which Talabat "restaurants" are actually **multi-location chains** operating more branches in the UAE than Talabat's own listing shows.

Starting point: `apify/Complete_list_apify.csv` — 15,768 raw branch rows (branch_id, restaurant_id, restaurant_name, map_url, lat/lon, area_id, area_name, cuisine), representing **10,257 unique restaurant names** after cleaning duplicates and formatting quirks out of the raw names.

---

## Phase 1 — Google Maps Matching via Apify

**Approach:** For every unique restaurant name, run a search query through Apify's `compass/crawler-google-places` actor and match the returned Google Maps listings back to the Talabat name.

**Key technique — the query format.** A bare name like "Wanderlust" often returned zero useful Google results — too short/generic. Appending a fixed suffix fixed this reliably:
```
"{Restaurant Name} UAE Restaurant"
```
This became the proven format used everywhere in the project from that point on.

**Key technique — avoiding false matches.** Early runs matched wrong businesses (e.g. "Bao Kitchen" → "Dragon Bao Bao Restaurant"). Fixed with an **ordered-prefix token match**: strip stopwords from both the Talabat name and the candidate Google listing's title, then require the listing to literally *start with* the Talabat name's tokens in order. This became the core matching algorithm reused everywhere downstream (Apify matching, the later Tavily recovery phase, and platform scraping).

**Known-chain handling.** A 46-brand allowlist (McDonald's, Starbucks, Subway, etc.) got a higher search cap (150 vs. default 30) since large chains have many real locations to find.

**Batches run:** 14 checkpointed batches through the main Apify account, concurrency scaled from 5 groups up to 30 groups (bumping concurrency turned out to be cost-neutral — Apify bills per result, not compute time — so we pushed it purely for speed, cutting a batch from ~44 minutes down to ~15-19 minutes).

**Bugs found and fixed along the way:**
- **Data-loss bug:** dataset downloads had no retry logic; a transient network timeout silently dropped 2,400 places from one batch. Fixed with a 4-attempt exponential-backoff retry wrapper.
- **Fused Talabat names:** ~19 restaurant names had a data-quality quirk like `"The MonkinMeadows,UAE"` (missing space before "in"). Found via a full-dataset regex scan and fixed with a dedicated cleaner.
- **Allowlist duplication bug:** 9 known-chain names got accidentally duplicated across batches, which silently displaced 10 *other* real chains (Pizza Hut, Tim Hortons, Applebee's, PF Chang's, Papa John's, Peet's Coffee, Texas de Brazil, Cold Stone Creamery, Itsu, Brew At Home by Dunkin) — they were never searched at all. Decision: handle these 10 separately later via their own official websites, since they're well-known enough to have one.
- **Cumulative summary undercounting:** the reconciliation script's list of processed batch folders was initially missing the very first batch, undercounting "tested" names by ~1,246 and making the "remaining" count look wrong. Caught by cross-checking totals, then fixed.

**A DIY scraping detour:** Before settling on Apify at scale, we tried a custom Playwright + residential-proxy scraper against Google Maps directly, hoping to skip Apify's per-place cost. It worked, but Google Maps' own JS bundle made every page load pull down **~4.5MB** — at bulk scale (100 names ≈ 900MB-1GB) the bandwidth cost made it worse than Apify, not better. Used it for only 200 names (2 batches) before abandoning it in favor of scaling the Apify pipeline instead.

**End state of Phase 1:** 9,608 unique names tested via Apify+DIY combined, leaving:
- **5,145 unique names ("Not Found")** — searched, no confident Google Maps match
- **447 unique names ("Not Searched")** — the pipeline stopped here before reaching them

---

## Phase 2 — Deliverables & First Reconciliation (`map/` folder)

Built a master join of the original 15,768 branches against every result gathered so far (Apify + DIY), producing one row per branch (or one row *per matched Google location* for confirmed chains — a name matched to 3 real locations produces 3 rows, which is exactly the "hidden chain" signal the project needed).

This surfaced **49 Talabat names confirmed as real multi-location chains** in the UAE (top ones by location count: Aseer Time — 11, B Cafe and Restaurant — 11, Attibassi Coffee — 9, Art of Dum — 7).

Also built a **bonus directory**: every *other* business Apify's searches turned up that isn't in Talabat's own list at all — a separate discovery output, filtered down by category keywords to genuine food-service businesses only (dropping retail stores, wholesalers, hotels, gyms that happened to show up in results).

Split everything into clean deliverable files: confirmed-matched branches, not-found branches, not-yet-searched branches, and the bonus listings — each carrying `branch_id`/`restaurant_id` so it can be joined straight back to the original Talabat data.

---

## Phase 3 — Recovering the "Not Found" Bucket (`tav/` folder)

Google Maps' own index didn't have a listing for these 5,145 names — so re-running the same kind of search wouldn't help. The next idea: use general web search (Tavily) plus an LLM (GPT-4o-mini) to find whatever *does* exist for these businesses online — an official website, a Zomato/Deliveroo/noon Food listing, anything with a real address.

### Architecture built
- Async, concurrent pipeline: Tavily search → GPT-4o-mini structured extraction, running many names in parallel
- A round-robin pool across 18 Tavily API keys, with automatic blacklisting of any key that gets rate-limited
- `tenacity` retry with exponential backoff + jitter on every external call
- Full `loguru` logging throughout
- Crash-safe checkpointing: every result appended to disk immediately, a name-level "done" list saved regularly so an interrupted run resumes exactly where it left off
- A hard, code-enforced **cost ceiling** — the run checks its own cumulative OpenAI spend before touching each new name and stops cleanly (without marking incomplete work as done) if it would cross the ceiling
- A dedicated pytest suite (grew to 37 tests: fast unit tests on pure logic, plus smoke tests hitting the real APIs) that had to pass before every scale-up, per an explicit test-before-production requirement

### The query format
Same lesson as Phase 1: dropped an over-engineered query (`"{name}" restaurant {area} UAE address phone contact`) in favor of the proven bare `{Name} UAE Restaurant` format — recovery rate roughly doubled once this was corrected.

### The hallucination bugs — this was most of the real work
Every one of these was caught by spot-checking real output, not by inspection of the code:

1. **Landmark name collisions.** A Talabat delivery-zone label ("Zayed Sports City") also happens to be a real stadium complex's name. The search surfaced the stadium's own contact page, and the model reported the *stadium's switchboard number* as the restaurant's phone — the same number ended up attached to 12 different, unrelated small businesses in one batch. Fixed with two guardrails: the business's own name must actually appear in the search results (not just the LLM's summary of them), and any phone number shared across multiple unrelated business names gets flagged and discarded.

2. **Fabricated placeholder phone numbers.** A suspiciously round number, `+971 4 123 4567`, turned up attached to 14 different unrelated businesses in one run — the model was inventing a plausible-*looking* UAE number when it didn't actually have one. Fixed with a detector for sequential-digit and repeated-digit patterns.

3. **Fabricated Google Maps links.** The model invented `maps.google.com/place/Name/@lat,lng` URLs with made-up coordinates — two different businesses were even given the exact same fake latitude. Fixed by only trusting a Maps URL that's a real Google Maps domain *and* literally appears in the raw search results; otherwise falling back to Google's own official search-deep-link format (`/maps/search/?api=1&query=...`), which needs no coordinates and can't be faked.

4. **Talabat's own listing mistaken for independent confirmation.** For businesses with no other web footprint, the model was treating Talabat's own delivery page as if it were external proof the business exists — and reporting Talabat's internal order-rating widget as if it were a Google rating, and Talabat's vague delivery-zone label as if it were a street address. Fixed: a match only counts if at least one *non-Talabat* source actually names the business.

5. **Wrong branch for multi-location brands.** For a brand with several real branches (e.g. one in Al Barsha, another in Deira, another in Motor City), the model would confidently report *one* of those addresses even when it didn't match the specific branch being searched for. Fixed: only report a specific address if it matches the listed area, or if there's only one candidate address at all; otherwise flag it as `"Chain"` — a confirmed real brand, address needing manual follow-up — rather than guessing.

6. **Over/under-correcting the "Chain" logic.** Tightening rule #5 initially overshot — the model started treating *any* uncertainty as a flat "not found" instead of using the Chain flag, which is a real and useful outcome, not a miss. Took two calibration passes to land on: pick the one best-supported address when there's only one; use "Chain" only when there are genuinely multiple different addresses with no way to tell which applies.

7. **The big one — weak name-matching let wrong businesses through.** The rule requiring "half the name's significant words" to appear in a matched listing was too forgiving for short 2-3 word names: a single shared generic word ("delights", "chai", "club", "acai") was enough to pass. Re-checking a sample: **376 of 679 (55%) of one batch's platform matches were a different, unrelated business** that merely shared one common word — "Fuji Delights" matched to "Foodies Delights", "Chai Samosa" to "Chai Wala Cafe", "The Kain Club" to "The Club Restaurant". Fixed by requiring *all* significant words to match for short names, and an absolute floor of at least 2 matching words for longer ones. Every match across the whole 5,145-name production run was then re-checked against this corrected rule.

### Platform scraping (Zomato / Deliveroo / noon Food)
Used only as a fallback when the main web search didn't already succeed (an earlier version was checking these platforms unconditionally — even for names that already had a perfect match via their own official website — which was pure waste and occasionally introduced a wrong result). Zomato URLs are pre-filtered to real UAE city pages (dubai, abudhabi, sharjah, ajman, fujairah, ras-al-khaimah, umm-al-quwain, al-ain) before ever being touched, since Zomato also operates in India, the UK, etc. and a same-named business there is not this business.

Zomato's pages could be read directly (no browser needed — the address/phone/rating are in a structured `ld+json` block server-rendered into the raw HTML). Deliveroo and noon Food both actively block plain requests, so their URLs are collected for possible manual/future use but couldn't be automatically resolved this way.

### Production run
All 5,145 names run end to end, checkpointed in 500-name chunks, total OpenAI cost **$2.29**.

---

## Final Reconciliation (`map_v2/` folder)

Joined the original 15,768-branch source of truth against both pipelines' final, corrected results.

| Status | Branches | % |
|---|---:|---:|
| **Matched** — real address/phone/website found | **11,116** | 70.5% |
| via the Apify/Google Maps pipeline | 7,101 | |
| via the Tavily web-search/Zomato recovery pipeline | 4,015 | |
| **Chain** — confirmed real brand, specific branch address not determinable | **171** | 1.1% |
| **Not Found** — checked both pipelines, genuinely no footprint found | **3,827** | 24.3% |
| **Not Searched** — never reached by either pipeline | **635** | 4.0% |
| **Handled separately** — KFC + Domino's (19 branches), own dedicated scrape | **19** | 0.1% |
| **Total** | **15,768** | 100% |

Of the 11,116 matched branches: **99.4% have an address, 81.0% have a phone number, 68.2% have a website.**

**Bonus discovery:** 18,806 additional real UAE food-service businesses turned up during the searches that aren't in Talabat's own list at all — a separate directory of potential expansion targets.

---

## What We Didn't Achieve

- **3,827 branches (24.3%)** remain genuinely unrecoverable — no Google Maps listing, no independent website, and no discoverable Zomato/Deliveroo/noon presence. Most of these are likely small, informal operations (home kitchens, tiny stalls) with no public web footprint of any kind to find.
- **635 branches (4.0%)** were never searched — the Apify pipeline's batches ran out before reaching the last stretch of names.
- **10 major chains** (Pizza Hut, Tim Hortons, Applebee's, PF Chang's, Papa John's, Peet's Coffee, Texas de Brazil, Cold Stone Creamery, Itsu, Brew At Home by Dunkin) were skipped due to the allowlist duplication bug — left for manual handling via their own official websites rather than re-running the whole batch pipeline for 10 names.
- **Deliveroo and noon Food** listings couldn't be scraped automatically (both block plain web requests) — their URLs were collected during the run but need a different scraping approach to actually pull structured data from them.
- **Website coverage sits at 68.2%** even among matched businesses — a real fraction of small UAE food businesses simply operate through delivery apps only, with no independent website to find.

---

## What We Found New

- **49 + additional Talabat names are genuine multi-location chains** with more UAE branches than Talabat's own listing shows for that name — the core "hidden chain" insight the whole project was built to surface.
- **18,806 real UAE food-service businesses** discovered that aren't on Talabat at all, from the same search work — a ready-made expansion/prospecting list.
- **Zomato listings carry clean, structured data for free** (address, phone, rating, review count) via a plain HTTP request reading their `ld+json` markup — no browser automation needed, confirmed against real listings.
- **A single shared generic word is not enough to confirm two business names are the same place** — this was the single highest-impact lesson of the whole recovery phase, discovered by catching a 55% false-positive rate and fixing the matching logic project-wide as a result.
