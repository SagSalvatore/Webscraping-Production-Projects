"""Retry the areas that failed probing, and benchmark sustainable throughput.

Two jobs in one pass because they need the same requests:
  * complete area_map.json (the 140 areas that timed out at concurrency 10)
  * measure req/s at several concurrency levels so the production run is tuned
    from evidence rather than a guess

Residential proxies are latency-bound, not bandwidth-bound: each request pays
1-3s of proxy round-trip regardless of concurrency, so throughput scales with
in-flight requests until the target rate-limits. This finds that ceiling.
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

RES = Path(__file__).resolve().parent / "smoke_results"
MAP = RES / "area_map.json"
U = os.getenv("OXYLABS_USERNAME", "")
P = os.getenv("OXYLABS_PASSWORD", "")
C = os.getenv("OXYLABS_COUNTRY", "ae")

logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | {message}")


def px(sid):
    return f"http://customer-{U}-cc-{C}-sessid-{sid}:{P}@pr.oxylabs.io:7777"


async def get(sess, url, sem, tries=3):
    sid = uuid.uuid4().hex[:8]
    for a in range(tries):
        async with sem:
            try:
                r = await sess.get(url, timeout=75,
                                   proxies={"http": px(sid), "https": px(sid)},
                                   headers={"Accept-Language": "en-US,en;q=0.9"})
                if r.status_code == 200:
                    return r.text
                sid = uuid.uuid4().hex[:8]
            except Exception:
                sid = uuid.uuid4().hex[:8]
        await asyncio.sleep(1.2 * (a + 1))
    return None


async def main():
    existing = set()
    per_area = {}
    with open(_ROOT / "data" / "urls" / "talabat_restaurant_urls.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                existing.add(r["branch_id"])
                p = r.get("page_found")
                if isinstance(p, int):
                    per_area.setdefault(r["area_id"], set()).add(p)
    complete = {a for a, pg in per_area.items() if pg and len(pg) / max(pg) >= 0.6}

    mapped = {o["id"] for o in json.loads(MAP.read_text(encoding="utf-8"))}
    areas = []
    with open(_ROOT / "CITY-URLS.csv", newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                m = re.search(r"/restaurants/(\d+)/", row[1].strip())
                aid = int(m.group(1)) if m else 0
                if aid not in complete and aid not in mapped:
                    areas.append({"name": row[0].strip().replace("﻿", ""),
                                  "url": row[1].strip(), "id": aid})
    logger.info(f"areas still unmapped: {len(areas)}")

    # ---------- benchmark on a slice of real work ----------
    logger.info("benchmarking concurrency on real requests …")
    bench_urls = [f"{a['url']}?page=1" for a in areas[:120]]
    async with AsyncSession(impersonate="chrome124") as sess:
        for conc in (10, 25, 40, 60):
            batch = bench_urls[:30]
            sem = asyncio.Semaphore(conc)
            t0 = time.time()
            got = await asyncio.gather(*(get(sess, u, sem, tries=1) for u in batch))
            dt = time.time() - t0
            ok = sum(1 for g in got if g)
            logger.info(f"   concurrency {conc:>3}: {ok}/{len(batch)} ok in {dt:>5.1f}s "
                        f"-> {len(batch)/dt:>5.2f} req/s  "
                        f"({86531/(len(batch)/dt)/3600:>4.1f} h for 86.5k pages)")

        # ---------- retry the unmapped areas ----------
        logger.info(f"\nretrying {len(areas)} unmapped areas at concurrency 25 …")
        sem = asyncio.Semaphore(25)
        out, done = [], [0]

        async def probe(a):
            html = await get(sess, f"{a['url']}?page=1", sem, tries=4)
            if html:
                res = parse_listing_page(html, 1)
                ids = {v.branch_id for v in res.vendors if v.branch_id}
                out.append({"name": a["name"], "id": a["id"], "url": a["url"],
                            "total_vendors": res.total_restaurants,
                            "total_pages": res.total_pages,
                            "p1_returned": len(ids), "p1_new": len(ids - existing),
                            "new_rate_p1": round(len(ids - existing) / max(len(ids), 1), 3)})
            done[0] += 1
            if done[0] % 40 == 0:
                logger.info(f"   {done[0]}/{len(areas)}")

        t0 = time.time()
        await asyncio.gather(*(probe(a) for a in areas))
        logger.success(f"recovered {len(out)}/{len(areas)} in {time.time()-t0:.0f}s")

    full = json.loads(MAP.read_text(encoding="utf-8")) + out
    MAP.write_text(json.dumps(full, indent=2, ensure_ascii=False), encoding="utf-8")
    still = [a for a in areas if a["id"] not in {o["id"] for o in out}]
    (RES / "unmappable_areas.json").write_text(
        json.dumps(still, indent=2, ensure_ascii=False), encoding="utf-8")

    tot = sum(o["total_pages"] or 0 for o in full)
    logger.success(f"area_map.json now has {len(full)} areas | {tot:,} total pages")
    logger.info(f"still unmappable: {len(still)} (saved for a separate retry)")


if __name__ == "__main__":
    asyncio.run(main())
