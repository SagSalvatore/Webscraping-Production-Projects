"""Stage 2 - Google Maps footprint per brand, via Serper /places.

One query per DISTINCT BRAND NAME (6,683, not 7,244 rows) returns, for 1 credit:
  * the count of distinct same-brand UAE locations  -> Chain vs Independent
  * geo, Google Maps CID, category, rating per location -> enrichment

Three filters are applied to the raw results, each because the raw count is
wrong without it:

 1. TITLE MATCH. Places returns fuzzy matches. "Sour Mango" came back with
    "Sour Bliss", "Mango Market DWC", "Mister Mango"; "Fair Wrap Cafeteria"
    returned 5 places, NONE of them the brand. Counting raw results would call
    single-outlet cafes chains.
 2. ARABIC TITLES. token_set_ratio("Karak Basha", "كرك باشا") ~= 11, so an
    Arabic-script title looks like a different brand. Arabic is stripped before
    comparison; an Arabic-only title is accepted when it is the sole result,
    since the query was specific enough to return just it.
 3. NON-UAE. gl=ae biases, it does not restrict. Karak Basha's only exact
    title match is in BAHRAIN and another result sat in OMAN.

Serper caps at 10 places regardless of `num` - 10 therefore means ">=10", which
is unambiguously a chain, so the cap costs us nothing.

    python serper_places.py --dry-run       plan + cost, no calls
    python serper_places.py --limit 50      small live test
    python serper_places.py                 full run
"""
import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter

import httpx
from loguru import logger
from rapidfuzz import fuzz

from config import (CHAIN_MIN_LOCATIONS, ENRICHMENT_OUT, LOGS, NON_UAE_TOKENS,
                    PLACES_OUT, SERPER_CACHE, SERPER_CONCURRENCY, SERPER_QPS,
                    SERPER_QUERY, SERPER_URL, TITLE_MATCH_MIN)
import os
from dotenv import load_dotenv
load_dotenv(__import__("config").ROOT / ".env", override=True)

AR = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
CHECKPOINT_EVERY = 200


def strip_arabic(s: str) -> str:
    return AR.sub("", s or "").strip(" -|,·")


def is_non_uae(addr: str) -> bool:
    a = (addr or "").lower()
    return any(t in a for t in NON_UAE_TOKENS)


# The UAE, generously boxed: mainland plus the islands and the full Musandam
# side of the coast. Nothing legitimate sits outside it.
UAE_BBOX = (22.4, 26.6, 51.0, 56.6)      # lat_min, lat_max, lon_min, lon_max


def outside_uae(lat, lon) -> bool:
    """Geo gate. Returns True only when coordinates EXIST and fall outside.

    WHY THIS EXISTS ALONGSIDE is_non_uae. NON_UAE_TOKENS is a blocklist of
    named countries, and a blocklist cannot enumerate the world: September's
    results included "2912 Sheppard Ave E, Scarborough, ON" (Canada),
    "921 E Colorado Blvd, Pasadena, CA", "Cebalat Ben Ammar, Tunisia",
    "Merzouga, Morocco", "Puncak Alam" (Malaysia) and "Brookvale NSW"
    (Australia) - not one of them carried a listed token, and all passed.
    `gl=ae` biases Google's results, it does not restrict them.

    Requiring a UAE token instead would be worse: plenty of genuine UAE
    listings give only a street ("Rahba Farm Plot No. 991", "113 Al Souq St"),
    and rejecting those would delete real branches.

    Coordinates settle it without a word list. Measured on September: 53 rows
    fell outside the box, every one genuinely foreign, and none of them carried
    a UAE token - so the two gates never disagree, this one just sees more.
    Every Serper result carried coordinates, but a missing pair returns False
    so the text gates still get their say.
    """
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    a, b, c, d = UAE_BBOX
    return not (a <= la <= b and c <= lo <= d)


# A one-word brand is matched by CONTAINMENT, so a common word swallows every
# business that happens to use it: "CHIPS" matched 10 fish-and-chip shops,
# "TOGETHER" matched "Better Together Cafe", "Leo" matched "Leo&Loona Kids
# Park". For a single-token brand the title must therefore START with it
# ("Kyoto burger" yes, "A Kyoto Sip" no). Multi-token brands are specific
# enough that containment anywhere in the title stays safe, which is what
# preserves the branch-suffix and Arabic-title cases the tests cover.
def _norm_amp_joined(s: str) -> str:
    """`&` DELETED with no space, so "Leo&Loona" -> "leoloona"."""
    s = AR.sub(" ", s or "").lower().replace("&", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_amp_split(s: str) -> str:
    """`&` becomes a SPACE and a standalone "and" is dropped, so the three
    spellings of one name collapse together: "Trigo & Taco", "TRIGO&TACO" and
    "Trigo and Taco" all reduce to "trigo taco"."""
    s = AR.sub(" ", s or "").lower().replace("&", " ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r" and ", " ", f" {s} ")
    return re.sub(r"\s+", " ", s).strip()


def _leads(b: str, t: str) -> bool:
    if not b or not t:
        return False
    if t == b or t.startswith(b + " "):
        return True
    # the Arabic definite article is optional in transliteration:
    # "Al Khafaief Cafeteria" is the brand "KHAFAIEF CAFETERIA"
    t2 = re.sub(r"^al ", "", t)
    return t2 == b or t2.startswith(b + " ")


def name_matches_strict(brand: str, title: str) -> bool:
    """The brand must be the title's LEADING name, not merely inside it.

    Sagar, Sept 2026: "the finding in name should be the exact match, it's not
    like different restaurants captured with the same name."

    Containment alone counts other people's businesses as your branches, and it
    is the single biggest source of invented chains. Measured on September:
        "Juice Center"          -> Haji Ali Juice Center, Bombay Star Juice
                                   Center, Mumbai Masti Juice Center - three
                                   unrelated shops, 8 "locations", one "chain"
        "Aldimashqi Restaurant" -> Alqasr / Alrukn / Jude Aldimashqi - three
                                   separate Damascene restaurants
        "Khorfakkan Restaurant" -> Hosun / Shatee Khorfakkan
        "Sagarmatha Restaurant" -> Nepaliko Sagarmatha Restaurant
        "GREEN TEA"             -> arabian green tea
    Words AFTER the brand are branch and legal suffixes and stay allowed
    ("...Muwaihat3", "...B1", "... LLC SP", "Sweets and Pastries Hili").
    Words BEFORE it make a different business, so the brand must come first.

    THE AMPERSAND CUTS BOTH WAYS, which is why arity decides the normalisation:
        multi-token brand: `&` is a SEPARATOR - "Trigo & Taco" is "TRIGO&TACO"
        single-token brand: `&` is a JOINER  - "Leo" is NOT "Leo&Loona Kids Park"
    Using one rule for both either loses Trigo & Taco or hands Leo a play park.
    """
    if not name_matches(brand, title):
        return False
    if len(norm_title(brand).split()) > 1:
        return _leads(_norm_amp_split(brand), _norm_amp_split(title))
    return _leads(_norm_amp_joined(brand), _norm_amp_joined(title))


def norm_title(s: str) -> str:
    """Lowercase, drop Arabic and punctuation, collapse spaces.

    `&` is expanded to `and` FIRST. Without it, a Talabat brand written
    "ghandoor cafeteria and pastery" failed to match its own Google listing
    "GHANDOOR Cafeteria & Pastery", because stripping `&` to a space broke the
    contiguous run. Recovered 50 brands at zero cost in false positives (12/12
    on the labelled set).

    Connectives are NOT dropped. Removing "the"/"of" recovered a few more but
    made "The One Restaurant" match "A One Restaurant" - both reduce to
    "one restaurant" - so the trade was rejected.
    """
    s = AR.sub(" ", s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_matches(brand: str, title: str) -> bool:
    """EXACT whole-word containment of the brand name in the title.

    Per Sagar: the name must match exactly, whatever else the title contains.
    Measured 21/23 on a labelled set vs 17/23 for token_set_ratio>=85, and it
    removes every false positive that fuzzy scoring let through:
        "The One Restaurant"   no longer matches "A One Restaurant",
                               "One to Ten Restaurant", "THE OBA RESTAURANT"
        "Kathmandu Restaurant" no longer matches "Kathmandu Darbar/Palace/
                               Sayapatri Restaurant" - separate businesses
                               that merely share a city name
    Still handles the real cases: branch suffixes ("...Muwaihat3", "...B1",
    "...& Pastries Hili"), legal suffixes ("... LLC SP"), and Arabic branch
    titles, because the Arabic is stripped before comparison
    ("كب اوف جوي - فرع اليحر cup of joy" contains "cup of joy").

    Known misses, both word-order variants: "Al Barq Restaurant & Cafeteria
    LLC" and "MALLAH AL AIRPORT ROAD". These UNDER-count a branch, which is the
    safe direction - we would call a chain Independent rather than invent one.
    """
    b, t = norm_title(brand), norm_title(title)
    if not b or not t:
        return False
    return re.search(r"(?:^| )" + re.escape(b) + r"(?:$| )", t) is not None


def match_places(brand: str, places: list) -> list:
    """Same-brand UAE locations only. See module docstring for why each gate."""
    keep = []
    for p in places:
        title = p.get("title") or ""
        addr = p.get("address") or ""
        if is_non_uae(addr) or outside_uae(p.get("latitude"), p.get("longitude")):
            continue
        if name_matches_strict(brand, title):
            keep.append(p)
        elif not strip_arabic(title) and len(places) == 1:
            # Arabic-only title and the query returned exactly one result:
            # specific enough to be the brand, but unmatchable in latin.
            keep.append(p)
    # distinct physical locations, not distinct titles - one brand can list
    # the same outlet twice under slightly different names
    seen, out = set(), []
    for p in keep:
        k = (round(p.get("latitude") or 0, 4), round(p.get("longitude") or 0, 4))
        if k in seen and k != (0, 0):
            continue
        seen.add(k)
        out.append(p)
    return out


def load_cache():
    if SERPER_CACHE.exists():
        try:
            return json.loads(SERPER_CACHE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("serper cache unreadable - starting fresh")
    return {}


def save_cache(c):
    tmp = SERPER_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    tmp.replace(SERPER_CACHE)


class Pacer:
    """Serper documents 100 req/s; stay under it by construction."""

    def __init__(self, qps):
        self.gap = 1.0 / qps if qps else 0
        self.slot = 0.0
        self.lock = asyncio.Lock()

    async def wait(self):
        if not self.gap:
            return
        async with self.lock:
            t = max(time.time(), self.slot)
            self.slot = t + self.gap
        d = t - time.time()
        if d > 0:
            await asyncio.sleep(d)


async def fetch_brand(client, brand, sem, pacer, cache, stats, key):
    if brand in cache:
        stats["cached"] += 1
        return
    q = SERPER_QUERY.format(name=brand)
    for attempt in range(4):
        await pacer.wait()
        async with sem:
            try:
                r = await client.post(SERPER_URL,
                                      headers={"X-API-KEY": key,
                                               "Content-Type": "application/json"},
                                      json={"q": q, "gl": "ae"}, timeout=30)
                if r.status_code == 200:
                    cache[brand] = {"query": q, "places": r.json().get("places", [])}
                    stats["ok"] += 1
                    return
                if r.status_code in (429, 500, 502, 503):
                    await asyncio.sleep(2 * (2 ** attempt))
                    continue
                stats[f"http_{r.status_code}"] += 1
                break
            except Exception as exc:
                stats[type(exc).__name__] += 1
                await asyncio.sleep(2 * (2 ** attempt))
    cache[brand] = {"query": q, "places": [], "failed": True}
    stats["failed"] += 1


async def main(args):
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
    logger.add(LOGS / "serper.log", level="DEBUG", encoding="utf-8", rotation="10 MB")

    key = os.getenv("SERPER_API_KEY", "")
    if not key:
        logger.error("SERPER_API_KEY missing from talabat/.env")
        return 1

    import rules
    hints = rules.build()
    # Brand order follows the RESTAURANT FILE, not alphabetical. --limit N then
    # covers the first N restaurants' brands, which is the same slice
    # classify_llm.py --limit N sees, so a small test is coherent end to end.
    # Sorting alphabetically made the two stages test disjoint restaurants.
    seen, brands = set(), []
    for v in hints.values():
        n = (v.get("name") or "").strip()
        if n and n not in seen:
            seen.add(n)
            brands.append(n)
    logger.info(f"restaurants {len(hints):,} | distinct brands {len(brands):,}")

    cache = load_cache()
    todo = [b for b in brands if b not in cache]
    if args.limit:
        todo = todo[: args.limit]
        logger.warning(f"--limit: {len(todo)} brands only")
    logger.info(f"cached {len(brands)-len([b for b in brands if b not in cache]):,} "
                f"| to query {len(todo):,} (1 credit each)")

    if args.dry_run:
        async with httpx.AsyncClient() as c:
            r = await c.get("https://google.serper.dev/account",
                            headers={"X-API-KEY": key}, timeout=20)
            bal = r.json().get("balance") if r.status_code == 200 else "?"
        logger.warning(f"--dry-run: no calls. balance={bal}, would spend {len(todo):,}")
        return 0

    stats = Counter()
    sem = asyncio.Semaphore(SERPER_CONCURRENCY)
    pacer = Pacer(SERPER_QPS)
    t0 = time.time()
    try:
        async with httpx.AsyncClient() as client:
            tasks = [fetch_brand(client, b, sem, pacer, cache, stats, key) for b in todo]
            for i, fut in enumerate(asyncio.as_completed(tasks), 1):
                await fut
                if i % CHECKPOINT_EVERY == 0:
                    save_cache(cache)
                    el = time.time() - t0
                    logger.info(f"  {i:,}/{len(todo):,} | {i/el:.0f}/s | "
                                f"ETA {(len(todo)-i)/(i/el)/60:.1f} min | {dict(stats)}")
    finally:
        save_cache(cache)
    logger.success(f"serper done in {(time.time()-t0)/60:.1f} min | {dict(stats)}")

    # ---- derive footprint + enrichment ----
    # SCOPED TO THIS COHORT'S BRANDS, not to the whole cache. The cache is shared
    # across months on purpose - a brand looked up in August costs nothing in
    # September - but iterating it would put August's 4,419 Google locations
    # inside September's enrichment file, which ships as a deliverable. Foreign
    # data in a "finished" file is the exact defect class the quality gates
    # exist for, and it is invisible: the rows are all real, just not this
    # month's. `footprint` is a lookup and a superset would be harmless; it is
    # scoped anyway so the run's own counts mean what they say.
    mine = set(brands)
    footprint, enrich = {}, []
    for brand, entry in cache.items():
        if brand not in mine:
            continue
        places = entry.get("places", [])
        kept = match_places(brand, places)
        footprint[brand] = {
            "query": entry.get("query"),
            "raw_places": len(places),
            "uae_locations": len(kept),
            "capped": len(places) >= 10,
            "outlet_type": ("Chain" if len(kept) >= CHAIN_MIN_LOCATIONS
                            else "Independent"),
            "failed": bool(entry.get("failed")),
        }
        for p in kept:
            cid = p.get("cid")
            enrich.append({
                "brand": brand,
                "title": p.get("title"),
                "address": p.get("address"),
                "latitude": p.get("latitude"), "longitude": p.get("longitude"),
                "phone": p.get("phoneNumber"),
                "website": p.get("website"),
                "google_category": p.get("category"),
                "rating": p.get("rating"), "rating_count": p.get("ratingCount"),
                "cid": cid,
                "google_maps_url": f"https://www.google.com/maps?cid={cid}" if cid else None,
            })
    # HARD GATE, per Sagar: only UAE locations may ever appear. Nothing
    # downstream re-checks this, and every location here also feeds the
    # Chain/Independent count - one foreign pin can manufacture a chain.
    foreign = [e for e in enrich
               if outside_uae(e.get("latitude"), e.get("longitude"))]
    if foreign:
        raise SystemExit(
            f"REFUSING TO WRITE: {len(foreign)} location(s) outside the UAE "
            f"reached the output, e.g. {foreign[0]['brand']} -> "
            f"{foreign[0]['title']} at {foreign[0]['latitude']},"
            f"{foreign[0]['longitude']}")

    PLACES_OUT.write_text(json.dumps(footprint, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    with open(ENRICHMENT_OUT, "w", encoding="utf-8") as f:
        for e in enrich:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    ot = Counter(v["outlet_type"] for v in footprint.values())
    logger.info(f"  brands: {len(footprint):,} | {dict(ot)}")
    logger.info(f"  capped at 10 (>=10 locations): {sum(1 for v in footprint.values() if v['capped']):,}")
    logger.info(f"  zero same-brand matches       : {sum(1 for v in footprint.values() if v['uae_locations']==0):,}")
    logger.info(f"  enrichment rows: {len(enrich):,}")
    for fld in ("address", "phone", "website", "rating"):
        n = sum(1 for e in enrich if e.get(fld))
        logger.info(f"    {fld:8} present on {n:,}/{len(enrich):,}"
                    f" ({n/max(len(enrich),1)*100:.0f}%)")
    logger.info(f"-> {PLACES_OUT.name}, {ENRICHMENT_OUT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--cohort", default="aug",
                   help="aug | sep - consumed by config at import time, "
                        "declared here only so argparse accepts it")
    sys.exit(asyncio.run(main(p.parse_args())))
