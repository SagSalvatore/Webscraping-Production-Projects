"""Re-fetch contact_phone and maps_url PER BRANCH for the smeared branches.

WHY. The same brand-level Google lookup that smeared the address also smeared
the phone and the Maps link: all 1,617 July smear groups share one identical
phone, and 1,613 share one identical maps_url. Those values belong to one
branch and were copied onto the rest.

THE QUERY IS PER BRANCH, NOT PER BRAND - that is the whole point:
    "{name} {area} uae"        area = the real one just repaired from Talabat
A brand-only query is what produced the defect; adding the branch's own area is
what separates its branches from each other.

VERIFICATION, because a Serper result is a claim, not an answer. A returned
place is accepted only if BOTH hold:
    distance  within MATCH_M of THAT branch's own Talabat coordinates
    name      the brand leads the returned title (name_matches_strict), the
              rule that stopped "Juice Center" collecting three unrelated shops
Nothing else is trusted. A brand like Starbucks will happily return a Starbucks
for every query; only the coordinates prove it is the right one.

NO MATCH -> "NA", never a guess, and never the brand-level value we are removing.

ONE ROW OUT PER ROW IN. Every input branch appears in the output with its
status, so the file can be joined straight back.

WRITING IS A SEPARATE STEP (apply_contact_fix.py) because the cohorts disagree
on how "missing" is stored - July uses null, August and unified use "NA" - and
that has to be honoured per file rather than normalised.

    python serper_contact_fill.py --dry-run
    python serper_contact_fill.py --calibrate 30   compare query templates first
    python serper_contact_fill.py
"""
import argparse
import asyncio
import csv
import json
import math
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

import httpx
import orjson
from dotenv import load_dotenv
from loguru import logger

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "August_classification"))
from serper_places import Pacer, name_matches_strict, outside_uae   # noqa: E402

load_dotenv(ROOT / ".env", override=True)
DATA = HERE / "data"
ROWS = DATA / "address_smear_rows.csv"
FIXED = DATA / "area_fix_applied.csv"
CACHE = DATA / "serper_branch_cache.json"
OUT = DATA / "branch_contacts.csv"
MAPS_URL = "https://google.serper.dev/maps"

TEMPLATE = "{name} {area} uae"
MATCH_M = 300.0          # a Google place further than this is a different branch
CONCURRENCY, QPS = 20, 30.0
CHECKPOINT_EVERY = 200
sys.stdout.reconfigure(encoding="utf-8")


def as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def m_apart(a, b):
    # csv.DictReader hands back STRINGS. math.radians("25.1") raises, the
    # except swallowed it, every candidate was rejected on distance and the
    # calibration scored 0/30 for both templates - a silent total failure that
    # looked like "Serper found nothing".
    vals = [as_float(x) for x in (a[0], a[1], b[0], b[1])]
    if any(v is None for v in vals):
        return None
    try:
        la1, lo1, la2, lo2 = map(math.radians, vals)
    except (TypeError, ValueError):
        return None
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


def load_branches():
    """branch_id -> name, repaired area, coordinates. One row per branch."""
    area_now = {}
    if FIXED.exists():
        for r in csv.DictReader(open(FIXED, encoding="utf-8-sig")):
            area_now.setdefault(r["source_id"], r["area_after"])
    out = {}
    for r in csv.DictReader(open(ROWS, encoding="utf-8-sig")):
        if r["dataset"] == "unified_202608" or r["source_id"] in out:
            continue
        out[r["source_id"]] = {
            "branch_id": r["source_id"], "name": r["name"],
            # the repaired area if this branch got one, else whatever it had
            "area": area_now.get(r["source_id"]) or r["file_area"],
            "lat": r["lat"], "lng": r["lng"],
            "old_phone": r["file_phone"], "old_maps_url": r["file_maps_url"],
        }
    return list(out.values())


def load_cache():
    if CACHE.exists():
        try:
            return orjson.loads(CACHE.read_bytes())
        except Exception:
            logger.warning("cache unreadable - starting fresh")
    return {}


def save_cache(c):
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_bytes(orjson.dumps(c))
    tmp.replace(CACHE)


async def fetch(client, b, key, sem, pacer, cache, stats, template):
    if b["branch_id"] in cache:
        stats["cached"] += 1
        return
    q = template.format(name=b["name"], area=b["area"] or "").strip()
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
                    cache[b["branch_id"]] = {
                        "query": q, "places": d.get("places") or d.get("maps") or []}
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
    cache[b["branch_id"]] = {"query": q, "places": [], "failed": True}
    stats["failed"] += 1


_AR = re.compile(r"[؀-ۿ]+")


def compact(s):
    return re.sub(r"[^a-z0-9]", "", _AR.sub(" ", str(s or "")).lower())


def same_brand(name, title):
    """Corroborates the brand once DISTANCE has already proved the location.

    name_matches_strict was too strict here: it rejected 'Sushi Art' against
    'SushiArt - سوشي ارت - Mirdif' over a single space, on places 0-19 m away -
    unambiguously the same branch. It also cannot match when OUR name is the
    longer one ('Starbucks - Mall of the Emirates' vs a title of 'Starbucks').

    So: strip Arabic, drop every non-alphanumeric, and accept when either
    compacted string starts the other. Still safe, because the dangerous case
    is a shared word in the MIDDLE - 'Juice Center' vs 'Haji Ali Juice Center'
    compacts to 'juicecenter' vs 'hajialijuicecenter', neither a prefix of the
    other, correctly rejected. And nothing is accepted beyond MATCH_M anyway.
    """
    a, b = compact(name), compact(title)
    if len(a) < 4 or len(b) < 4:
        return False
    return a.startswith(b) or b.startswith(a)


def pick(b, entry):
    """The one place that is provably THIS branch, or None."""
    best, bestd = None, None
    for p in entry.get("places") or []:
        if outside_uae(p.get("latitude"), p.get("longitude")):
            continue
        if not same_brand(b["name"], p.get("title") or ""):
            continue
        d = m_apart((b["lat"], b["lng"]), (p.get("latitude"), p.get("longitude")))
        if d is None or d > MATCH_M:
            continue
        if bestd is None or d < bestd:
            best, bestd = p, d
    return best, bestd


async def run(branches, key, cache, template, label):
    stats = Counter()
    sem, pacer = asyncio.Semaphore(CONCURRENCY), Pacer(QPS)
    t0 = time.time()
    todo = [b for b in branches if b["branch_id"] not in cache]
    logger.info(f"{label}: {len(todo):,} to query (1 credit each), "
                f"{len(branches)-len(todo):,} cached")
    try:
        async with httpx.AsyncClient() as client:
            tasks = [fetch(client, b, key, sem, pacer, cache, stats, template)
                     for b in todo]
            for i, f in enumerate(asyncio.as_completed(tasks), 1):
                await f
                if i % CHECKPOINT_EVERY == 0:
                    save_cache(cache)
                    el = time.time() - t0
                    logger.info(f"  {i:,}/{len(todo):,} | {i/el:.1f}/s | "
                                f"ETA {(len(todo)-i)/(i/el)/60:.0f} min | {dict(stats)}")
    finally:
        save_cache(cache)
    return stats


async def main(args):
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
    key = os.getenv("SERPER_API_KEY", "")
    if not key:
        logger.error("SERPER_API_KEY missing from talabat/.env")
        return 1

    branches = load_branches()
    print("=" * 78)
    print(f"  SERPER per-branch contact + maps_url   ({len(branches):,} branches)")
    print("=" * 78)
    with_area = sum(1 for b in branches if b["area"])
    with_geo = sum(1 for b in branches if b["lat"])
    print(f"  with a repaired area : {with_area:,}")
    print(f"  with coordinates     : {with_geo:,}   (needed to verify a match)")
    print(f"  query template       : {TEMPLATE!r}")
    print(f"  accept radius        : {MATCH_M:.0f} m + leading-name match")

    async with httpx.AsyncClient() as c:
        r = await c.get("https://google.serper.dev/account",
                        headers={"X-API-KEY": key}, timeout=20)
        bal = r.json().get("balance") if r.status_code == 200 else "?"
    print(f"  serper balance       : {bal}")

    if args.dry_run:
        print(f"\n  --dry-run: would spend {sum(1 for b in branches):,} credits")
        return 0

    cache = load_cache()

    # CALIBRATE: the measured template in this project is "{name} uae
    # restaurant". Adding the area is a change, so compare them on a sample
    # before spending on 4,964.
    if args.calibrate:
        sample = [b for b in branches if b["lat"] and b["area"]][: args.calibrate]
        res = {}
        for tpl, nm in ((TEMPLATE, "name+area"), ("{name} uae restaurant", "name only")):
            c2 = {}
            await run(sample, key, c2, tpl, nm)
            hit = sum(1 for b in sample if pick(b, c2.get(b["branch_id"], {}))[0])
            res[nm] = hit
            print(f"  {nm:10} verified match on {hit}/{len(sample)}")
        print(f"\n  -> use {'name+area' if res['name+area'] >= res['name only'] else 'name only'}")
        return 0

    await run(branches, key, cache, TEMPLATE, "full run")

    rows, st = [], Counter()
    for b in branches:
        e = cache.get(b["branch_id"], {})
        p, d = pick(b, e)
        st["matched" if p else "no_match"] += 1
        rows.append({
            "branch_id": b["branch_id"], "name": b["name"], "area": b["area"],
            "query": e.get("query", ""), "status": "matched" if p else "NA",
            "matched_title": (p or {}).get("title", ""),
            "distance_m": f"{d:.0f}" if d is not None else "",
            "phone": (p or {}).get("phoneNumber") or "",
            "maps_url": (f"https://www.google.com/maps?cid={p['cid']}"
                         if p and p.get("cid") else ""),
            "address": (p or {}).get("address") or "",
            "website": (p or {}).get("website") or "",
            "old_phone": b["old_phone"], "old_maps_url": b["old_maps_url"],
            "places_returned": len(e.get("places") or []),
        })
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"\n  verified match : {st['matched']:,}")
    print(f"  no match -> NA : {st['no_match']:,}")
    ph = sum(1 for r in rows if r["phone"])
    mu = sum(1 for r in rows if r["maps_url"])
    print(f"  phone found    : {ph:,} ({ph/len(rows)*100:.1f}%)")
    print(f"  maps_url found : {mu:,} ({mu/len(rows)*100:.1f}%)")
    print(f"  distinct phones now: "
          f"{len({r['phone'] for r in rows if r['phone']}):,} "
          f"(was {len({r['old_phone'] for r in rows if r['old_phone']}):,})")
    print(f"\n  -> {OUT.relative_to(ROOT)}  ({len(rows):,} rows, one per branch)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--calibrate", type=int, metavar="N",
                   help="compare query templates on N branches first")
    sys.exit(asyncio.run(main(p.parse_args())))
