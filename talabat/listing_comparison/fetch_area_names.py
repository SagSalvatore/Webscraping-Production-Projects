"""Pull the venue's REAL area straight from its Talabat page.

Talabat exposes the correct area in __NEXT_DATA__:
    props/pageProps/initialMenuState/restaurant/areaName
    props/pageProps/gtmEventData/restaurant/areaName
plus `branchName` which is "Brand, Area" and corroborates it.

This is Talabat's OWN label, so it matches the taxonomy the rest of the dataset
uses. (Reverse geocoding was tried first and abandoned: it returns OSM's naming,
which is a third-party opinion and can disagree with Talabat's area names even
when the point is right.)

Do NOT read `address.addressLocality` from the JSON-LD - it echoes the ?aid=
crawl zone and carries the same bug we are fixing.

The ?aid= parameter must stay on the URL: without it Talabat serves a generic
homepage with no restaurant data at all.

    python fetch_area_names.py --dry-run
    python fetch_area_names.py --limit 50     small live test
    python fetch_area_names.py                full run
"""
import argparse
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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
load_dotenv(ROOT / ".env", override=True)

OUT = HERE / "output"
LOGD = HERE / "logs"
LOGD.mkdir(exist_ok=True)
SRC = OUT / "run2_identity_classification.csv"
CACHE = OUT / "areaname_cache.json"
FAILED = OUT / "areaname_failed.jsonl"

U = os.getenv("OXYLABS_USERNAME", "")
P = os.getenv("OXYLABS_PASSWORD", "")
C = os.getenv("OXYLABS_COUNTRY", "ae")

AREA_RX = re.compile(r'"areaName"\s*:\s*"([^"]*)"')
BRANCH_RX = re.compile(r'"branchName"\s*:\s*"([^"]*)"')
CHECKPOINT_EVERY = 100


def px():
    sid = uuid.uuid4().hex[:8]
    u = f"http://customer-{U}-cc-{C}-sessid-{sid}:{P}@pr.oxylabs.io:7777"
    return {"http": u, "https": u}


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


async def fetch_one(sess, row, sem, cache, stats):
    bid = row["branch_id"]
    if bid in cache:
        return
    url = row["url"]                     # keeps ?aid= - required by Talabat
    async with sem:
        for attempt in range(4):
            try:
                r = await sess.get(url, timeout=60, proxies=px(),
                                   headers={"Accept-Language": "en-US,en;q=0.9"})
                if r.status_code == 200:
                    areas = AREA_RX.findall(r.text)
                    branch = BRANCH_RX.findall(r.text)
                    # all occurrences should agree; take the first non-empty
                    area = next((a for a in areas if a.strip()), None)
                    cache[bid] = {
                        "area_name_real": area,
                        "branch_name": branch[0] if branch else None,
                        "occurrences": len(areas),
                    }
                    stats["ok" if area else "no_area"] += 1
                    return
                if r.status_code in (403, 429, 503):
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
            except Exception:
                await asyncio.sleep(1.5 * (attempt + 1))
    stats["failed"] += 1
    with open(FAILED, "a", encoding="utf-8") as f:
        f.write(json.dumps({"branch_id": bid, "url": url}, ensure_ascii=False) + "\n")


async def main(args):
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
    logger.add(LOGD / "fetch_area.log", level="DEBUG", encoding="utf-8", rotation="10 MB")

    if not U or not P:
        logger.error("Oxylabs credentials missing")
        return 1

    src = Path(args.src) if args.src else SRC
    if not src.is_absolute():
        src = OUT / src.name
    logger.info(f"source: {src.name}")
    # The run-2 classification CSV carries a `category` column and only its
    # genuinely_new_brand rows are wanted. Other callers pass a purpose-built
    # list that has no such column - the address-smear repair feeds it 4,964
    # EXISTING branches - so filter only when the column is actually there,
    # rather than forcing every caller to fake a category it does not have.
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))
    if rows and "category" in rows[0]:
        rows = [r for r in rows if r["category"] == "genuinely_new_brand"]
    if args.limit:
        rows = rows[: args.limit]
        logger.warning(f"TEST MODE - {len(rows)} rows")

    cache = load_cache()
    todo = [r for r in rows if r["branch_id"] not in cache]
    logger.info(f"genuinely_new_brand rows: {len(rows):,}")
    logger.info(f"cached {len(rows)-len(todo):,} | to fetch {len(todo):,} "
                f"| concurrency {args.concurrency}")

    if args.dry_run:
        logger.warning("--dry-run: no requests")
        return 0

    stats = {"ok": 0, "no_area": 0, "failed": 0}
    t0 = time.time()
    sem = asyncio.Semaphore(args.concurrency)
    try:
        async with AsyncSession(impersonate="chrome124") as sess:
            tasks = [fetch_one(sess, r, sem, cache, stats) for r in todo]
            for i, fut in enumerate(asyncio.as_completed(tasks), 1):
                await fut
                if i % CHECKPOINT_EVERY == 0:
                    save_cache(cache)
                    el = time.time() - t0
                    logger.info(f"  {i:,}/{len(todo):,} | ok {stats['ok']:,} "
                                f"no_area {stats['no_area']} failed {stats['failed']} "
                                f"| {i/el:.1f}/s | ETA {(len(todo)-i)/(i/el)/60:.0f} min")
    finally:
        save_cache(cache)

    # ---- build corrected output ----
    # Per Sagar: a row whose page has no `areaName` (absent, null or empty) is
    # DROPPED, not carried with a blank. We would rather ship fewer rows than
    # rows whose area we cannot state, and the crawl-zone value is known-wrong
    # so it is not an acceptable fallback.
    out, changed, dropped = [], 0, 0
    for r in rows:
        hit = cache.get(r["branch_id"]) or {}
        real = (hit.get("area_name_real") or "").strip()
        if not real or real.lower() in ("null", "none"):
            dropped += 1
            continue
        if real.lower() != (r["area_name"] or "").strip().lower():
            changed += 1
        out.append({
            "branch_id": r["branch_id"], "restaurant_id": r["restaurant_id"],
            "name": r["name"], "url": r["url"],
            "lat": r["lat"], "lon": r["lon"],
            "area_crawl_zone_WRONG": r["area_name"],
            "area_name_real": real,
            "branch_name": hit.get("branch_name") or "",
            "changed": real.lower() != (r["area_name"] or "").strip().lower(),
        })
    if not out:
        logger.error("every row was dropped for a missing areaName - nothing to write")
        return 1

    fields = list(out[0].keys())
    with open(OUT / f"{args.out_prefix}_area_corrected.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out)
    with open(OUT / f"{args.out_prefix}_area_corrected.jsonl", "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    logger.success(f"done in {(time.time()-t0)/60:.1f} min")
    logger.info(f"  rows kept    : {len(out):,} / {len(rows):,}")
    logger.info(f"  DROPPED (no areaName) : {dropped:,}")
    logger.info(f"  area CHANGED : {changed:,} / {len(out):,} "
                f"({changed/len(out)*100:.1f}%)")
    logger.info(f"  failed pages : {stats['failed']:,}"
                + ("  (re-run to retry - cache resumes)" if stats["failed"] else ""))
    logger.info("\n  samples:")
    for o in [o for o in out if o["changed"]][:10]:
        logger.info(f"    {o['name'][:26]:28} {o['area_crawl_zone_WRONG'][:18]:20} -> "
                    f"{o['area_name_real']}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int)
    p.add_argument("--src", help="classification CSV to read (default: run-2's)")
    p.add_argument("--out-prefix", default="genuinely_new",
                   help="output basename prefix")
    p.add_argument("--concurrency", type=int, default=12)
    p.add_argument("--dry-run", action="store_true")
    sys.exit(asyncio.run(main(p.parse_args())))
