"""Smoke test — measure the NEW-branch yield before committing to a long crawl.

Two questions this answers, both raised before running production:

  1. Do successive pages of one area return DISTINCT listings, or repeats?
     (measured as unique branch_ids per page vs page size)
  2. Of what an area returns, how much is NEW versus already in our existing
     17,211-branch universe?

Why it matters: in the previous run Al Barsha 3 yielded 14.2 new branches/page
while Al Jaddaf yielded 3.6 — delivery zones overlap heavily, so areas crawled
later return progressively less. If 20 representative areas yield almost
nothing new, a 637-area crawl is not worth the hours.

Samples the first N pages of 20 areas rather than crawling them fully - enough
to measure the rate and extrapolate, without a multi-hour run.

    python smoke_test_yield.py                # 20 areas x 5 pages
    python smoke_test_yield.py --areas 20 --pages 5
"""
import argparse
import asyncio
import csv
import json
import random
import re
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from loguru import logger

_ROOT = Path(__file__).resolve().parent.parent          # talabat/
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")

from url_collector.parser import parse_listing_page      # noqa: E402

import os                                                # noqa: E402

CSV_PATH = _ROOT / "CITY-URLS.csv"
EXISTING = _ROOT / "data" / "urls" / "talabat_restaurant_urls.jsonl"
OUT_DIR = Path(__file__).resolve().parent / "smoke_results"
OUT_DIR.mkdir(exist_ok=True)

USER = os.getenv("OXYLABS_USERNAME", "")
PASS = os.getenv("OXYLABS_PASSWORD", "")
CC = os.getenv("OXYLABS_COUNTRY", "ae")

# NOTE: proxy scheme must be http:// even though the target is https://.
# The tunnel is an HTTP CONNECT; using https:// here makes curl attempt a TLS
# handshake *to the proxy*, which fails with an opaque OPENSSL error.
def proxy_url(sid: str) -> str:
    return f"http://customer-{USER}-cc-{CC}-sessid-{sid}:{PASS}@pr.oxylabs.io:7777"


# areas already crawled to completion - excluded from the sample, since
# re-measuring them would understate the yield of genuinely fresh areas
COMPLETE_AREA_IDS = set()


def load_existing():
    ids, per_area = set(), defaultdict(set)
    with open(EXISTING, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                ids.add(r["branch_id"])
                per_area[r["area_id"]].add(r.get("page_found"))
    # an area counts as COMPLETE when we crawled >=60% of the pages we saw
    for aid, pages in per_area.items():
        pages = {p for p in pages if isinstance(p, int)}
        if pages and len(pages) / max(pages) >= 0.6:
            COMPLETE_AREA_IDS.add(aid)
    return ids


def load_areas():
    out = []
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            name, url = row[0].strip().replace("﻿", ""), row[1].strip()
            m = re.search(r"/restaurants/(\d+)/", url)
            out.append({"name": name, "url": url, "id": int(m.group(1)) if m else 0})
    return out


async def fetch(session, url, sid, referer=None):
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    px = proxy_url(sid)
    for attempt in range(3):
        try:
            r = await session.get(url, headers=headers, timeout=60,
                                  proxies={"http": px, "https": px})
            if r.status_code == 200:
                return r.text
            if r.status_code in (403, 429, 503):
                await asyncio.sleep(2 * (attempt + 1))
                sid = uuid.uuid4().hex[:8]
                px = proxy_url(sid)
                continue
            return None
        except Exception:
            await asyncio.sleep(2 * (attempt + 1))
    return None


async def probe_area(session, area, max_pages, existing, sem, results):
    sid = uuid.uuid4().hex[:8]
    async with sem:
        html = await fetch(session, f"{area['url']}?page=1", sid)
    if not html:
        results.append({**area, "error": "page1 failed"})
        return

    p1 = parse_listing_page(html, 1)
    total_pages = p1.total_pages
    total_vendors = p1.total_restaurants

    seen_this_area = set()
    per_page = []

    def absorb(res, page):
        ids = {v.branch_id for v in res.vendors if v.branch_id}
        new_to_area = ids - seen_this_area
        seen_this_area.update(ids)
        per_page.append({
            "page": page,
            "returned": len(ids),
            "new_within_area": len(new_to_area),
            "new_vs_universe": len(new_to_area - existing),
        })

    absorb(p1, 1)

    pages = list(range(2, min(max_pages, total_pages or max_pages) + 1))

    async def one(pg):
        s = uuid.uuid4().hex[:8]
        async with sem:
            h = await fetch(session, f"{area['url']}?page={pg}", s,
                            referer=f"{area['url']}?page={pg-1}")
        if h:
            absorb(parse_listing_page(h, pg), pg)

    if pages:
        await asyncio.gather(*(one(p) for p in pages))

    new_ids = seen_this_area - existing
    results.append({
        "area": area["name"],
        "area_id": area["id"],
        "total_vendors_reported": total_vendors,
        "total_pages_reported": total_pages,
        "pages_sampled": len(per_page),
        "unique_returned": len(seen_this_area),
        "new_vs_universe": len(new_ids),
        "new_rate": round(len(new_ids) / max(len(seen_this_area), 1), 3),
        "per_page": per_page,
    })
    logger.info(
        f"{area['name'][:30]:32} pages={len(per_page):>2}/{total_pages or '?':<4} "
        f"unique={len(seen_this_area):>3} NEW={len(new_ids):>3} "
        f"({len(new_ids)/max(len(seen_this_area),1)*100:>5.1f}%) "
        f"| area total={total_vendors}"
    )


async def main(args):
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | {message}")

    if not USER or not PASS:
        logger.error("Oxylabs credentials missing in talabat/.env")
        return 1

    existing = load_existing()
    areas = load_areas()
    todo = [a for a in areas if a["id"] not in COMPLETE_AREA_IDS]
    logger.info(f"existing universe: {len(existing):,} branch_ids")
    logger.info(f"areas: {len(areas)} total | {len(COMPLETE_AREA_IDS)} complete "
                f"| {len(todo)} to crawl")

    random.seed(42)                       # reproducible sample
    sample = random.sample(todo, min(args.areas, len(todo)))
    logger.info(f"sampling {len(sample)} areas x up to {args.pages} pages\n")

    sem = asyncio.Semaphore(args.concurrency)
    results = []
    t0 = time.time()
    async with AsyncSession(impersonate="chrome124") as session:
        await asyncio.gather(*(probe_area(session, a, args.pages, existing, sem, results)
                               for a in sample))
    elapsed = time.time() - t0

    ok = [r for r in results if "error" not in r]
    if not ok:
        logger.error("all probes failed")
        return 1

    tot_unique = sum(r["unique_returned"] for r in ok)
    tot_new = sum(r["new_vs_universe"] for r in ok)
    pages_done = sum(r["pages_sampled"] for r in ok)

    print("\n" + "=" * 74)
    print("SMOKE TEST RESULT")
    print("=" * 74)
    print(f"  areas probed            : {len(ok)}  ({elapsed:.0f}s, {pages_done} pages)")
    print(f"  unique branches seen    : {tot_unique:,}")
    print(f"  NEW (not in our 17,211) : {tot_new:,}  ({tot_new/max(tot_unique,1)*100:.1f}%)")
    print(f"  new per page            : {tot_new/max(pages_done,1):.1f}")

    # Q1: do pages repeat within an area?
    dup_within = sum(p["returned"] - p["new_within_area"]
                     for r in ok for p in r["per_page"])
    returned = sum(p["returned"] for r in ok for p in r["per_page"])
    print(f"\n  page-repeat check: {dup_within:,} of {returned:,} returned rows "
          f"were repeats within the same area ({dup_within/max(returned,1)*100:.1f}%)")
    print("  -> pagination returns distinct listings"
          if dup_within / max(returned, 1) < 0.1
          else "  -> WARNING: pages repeat heavily")

    hi = [r for r in ok if r["new_rate"] >= 0.5]
    lo = [r for r in ok if r["new_rate"] < 0.1]
    print(f"\n  areas with >=50% new  : {len(hi)}/{len(ok)}")
    print(f"  areas with <10% new   : {len(lo)}/{len(ok)}")

    # extrapolate
    avg_pages = sum(r["total_pages_reported"] or 0 for r in ok) / len(ok)
    rate = tot_new / max(pages_done, 1)
    print(f"\n  EXTRAPOLATION")
    print(f"    avg pages per area        : {avg_pages:.0f}")
    print(f"    projected new per area    : {rate*avg_pages:,.0f}")
    print(f"    projected NEW for 300 areas: {rate*avg_pages*300:,.0f}")
    print(f"    est. requests for 300     : {avg_pages*300:,.0f}")

    out = OUT_DIR / "smoke_yield.json"
    out.write_text(json.dumps({"summary": {
        "areas": len(ok), "unique": tot_unique, "new": tot_new,
        "pages": pages_done, "new_per_page": rate}, "areas": ok},
        indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  detail -> {out}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--areas", type=int, default=20)
    p.add_argument("--pages", type=int, default=5)
    p.add_argument("--concurrency", type=int, default=6)
    sys.exit(asyncio.run(main(p.parse_args())))
