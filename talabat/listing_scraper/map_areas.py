"""Probe page 1 of every remaining area to build a crawl plan.

One request per area (~637 total, a few minutes) gives us, per area:
    total_vendors  - how many listings that area reports
    total_pages    - how deep the crawl would be
    new_rate_p1    - fraction of page-1 results not already in our universe

That turns "crawl the first 300 areas" from an arbitrary split into an ordered
plan: cheap high-yield areas first. The smoke test showed small peripheral
areas returning 100% new in ~21 pages, while central Dubai areas returned
8-20% new across 400+ pages - so page-count order matters enormously.

    python map_areas.py
"""
import asyncio
import csv
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from loguru import logger

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")
from url_collector.parser import parse_listing_page      # noqa: E402

OUT = Path(__file__).resolve().parent / "smoke_results" / "area_map.json"
OUT.parent.mkdir(exist_ok=True)

U = os.getenv("OXYLABS_USERNAME", "")
P = os.getenv("OXYLABS_PASSWORD", "")
C = os.getenv("OXYLABS_COUNTRY", "ae")


def px(sid):
    return f"http://customer-{U}-cc-{C}-sessid-{sid}:{P}@pr.oxylabs.io:7777"


async def probe(sess, area, existing, sem, out, done):
    sid = uuid.uuid4().hex[:8]
    async with sem:
        for attempt in range(3):
            try:
                r = await sess.get(f"{area['url']}?page=1", timeout=60,
                                   proxies={"http": px(sid), "https": px(sid)},
                                   headers={"Accept-Language": "en-US,en;q=0.9"})
                if r.status_code == 200:
                    res = parse_listing_page(r.text, 1)
                    ids = {v.branch_id for v in res.vendors if v.branch_id}
                    out.append({
                        "name": area["name"], "id": area["id"], "url": area["url"],
                        "total_vendors": res.total_restaurants,
                        "total_pages": res.total_pages,
                        "p1_returned": len(ids),
                        "p1_new": len(ids - existing),
                        "new_rate_p1": round(len(ids - existing) / max(len(ids), 1), 3),
                    })
                    break
                sid = uuid.uuid4().hex[:8]
                await asyncio.sleep(1.5 * (attempt + 1))
            except Exception:
                sid = uuid.uuid4().hex[:8]
                await asyncio.sleep(1.5 * (attempt + 1))
        else:
            out.append({"name": area["name"], "id": area["id"], "url": area["url"],
                        "error": True})
    done[0] += 1
    if done[0] % 50 == 0:
        logger.info(f"  {done[0]} areas probed")


async def main():
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | {message}")

    existing, per_area = set(), {}
    with open(_ROOT / "data" / "urls" / "talabat_restaurant_urls.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                existing.add(r["branch_id"])
                p = r.get("page_found")
                if isinstance(p, int):
                    per_area.setdefault(r["area_id"], set()).add(p)
    complete = {a for a, pg in per_area.items() if pg and len(pg) / max(pg) >= 0.6}

    areas = []
    with open(_ROOT / "CITY-URLS.csv", newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                m = re.search(r"/restaurants/(\d+)/", row[1].strip())
                aid = int(m.group(1)) if m else 0
                if aid not in complete:
                    areas.append({"name": row[0].strip().replace("﻿", ""),
                                  "url": row[1].strip(), "id": aid})

    logger.info(f"universe={len(existing):,} | complete areas skipped={len(complete)} | probing {len(areas)}")
    sem = asyncio.Semaphore(10)
    out, done, t0 = [], [0], time.time()
    async with AsyncSession(impersonate="chrome124") as sess:
        await asyncio.gather(*(probe(sess, a, existing, sem, out, done) for a in areas))

    ok = [o for o in out if not o.get("error")]
    OUT.write_text(json.dumps(ok, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.success(f"probed {len(ok)}/{len(areas)} in {time.time()-t0:.0f}s -> {OUT.name}")

    tot_pages = sum(o["total_pages"] or 0 for o in ok)
    print(f"\n  total pages across all remaining areas : {tot_pages:,}")
    print(f"  est. requests for a FULL crawl         : {tot_pages:,}")
    print(f"  at 8 req/s -> {tot_pages/8/3600:.1f} h   |  at 15 req/s -> {tot_pages/15/3600:.1f} h")

    # cheap + high-yield first
    ranked = sorted(ok, key=lambda o: (-(o["new_rate_p1"] or 0), o["total_pages"] or 9999))
    print("\n  TOP 15 by (new-rate, then cheapness):")
    print(f"     {'area':32} {'pages':>6} {'vendors':>8} {'p1 new%':>8}")
    for o in ranked[:15]:
        print(f"     {o['name'][:30]:32} {o['total_pages'] or 0:>6} "
              f"{o['total_vendors'] or 0:>8} {(o['new_rate_p1'] or 0)*100:>7.0f}%")

    for cut in (100, 200, 300, len(ok)):
        sel = ranked[:cut]
        pg = sum(o["total_pages"] or 0 for o in sel)
        print(f"\n  top {cut:>3} areas: {pg:>7,} pages  (~{pg/8/3600:.1f} h at 8 req/s)")


if __name__ == "__main__":
    asyncio.run(main())
