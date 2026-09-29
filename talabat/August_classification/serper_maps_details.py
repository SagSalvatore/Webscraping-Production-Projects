"""Gap-fill address / phone / website via Serper's /maps endpoint.

WHY A SECOND ENDPOINT EXISTS AT ALL:
    /places returns the Maps search CARD - 8 fields, address on only 10.4% of
    35,345 results, phoneNumber 4.8%, website 3.0%, placeId always null.
    /maps returns the Maps LISTING - 19 fields including address, phoneNumber,
    website, placeId, openingHours, priceLevel, types - and up to 20 results
    instead of 10.
    Same API, same credit cost, same query. The first pass used /places purely
    because the name matched "Google Places"; that cost us the enrichment and
    nearly cost an unnecessary Apify run.

SCOPE: brands whose restaurants are in restaurants_WITHOUT_address.json AND
have has_google_maps_listing = true. Those are confirmed present on Maps - we
already hold their CID - so the address exists and /places simply did not
return it. Brands with NO Maps match are excluded: no endpoint will find them.

Matching is identical to serper_places.py - exact whole-word name containment,
Arabic stripped, non-UAE dropped - so a brand cannot pick up a stranger's
address. That filter is the whole reason these numbers are trustworthy.

    python serper_maps_details.py --dry-run
    python serper_maps_details.py --limit 50
    python serper_maps_details.py
"""
import argparse
import asyncio
import json
import sys
import time
from collections import Counter

import httpx
from dotenv import load_dotenv
from loguru import logger

import config as C
from serper_places import (Pacer, is_non_uae, name_matches_strict,
                           outside_uae, strip_arabic)
import os

load_dotenv(C.ROOT / ".env", override=True)

MAPS_URL = "https://google.serper.dev/maps"
CACHE = C.DATA / "serper_maps_cache.json"
OUT = C.DATA / "google_maps_details.jsonl"
WITHOUT = C.DATA / "restaurants_WITHOUT_address.json"
CHECKPOINT_EVERY = 200
CONCURRENCY = 20
QPS = 30.0


def load_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("cache unreadable - starting fresh")
    return {}


def save_cache(c):
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE)


def match_maps(brand, places):
    """Same gates as /places: exact name, UAE only, one row per location."""
    keep, seen = [], set()
    for p in places:
        title = p.get("title") or ""
        addr = p.get("address") or ""
        if is_non_uae(addr) or outside_uae(p.get("latitude"), p.get("longitude")):
            continue
        ok = name_matches_strict(brand, title)
        if not ok and not strip_arabic(title) and len(places) == 1:
            ok = True                       # arabic-only sole result
        if not ok:
            continue
        k = (round(p.get("latitude") or 0, 4), round(p.get("longitude") or 0, 4))
        if k in seen and k != (0, 0):
            continue
        seen.add(k)
        keep.append(p)
    return keep


async def fetch(client, brand, sem, pacer, cache, stats, key):
    if brand in cache:
        stats["cached"] += 1
        return
    q = C.SERPER_QUERY.format(name=brand)
    for attempt in range(4):
        await pacer.wait()
        async with sem:
            try:
                r = await client.post(MAPS_URL,
                                      headers={"X-API-KEY": key,
                                               "Content-Type": "application/json"},
                                      json={"q": q, "gl": "ae"}, timeout=30)
                if r.status_code == 200:
                    d = r.json()
                    cache[brand] = {"query": q,
                                    "places": d.get("places") or d.get("maps") or []}
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
    logger.add(C.LOGS / "serper_maps.log", level="DEBUG", encoding="utf-8",
               rotation="10 MB")

    key = os.getenv("SERPER_API_KEY", "")
    if not key:
        logger.error("SERPER_API_KEY missing")
        return 1

    if args.all_brands:
        # September mode. August ran this as a GAP-FILL: only brands already
        # known to be missing an address. Here the goal is different - website,
        # phone and the Google listing details are wanted for the whole cohort,
        # and /maps returns 19 fields to /places' 8 at the same credit cost.
        # So the target is every brand in this cohort, and the split reported
        # below is by whether /places found a Maps listing at all, which is the
        # only useful predictor of a hit (91% vs 12%, measured in August).
        import rules
        foot = json.loads(C.PLACES_OUT.read_text(encoding="utf-8"))
        brands = sorted({(v.get("name") or "").strip()
                         for v in rules.build().values() if (v.get("name") or "").strip()})
        on_maps = [b for b in brands if foot.get(b, {}).get("uae_locations", 0) > 0]
        logger.info(f"cohort brands                  : {len(brands):,}")
        logger.info(f"  with a /places Maps listing  : {len(on_maps):,}  (~91% hit rate)")
        logger.info(f"  without one                  : {len(brands)-len(on_maps):,}  (~12%)")
    else:
        rows = json.loads(WITHOUT.read_text(encoding="utf-8"))
        if args.no_maps_listing:
            # second sweep: brands /places found NOTHING for. /maps returns up
            # to 20 results vs 10 and indexes differently, so a minority do
            # surface. Measured hit rate on a 40-brand sample: 12% (vs 91% for
            # the confirmed-listing set). Same exact-name + UAE validation
            # applies - 335 wrong-brand results were rejected in that sample.
            target = [r for r in rows if not r.get("has_google_maps_listing")]
        else:
            target = [r for r in rows if r.get("has_google_maps_listing")]
        brands = sorted({r["restaurant_name"] for r in target})
        logger.info(f"restaurants without an address : {len(rows):,}")
        logger.info(f"  of those, ON Google Maps     : {len(target):,}")
        logger.info(f"  distinct brands to re-query  : {len(brands):,}")

    cache = load_cache()
    todo = [b for b in brands if b not in cache]
    if args.limit:
        todo = todo[: args.limit]
        logger.warning(f"--limit: {len(todo)} brands")
    logger.info(f"  cached {len(brands)-len([b for b in brands if b not in cache]):,} "
                f"| to query {len(todo):,} (1 credit each)")

    if args.dry_run:
        async with httpx.AsyncClient() as cl:
            r = await cl.get("https://google.serper.dev/account",
                             headers={"X-API-KEY": key}, timeout=20)
            bal = r.json().get("balance") if r.status_code == 200 else "?"
        logger.warning(f"--dry-run: balance={bal}, would spend {len(todo):,}")
        return 0

    stats = Counter()
    sem = asyncio.Semaphore(CONCURRENCY)
    pacer = Pacer(QPS)
    t0 = time.time()
    try:
        async with httpx.AsyncClient() as client:
            tasks = [fetch(client, b, sem, pacer, cache, stats, key) for b in todo]
            for i, fut in enumerate(asyncio.as_completed(tasks), 1):
                await fut
                if i % CHECKPOINT_EVERY == 0:
                    save_cache(cache)
                    el = time.time() - t0
                    logger.info(f"  {i:,}/{len(todo):,} | {i/el:.0f}/s | "
                                f"ETA {(len(todo)-i)/(i/el)/60:.1f} min | {dict(stats)}")
    finally:
        save_cache(cache)
    logger.success(f"maps done in {(time.time()-t0)/60:.1f} min | {dict(stats)}")

    # ---- extract ----
    # scoped to this run's brands: the cache may outlive one cohort, and this
    # file ships, so a stale brand in it would be foreign data.
    mine = set(brands)
    out, per_brand = [], {}
    for brand, entry in cache.items():
        if brand not in mine:
            continue
        kept = match_maps(brand, entry.get("places") or [])
        per_brand[brand] = len(kept)
        for p in kept:
            cid = p.get("cid")
            out.append({
                "brand": brand,
                "title": p.get("title"),
                "address": p.get("address"),
                "phone": p.get("phoneNumber"),
                "website": p.get("website"),
                "place_id": p.get("placeId"),
                "cid": cid,
                "google_maps_url": f"https://www.google.com/maps?cid={cid}" if cid else None,
                "latitude": p.get("latitude"), "longitude": p.get("longitude"),
                "google_category": p.get("type"),
                "google_categories": p.get("types"),
                "rating": p.get("rating"), "rating_count": p.get("ratingCount"),
                "price_level": p.get("priceLevel"),
                "opening_hours": p.get("openingHours"),
            })
    # HARD GATE, per Sagar: UAE locations only, never a foreign one.
    foreign = [o for o in out
               if outside_uae(o.get("latitude"), o.get("longitude"))]
    if foreign:
        raise SystemExit(
            f"REFUSING TO WRITE: {len(foreign)} location(s) outside the UAE, "
            f"e.g. {foreign[0]['brand']} -> {foreign[0]['title']} at "
            f"{foreign[0]['latitude']},{foreign[0]['longitude']}")

    with open(OUT, "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    logger.success(f"-> {OUT.name} ({len(out):,} locations)")
    for fld in ("address", "phone", "website", "place_id", "opening_hours", "rating"):
        n = sum(1 for o in out if o.get(fld))
        logger.info(f"    {fld:14} {n:>6,}/{len(out):,}  ({n/max(len(out),1)*100:5.1f}%)")
    got = sum(1 for b, n in per_brand.items() if n)
    withaddr = len({o["brand"] for o in out if o.get("address")})
    logger.info(f"  brands with >=1 location : {got:,}/{len(per_brand):,}")
    logger.info(f"  brands with >=1 ADDRESS  : {withaddr:,}  "
                f"(was 0 for these brands on /places)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--no-maps-listing", action="store_true",
                   help="sweep the brands /places found nothing for")
    p.add_argument("--all-brands", action="store_true",
                   help="every brand in the cohort, not just address gaps - "
                        "use this for a monthly enrichment pass")
    p.add_argument("--cohort", default="aug",
                   help="aug | sep - consumed by config at import time, "
                        "declared here only so argparse accepts it")
    sys.exit(asyncio.run(main(p.parse_args())))
