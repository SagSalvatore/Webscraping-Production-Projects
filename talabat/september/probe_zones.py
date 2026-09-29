"""Step 2 - probe every delivery zone, snapshot it, and diff against last month.

WHY. Crawling all 646 zones is 96,497 pages, most of which return restaurants we
already hold. One request per zone returns that zone's totalVendors, so a
zone whose total has not moved can be skipped entirely.

    zone            Aug     Sep    delta
    Al Bahya        563     571     +8    -> crawl
    Dubai Fest.C  7,212   7,212      0    -> skip

WHAT totalVendors IS AND IS NOT. It is a change DETECTOR, not a change COUNT:
  * It is NET. A zone that gains 5 and loses 5 reads as zero.
  * It CANNOT be summed across zones. Delivery zones overlap heavily, so one new
    restaurant appears in the totals of every neighbouring zone; adding the
    deltas multiply-counts badly.
The actual count of new restaurants comes from crawling and comparing
branch_ids - this only decides WHERE to crawl.

DATED SNAPSHOTS, DELIBERATELY. listing_scraper/map_areas.py writes a fixed
smoke_results/area_map.json and would overwrite the only baseline we hold (taken
2026-08-10, 572 zones). This writes zone_snapshot_<month>.json instead, so every
cycle keeps its own and month-over-month comparison stays possible.

    python probe_zones.py --dry-run       plan only, no requests
    python probe_zones.py --limit 20      small live test
    python probe_zones.py                 full probe of all zones
"""
import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

from curl_cffi import requests as cffi
from dotenv import load_dotenv

from config import (CITY_URLS, DATA, PREV_MONTH, PREV_ZONE_SNAPSHOT, ROOT,
                    ZONE_DELTA, ZONE_SNAPSHOT)

load_dotenv(ROOT / ".env", override=True)
sys.stdout.reconfigure(encoding="utf-8")

CONCURRENCY = 12
RX = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def proxy_url():
    u = os.environ["OXYLABS_USERNAME"]
    p = os.environ["OXYLABS_PASSWORD"]
    c = os.environ.get("OXYLABS_COUNTRY", "ae")
    return f"http://customer-{u}-cc-{c}:{p}@pr.oxylabs.io:7777"


def load_zones():
    """CITY-URLS.csv has NO header row - the first line is data."""
    zones = []
    with open(CITY_URLS, encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 2 or not row[1].startswith("http"):
                continue
            m = re.search(r"/restaurants/(\d+)/", row[1])
            if m:
                zones.append({"name": row[0].strip(), "id": int(m.group(1)),
                              "url": row[1].strip()})
    return zones


def probe_one(sess, z):
    """One request -> that zone's totalVendors and page size."""
    try:
        r = sess.get(f"{z['url']}?page=1", timeout=45)
        if r.status_code != 200:
            return {**z, "error": f"HTTP {r.status_code}"}
        m = RX.search(r.text)
        if not m:
            return {**z, "error": "no __NEXT_DATA__"}
        d = json.loads(m.group(1))["props"]["pageProps"].get("data") or {}
        tv = d.get("totalVendors")
        size = d.get("size") or 15
        vendors = d.get("vendors") or []
        # MUST be branchId, not id. `id`/`restaurantId` is the parent CHAIN -
        # shared across every branch (KFC carries one across 258) - so comparing
        # it against our branch_id universe compares two different id spaces and
        # reports ~91% of page 1 as new when the true rate is a few percent.
        # url_collector/parser.py names both fields explicitly; follow it.
        return {**z, "total_vendors": tv, "page_size": size,
                "total_pages": (tv + size - 1) // size if tv else None,
                "p1_ids": [v.get("branchId") for v in vendors
                           if v.get("branchId")]}
    except Exception as exc:
        return {**z, "error": type(exc).__name__}


async def probe_all(zones):
    sem = asyncio.Semaphore(CONCURRENCY)
    sess = cffi.Session(impersonate="chrome124")
    sess.proxies = {"http": proxy_url(), "https": proxy_url()}
    loop = asyncio.get_running_loop()
    done = [0]

    async def one(z):
        async with sem:
            r = await loop.run_in_executor(None, probe_one, sess, z)
            done[0] += 1
            if done[0] % 50 == 0:
                print(f"    {done[0]:,}/{len(zones):,} ...", flush=True)
            return r

    return await asyncio.gather(*(one(z) for z in zones))


def load_previous():
    """The baseline is always a PREVIOUS cycle's snapshot - never this one's.

    This used to try ZONE_SNAPSHOT (this month's own file) first, which meant a
    re-run diffed the month against ITSELF: a `--limit 20` test wrote 20 zones,
    then the full 646-zone run compared against those 20 and reported
    "zones CHANGED: 0, unchanged: 20, no baseline: 618". Silently useless - the
    numbers look like a finished comparison rather than an error.

    Order: last month's dated snapshot, else the original area_map.json baseline.
    """
    prev_dated = DATA / f"zone_snapshot_{PREV_MONTH}.json"
    for p in (prev_dated, PREV_ZONE_SNAPSHOT):
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8-sig"))
            rows = d["zones"] if isinstance(d, dict) and "zones" in d else d
            return {int(z["id"]): z for z in rows if z.get("id") is not None}, p.name
    return {}, None


def load_current():
    """This month's already-probed snapshot, for --rediff."""
    if not ZONE_SNAPSHOT.exists():
        raise SystemExit(f"--rediff needs {ZONE_SNAPSHOT.name}; run the probe first")
    d = json.loads(ZONE_SNAPSHOT.read_text(encoding="utf-8-sig"))
    rows = d["zones"] if isinstance(d, dict) and "zones" in d else d
    return [z for z in rows if z.get("id") is not None]


def main(args):
    t0 = time.time()
    print("=" * 74)
    print("  PROBE DELIVERY ZONES  (one request each)")
    print("=" * 74)

    zones = load_zones()
    if args.limit:
        zones = zones[: args.limit]
    print(f"  zones to probe : {len(zones):,}")

    prev, prev_name = load_previous()
    print(f"  baseline       : {prev_name or 'NONE - this becomes the first'}"
          f"  ({len(prev):,} zones)")

    if args.dry_run:
        print("\n  --dry-run: no requests made")
        return 0

    if args.rediff:
        # recompute the delta from the snapshot already on disk - free, and
        # the way to recover from a bad baseline without re-probing 646 zones
        results = load_current()
        print(f'  --rediff: reusing {ZONE_SNAPSHOT.name} ({len(results):,} zones)')
    else:
        results = asyncio.run(probe_all(zones))
    ok = [r for r in results if r.get("total_vendors") is not None]
    bad = [r for r in results if r.get("total_vendors") is None]
    print(f"\n  probed ok      : {len(ok):,}")
    print(f"  failed         : {len(bad):,}")
    if ok:
        print(f"  total vendors  : {sum(r['total_vendors'] for r in ok):,} "
              f"(NOT a restaurant count - zones overlap)")
        print(f"  total pages    : {sum(r['total_pages'] or 0 for r in ok):,}")

    changed, unchanged, brand_new = [], [], []
    for r in ok:
        p = prev.get(r["id"])
        if not p or p.get("total_vendors") is None:
            brand_new.append(r)
            continue
        delta = r["total_vendors"] - p["total_vendors"]
        if delta:
            changed.append({**r, "prev_total": p["total_vendors"],
                            "delta": delta})
        else:
            unchanged.append(r)

    print(f"\n  --- vs {prev_name or 'no baseline'} ---")
    print(f"  zones CHANGED  : {len(changed):,}   -> crawl these")
    print(f"  zones unchanged: {len(unchanged):,}   -> skip")
    print(f"  no baseline    : {len(brand_new):,}")
    if changed:
        changed.sort(key=lambda z: -abs(z["delta"]))
        pages = sum(z["total_pages"] or 0 for z in changed)
        allp = sum(r["total_pages"] or 0 for r in ok)
        print(f"  pages to crawl : {pages:,} of {allp:,} "
              f"({pages/allp*100:.0f}%)" if allp else "")
        print("\n  biggest movers:")
        for z in changed[:12]:
            print(f"     {z['delta']:>+6}  {z['prev_total']:>6,} -> "
                  f"{z['total_vendors']:>6,}  {z['name'][:34]}")

    if not args.rediff:
        ZONE_SNAPSHOT.write_text(json.dumps(
            {"month": ZONE_SNAPSHOT.stem.split("_")[-1],
             "probed": len(ok), "failed": len(bad), "zones": results},
            ensure_ascii=False), encoding="utf-8")
    ZONE_DELTA.write_text(json.dumps({
        "baseline": prev_name,
        "changed": len(changed), "unchanged": len(unchanged),
        "no_baseline": len(brand_new),
        "note": "totalVendors is NET per zone and NOT summable across zones - "
                "delivery zones overlap, so one restaurant appears in many.",
        "zones_changed": [{k: z[k] for k in
                           ("id", "name", "prev_total", "total_vendors",
                            "delta", "total_pages")} for z in changed],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  -> {ZONE_SNAPSHOT.name}")
    print(f"  -> {ZONE_DELTA.name}")
    print(f"  elapsed {time.time()-t0:.0f}s")
    print("\n  NEXT: crawl the changed zones, then detect_relistings.py")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--rediff", action="store_true",
                   help="recompute the delta from the existing snapshot, no requests")
    sys.exit(main(p.parse_args()))
