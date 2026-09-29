# UAE F&B Market Intelligence Report
## Dubai Full Coverage + UAE Expansion Strategy

> **Generated:** 2026-05-26 (updated)  
> **Current database:** 999 unique Dubai F&B venues (16 areas × 4 search terms)  
> **Target:** ~13,000 Dubai · ~24,800 full UAE  
> **Recommended plan:** Starter $29/month + credit top-ups

---

## 1. The Most Important Concept: Search Terms vs CATEGORY_MAP

Before discussing areas and budget, this distinction must be clear because it affects every
decision about coverage and cost.

### What we actually searched Google Maps with (4 terms)

```
"restaurants"            → Full-service dining
"fast food"              → QSR / quick-serve chains
"cafes and bakeries"     → Coffee shops, patisseries
"food delivery kitchen"  → Cloud / ghost kitchens
```

That is the **only thing controlling what venues we found**. Google returns its
top-ranked results for each query in each area.

### What CATEGORY_MAP is (and isn't)

`CATEGORY_MAP` is a **post-processing label** — it maps the 100+ different Google
category names (like `"Hyderabadi restaurant"`, `"Cake shop"`, `"Shared-use commercial kitchen"`)
to our 8 internal buckets (Full-Service, QSR, Bakery, etc.) **after the data has already arrived**.

- Adding entries to `CATEGORY_MAP` → classifies existing data better → zero new venues
- Adding entries to `CATEGORIES` (search terms) → Google returns different venues → new data

### Why this matters for the 13K target

We have 999 venues from 4 search terms × 16 areas × max 15–40.
Dubai has ~13,000 F&B outlets total. The gap is entirely explained by:

| Gap | Size | Fix |
|---|---|---|
| Only 16 of 55 areas covered | 39 missing areas | Add remaining 39 areas |
| Only 4 of 12 needed search terms | 8 missing term types | Add 8 new search terms |
| Low max per query (15–40) | Misses long tail | Raise to max=60 |

---

## 2. Dubai Area Coverage — 55 Areas Total

Your research identified **55 meaningful Dubai F&B areas** in 5 groups.
We have scraped 16 (basic coverage only). Here's the full map:

### Group 1 — High-Density Old Dubai & Street Food Hubs (12 areas)

*Massive QSR + FSR volume, budget-friendly, South Asian and Arab cuisine heavy*

| Area | Status | Expected Venues |
|---|---|---|
| Al Rigga, Deira | ❌ New | ~55 |
| Naif, Deira | ❌ New | ~50 |
| Hor Al Anz, Deira | ❌ New | ~45 |
| Al Muteena, Deira | ❌ New | ~45 |
| Abu Hail, Deira | ❌ New | ~40 |
| Al Karama | ✅ Done (4 terms) | 50 in DB, ~70 total |
| Al Mankhool, Bur Dubai | ❌ New | ~50 |
| Al Hamriya, Bur Dubai | ❌ New | ~40 |
| Oud Metha | ❌ New | ~45 |
| Al Rafaa, Bur Dubai | ❌ New | ~35 |
| Al Satwa | ❌ New | ~60 |
| Al Jafiliya | ❌ New | ~45 |

> We scraped "Deira" and "Bur Dubai" as single broad areas before. The new granular sub-areas
> (Al Rigga, Naif, Hor Al Anz, Al Mankhool, Al Hamriya, Al Rafaa) each get their OWN search —
> returning venues that wouldn't rank in a broad "Deira" query.

### Group 2 — High-Density Expat Commuter Belts (14 areas)

*Massive QSR + delivery volume, price-sensitive, large South/Southeast Asian population*

| Area | Status | Expected Venues |
|---|---|---|
| International City Phase 1 | ❌ New | ~35 |
| International City China Cluster | ❌ New | ~25 |
| International City France Cluster | ❌ New | ~25 |
| Al Nahda 1 | ❌ New | ~55 |
| Al Nahda 2 | ❌ New | ~50 |
| Al Qusais 1 | ❌ New | ~45 |
| Al Qusais 2 | ❌ New | ~45 |
| Al Qusais 3 | ❌ New | ~40 |
| Jumeirah Village Circle (JVC) | ❌ New | ~55 |
| Discovery Gardens | ❌ New | ~35 |
| Al Furjan | ❌ New | ~40 |
| Mirdif | ❌ New | ~60 |
| Al Warqa 1 | ❌ New | ~40 |
| Muhaisnah | ❌ New | ~40 |

### Group 3 — Premium Commercial & Tourist Hubs (9 areas)

*Fine dining + international chains + high-end bakeries — highest revenue venues*

| Area | Status | Expected Venues |
|---|---|---|
| Downtown Dubai | ✅ Done (4 terms) | 142 in DB, ~200 total |
| Business Bay | ✅ Done (4 terms) | 111 in DB, ~165 total |
| DIFC | ❌ New | ~80 |
| Dubai Marina | ✅ Done (4 terms) | 93 in DB, ~145 total |
| Jumeirah Beach Residence (JBR) | ❌ New | ~75 |
| Palm Jumeirah | ❌ New | ~90 |
| Dubai Hills Estate | ❌ New | ~65 |
| City Walk | ❌ New | ~60 |
| Bluewaters Island | ❌ New | ~40 |

> ⭐ DIFC, Palm Jumeirah, JBR and City Walk are the highest-priority new areas.
> These are among Dubai's top dining destinations and zero venues from them are in our database.

### Group 4 — Mid-Tier Residential & Freezones (12 areas)

*Mixed FSR, QSR, cafes — large volume from office workers and residents*

| Area | Status | Expected Venues |
|---|---|---|
| JLT Cluster A-M | ✅ Done partial (incomplete) | 33 in DB |
| JLT Cluster N-Z | ❌ New (separate half of JLT) | ~30 |
| Al Barsha 1 | ✅ Done (4 terms) | 60 in DB, ~85 total |
| Al Barsha 2 | ❌ New | ~40 |
| Barsha Heights (Tecom) | ✅ Done (4 terms) | 52 in DB, ~75 total |
| Dubai Silicon Oasis | ✅ Done (4 terms) | 59 in DB, ~80 total |
| Motor City | ✅ Done (4 terms) | 58 in DB, ~80 total |
| Sports City | ❌ New | ~35 |
| Production City (IMPZ) | ❌ New | ~30 |
| Studio City | ❌ New | ~35 |
| Arjan | ❌ New | ~30 |
| Meydan | ❌ New | ~35 |

### Group 5 — Industrial Zones (8 areas)

*The cloud kitchen capital of Dubai — dark kitchens, catering, commissaries*

| Area | Status | Expected Venues |
|---|---|---|
| Al Quoz Industrial Area 1 | ❌ New (sub-zone) | ~30 |
| Al Quoz Industrial Area 2 | ❌ New (sub-zone) | ~30 |
| Al Quoz Industrial Area 3 | ❌ New (sub-zone) | ~30 |
| Al Quoz Industrial Area 4 | ✅ Done (4 terms only) | 54 in DB, ~75 total |
| Dubai Investment Park 1 | ✅ Done (4 terms only) | 55 in DB, ~75 total |
| Dubai Investment Park 2 | ❌ New (sub-zone) | ~30 |
| Jebel Ali Industrial Area | ❌ New | ~25 |
| Ras Al Khor Industrial Area | ❌ New | ~30 |

### Dubai area count summary

| Group | Total Areas | Done | New | Expected New Venues |
|---|---|---|---|---|
| High-Density Old Dubai | 12 | 1 (partial) | 11 | ~540 |
| Expat Commuter Belts | 14 | 0 | 14 | ~590 |
| Premium Hubs | 9 | 3 (partial) | 6 | ~410 |
| Mid-Tier Residential | 12 | 5 (partial) | 7 | ~235 |
| Industrial Zones | 8 | 2 (partial) | 6 | ~175 |
| **Total** | **55** | **7 full / 7 partial** | **~39 new** | **~1,950 new** |

Re-running done areas with 8 new search terms adds ~750 more venues from known areas.  
**Grand total estimate: 999 existing + 1,950 new + 750 re-run = ~3,700 Dubai venues from 55 areas.**

---

## 3. The 12 Search Terms

### What we had (4 terms — used in Runs 1 & 2)

| Term | Captures |
|---|---|
| `restaurants` | Full-service dining, all cuisines |
| `fast food` | Western QSR chains, burgers, pizza |
| `cafes and bakeries` | Coffee shops, patisseries |
| `food delivery kitchen` | Explicitly self-declared cloud kitchens |

### 8 new terms to add (total = 12)

| Term | Why it's needed | What it captures that "restaurants" misses |
|---|---|---|
| `indian restaurants` | Largest single cuisine segment in UAE (28% population is South Asian) | Indian restaurants that don't rank in generic "restaurants" search |
| `juice bars and smoothies` | Dessert & Drinks venues don't show up in restaurant results | Standalone juice bars, smoothie counters |
| `dessert shops and ice cream` | Growing segment, separate search intent | Ice cream parlours, dessert cafes |
| `shawarma and kebab` | Middle Eastern QSR is enormous but doesn't rank in "fast food" | Hundreds of shawarma shops across Dubai |
| `brunch restaurants` | Brunch is a major Dubai dining category | Brunch-specific venues targeting weekend diners |
| `pizza restaurants` | Large enough segment to have own search intent | Pizza chains and independents not ranking in "fast food" |
| `sushi restaurants` | Fast-growing segment | Japanese / fusion sushi venues |
| `food trucks` | Emerging, often not listed as "restaurant" | Mobile F&B, pop-up food parks |

### Impact of 12 vs 4 search terms

```
4 terms  × 55 areas × 60 max = 13,200 gross → ~9,500 unique
12 terms × 55 areas × 60 max = 39,600 gross → ~28,500 unique
```

The additional 8 terms more than double the expected coverage.

---

## 4. Starter Plan ($29/month) — What It Covers and What It Doesn't

### Critical fact about Starter (and all plans)

> **The $0.004 per-venue scraping cost is FIXED on all Apify plans.**
> Plans do NOT discount this rate. The plan fee ($29/mo) buys you:
> - 32 concurrent runs (vs 25 on FREE) — 28% faster scraping
> - Better proxy allocation (30 IPs included vs none on FREE)
> - The $29 acts as your monthly credit balance

### What $29 buys in scraping volume

```
$29 / $0.004 per venue = 7,250 venues per month at zero additional cost
```

### Full Dubai cost breakdown

| What | Queries | Gross venues | Cost |
|---|---|---|---|
| Full 55 areas × 12 terms × max 60 | 660 | 39,600 | **$158.40** |
| Already scraped (999 venues, 4 terms, 16 areas) | — | — | $4.43 (paid) |
| **Remaining Dubai** | ~648 | ~38,900 | **~$155.60** |

### Starter plan budget options for Dubai

| Option | Monthly cost | Months to complete Dubai | Total spend |
|---|---|---|---|
| Starter + no top-up | $29/mo plan + $0 extra | ~6 months | $174 |
| Starter + $50 top-up each month | $79 first month | ~2 months | $108 |
| **Starter + $130 top-up once** | **$159 first month, $29/mo after** | **1 month** | **$159** |
| FREE plan + $160 credit purchase | $160 one-time, no subscription | 1 run | $160 |

> **Recommendation for completing Dubai:**  
> Buy the **Starter plan ($29)** for the platform benefits (32 concurrent runs, proxy allocation),  
> and top up with **$130 in additional credits** in the first month.  
> Total first month: **~$159**. After that, monthly refresh costs ~$20–30.

### Per-group batching strategy (to manage cash flow)

Instead of one $158 run, split by priority group:

| Group | Cost at max=60 | Priority | Cumulative spend |
|---|---|---|---|
| Premium (9 areas) | $25.92 | 🔴 Week 1 | $25.92 |
| High-Density Old Dubai (12 areas) | $34.56 | 🔴 Week 1–2 | $60.48 |
| Expat Belts (14 areas) | $40.32 | 🟡 Week 2–3 | $100.80 |
| Mid-Tier (12 areas) | $34.56 | 🟡 Week 3 | $135.36 |
| Industrial (8 areas) | $23.04 | 🟢 Week 4 | $158.40 |

Each group runs in ~5–7 minutes. Run one group per week with a $29 credit balance
top-up each time (4 top-ups × $29 = $116 + $29 plan = $145 total — slight saving vs lump sum).

---

## 5. Full UAE Expansion After Dubai

### UAE coverage map

| Emirates | Est. Areas | Key Cities | Est. Venues | Est. Cost |
|---|---|---|---|---|
| Dubai (55 areas) | 55 | Dubai | ~13,000 | $158.40 |
| Abu Dhabi | ~25 | Abu Dhabi city, Al Ain, Al Reem, Yas | ~7,000 | $72.00 |
| Sharjah | ~12 | Sharjah city, Muwaileh, Industrial | ~3,000 | $34.56 |
| Ajman | ~5 | Ajman city | ~600 | $7.20 |
| Ras Al Khaimah | ~5 | RAK city, Al Hamra | ~600 | $7.20 |
| Fujairah | ~4 | Fujairah city | ~400 | $5.76 |
| Umm Al Quwain | ~3 | UAQ city | ~200 | $4.32 |
| **UAE Total** | **~109 areas** | — | **~24,800** | **~$289.44** |

### Abu Dhabi priorities (high value, add after Dubai)

- Al Khalidiyah (dense, expat-heavy)
- Al Reem Island (premium, growing fast)
- Yas Island (tourism, F&B clusters)
- Saadiyat Island (premium dining)
- Corniche area (tourist and resident)
- Mussafah Industrial (cloud kitchens)
- Khalifa City A & B (suburban residential)
- Al Mushrif / Al Muroor (mid-tier residential)

---

## 6. Apify Plan Decision — Final Recommendation

### The plans (actual names from Apify)

| Plan | Monthly Fee | Concurrent Runs | Proxy Included | Best For |
|---|---|---|---|---|
| FREE | $0 | 25 | None | Testing, one-time builds |
| **Starter** | **$29** | **32** | **30 IPs** | **Ongoing UAE coverage** ← |
| Scale | $199 | 128 | 200 IPs | MENA-wide (Saudi, Egypt, etc.) |
| Business | $999 | 256 | 500 IPs | Enterprise, SLA required |
| Enterprise | Custom | Unlimited | Custom | Dedicated infrastructure |

### For your specific use case

**Q: Is Starter ($29) enough for covering Dubai and UAE?**

| Scenario | Answer |
|---|---|
| **Full Dubai in one run (55 areas, 12 terms, max=60)** | Starter plan + $130 top-up = $159 one-time |
| **Full UAE in one run** | Starter plan + $260 top-up = $289 one-time |
| **Monthly Dubai refresh (new openings only)** | $29 plan credit easily covers (~$15–25/month) |
| **Monthly UAE refresh** | $29 plan + ~$20 top-up = ~$49/month |
| **Is Starter plan worth it vs FREE?** | Yes — 32 vs 25 concurrent runs = 28% faster |

### The $29 plan does NOT cover full Dubai alone

The $29/month plan credit covers **7,250 venues** (at $0.004 each). Full Dubai (55 areas × 12 terms × max=60) generates **~28,500 unique venues** — that's **$158.40** in scraping cost. The Starter plan gets you $29 of that. You need to top up the remaining $129.40.

### Scale ($199) vs Starter ($29) — when does Scale make sense?

```
Scale saves on proxy bandwidth:  $7.50/GB vs $8.00/GB = 6% saving
Scale adds 96 more concurrent runs (128 vs 32)

For UAE alone: Scale plan costs $170/month MORE than Starter.
To break even, you'd need 128 vs 32 concurrent runs to matter —
i.e., scraping so many queries that time-to-complete is a real bottleneck.

UAE (109 areas × 12 terms = 1,308 queries) completes in ~61 min on Starter (32 parallel).
UAE completes in ~15 min on Scale (128 parallel).

→ Unless speed is critical (daily refreshes), Starter is sufficient for UAE alone.
→ Buy Scale ONLY when expanding to Saudi Arabia, Egypt, Kuwait — MENA scale needs the throughput.
```

### Final answer

| Decision | Choice | Cost |
|---|---|---|
| **Platform plan** | **Starter ($29/month)** | $29/mo |
| **Initial Dubai build** | **Starter + $130 credit top-up** | $159 one-time |
| **Initial UAE build** | **Starter + $260 credit top-up** | $289 one-time |
| **Monthly refresh (UAE)** | **Starter plan credit covers it** | ~$29–49/mo |
| **Upgrade to Scale when** | Saudi/Egypt/Kuwait added | If/when MENA expands |

---

## 7. Execution Plan

### Phase 1 — Complete Dubai (4 weeks, ~$159 total)

```bash
# Week 1 — Premium hubs (DIFC, Palm, JBR, City Walk, Bluewaters) + re-run Downtown/Marina/Bay with 8 new terms
python apify/generate_input.py --group premium --max-results 60
python apify/run_scraper.py
python apify/process_results.py --skip-seen
# Cost: ~$26   New venues: ~1,000

# Week 2 — High-density Old Dubai (Al Rigga, Naif, Al Satwa, Oud Metha etc.)
python apify/generate_input.py --group high_density --max-results 60
python apify/run_scraper.py
python apify/process_results.py --skip-seen
# Cost: ~$35   New venues: ~2,200

# Week 3 — Expat commuter belts (International City, Al Nahda, JVC, Mirdif etc.)
python apify/generate_input.py --group expat_belts --max-results 60
python apify/run_scraper.py
python apify/process_results.py --skip-seen
# Cost: ~$40   New venues: ~2,800

# Week 4 — Mid-tier + industrial (Al Barsha 2, JLT N-Z, Arjan, Al Quoz 1-3, DIP2, Jebel Ali)
python apify/generate_input.py --group mid_tier --max-results 60
python apify/run_scraper.py
python apify/process_results.py --skip-seen
python apify/generate_input.py --group industrial --max-results 60
python apify/run_scraper.py
python apify/process_results.py --skip-seen
# Cost: ~$58   New venues: ~1,500
```

**After Phase 1: ~8,500 Dubai venues · $158 total scraping cost**

### Phase 2 — Abu Dhabi (week 5–6, ~$72)

```bash
# Add Abu Dhabi areas to generate_input.py (25 areas)
# New CLI flag: --city abu-dhabi
python apify/generate_input.py --city abu-dhabi --max-results 60
python apify/run_scraper.py
python apify/process_results.py --skip-seen
# Expected: ~7,000 new venues
```

### Phase 3 — Remaining Emirates (week 7, ~$58)

```bash
# Sharjah, Ajman, RAK, Fujairah, UAQ
python apify/generate_input.py --city sharjah --max-results 60
python apify/generate_input.py --city northern-emirates --max-results 60
# Expected: ~4,800 new venues
```

### Phase 4 — Monthly refresh

```bash
# After full build, re-run monthly with --skip-seen
# Only new venues (opened since last scrape) get added
# Monthly cost: ~$20–40 depending on re-scrape scope
python apify/run_scraper.py          # uses latest apify_input.json
python apify/process_results.py --skip-seen
```

---

## 8. Total Investment Summary for Management

| Item | Cost | Venues | Timeline |
|---|---|---|---|
| Phase 0 (done) — 16 Dubai areas, 4 terms | $4.43 | 999 | Done |
| Phase 1 — 55 Dubai areas, 12 terms | **~$158** | ~8,500 | 4 weeks |
| Phase 2 — Abu Dhabi | **~$72** | ~7,000 | Week 5–6 |
| Phase 3 — Other Emirates | **~$58** | ~4,800 | Week 7 |
| **One-time UAE build total** | **~$293** | **~20,300** | **7 weeks** |
| **Starter plan (12 months)** | **$348/yr** | — | Ongoing |
| **Monthly refresh (est. avg)** | **~$35/mo** | New openings | Monthly |
| **Year 1 total** | **~$676** | **~20,300 UAE venues** | — |

**Cost per UAE F&B venue: $676 / 20,300 = $0.033 — 3.3 US cents per venue**

---

*Report v3 — updated 2026-05-26. Based on actual scrape data (999 venues, 2 runs)
and empirical Apify PAY_PER_EVENT pricing ($0.004/place, fixed across all plans).*
