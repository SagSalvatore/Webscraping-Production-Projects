"""Rank the remaining areas for crawling, and classify their CHARACTER.

Why this exists: 586 areas / 94,320 pages is far more than the proxy budget
allows, so the order matters more than the total.

Two ranking inputs come from data we already hold - no research needed:
    total_vendors   Talabat's own count of vendors serving the area (density)
    new_rate_p1     fraction of page-1 results not already in our universe

QSR/FSR/Cafe composition was evaluated and REJECTED as a ranking signal: it is
~44%/32%/13% in every area measured, so it cannot discriminate between them.

What our data cannot tell us is area CHARACTER - 'Industrial Area 13' and
'Garden City' can have similar vendor counts but very different commercial
profiles. Tavily fills that gap, classifying each candidate as residential /
commercial / industrial / tourist so industrial zones can be de-prioritised if
the goal is lively QSR/FSR/Cafe districts.

    python prioritize_areas.py --dry-run       ranking only, no API calls
    python prioritize_areas.py --top 100       classify the top 100
    python prioritize_areas.py --probe         MEASURE new-rate, then rank

--probe exists because the ranking above turned out to have NO predictive
power. Measured against the top-20 crawl of 2026-08-11, the Spearman rank
correlation between `efficiency` and actual new-branches-per-page was -0.099 -
indistinguishable from random. The #1-ranked area (Garden City) returned 1 new
branch; ranks 13/16/19 returned 82% of all 2,961.

Two reasons the estimate failed:
  * new_rate_p1 was measured when the dedup set was much smaller, so it
    massively overstates novelty now that 30,769 branch_ids are known.
  * total_vendors counts vendors SERVING an area, and delivery zones overlap
    heavily, so the same vendor inflates many areas at once.

--probe fetches page 1 of each candidate area RIGHT NOW and counts how many of
its vendors are genuinely unknown. One request per area buys a measurement
instead of an estimate.
"""
import argparse
import asyncio
import json
import os
import re
import sys
from collections import defaultdict

import time
import uuid

import httpx
from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from loguru import logger

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")

OXY_U = os.getenv("OXYLABS_USERNAME", "")
OXY_P = os.getenv("OXYLABS_PASSWORD", "")
OXY_C = os.getenv("OXYLABS_COUNTRY", "ae")
RES = Path(__file__).resolve().parent / "smoke_results"
OUT = RES / "area_priority.json"
OUT_CSV = RES / "area_priority.csv"

TAVILY = os.getenv("TAVILY_API_KEY", "")

INDUSTRIAL = re.compile(r"\b(industrial|ind\.|jafza|freezone|free zone|mussafah|"
                        r"technopark|logistics|port|warehouse)\b", re.I)


def load_remaining():
    amap = json.load(open(RES / "area_map.json", encoding="utf-8"))
    ck = json.load(open(_ROOT / "data" / "urls" / "run2_checkpoint.json", encoding="utf-8"))
    done = defaultdict(set)
    for k in ck["done_pages"]:
        a, p = k.split("::")
        done[int(a)].add(int(p))
    per = defaultdict(set)
    with open(_ROOT / "data" / "urls" / "talabat_restaurant_urls.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                p = r.get("page_found")
                if isinstance(p, int):
                    per[r["area_id"]].add(p)
    run1 = {a for a, pg in per.items() if pg and len(pg) / max(pg) >= 0.6}

    out = []
    for a in amap:
        if not a.get("total_pages") or a["id"] in run1:
            continue
        d = len(done.get(a["id"], ()))
        if d >= a["total_pages"] * 0.95:
            continue
        rem = a["total_pages"] - d
        v = a.get("total_vendors") or 0
        nr = a.get("new_rate_p1") or 0
        out.append({
            "id": a["id"], "name": a["name"], "url": a["url"],
            "total_vendors": v, "new_rate_p1": nr,
            "pages_left": rem,
            "expected_new": round(v * nr),
            # efficiency = expected new per page spent. Absolute value is
            # inflated by cross-area overlap; use it for ORDER, not totals.
            "efficiency": round(v * nr / max(rem, 1), 2),
            "looks_industrial": bool(INDUSTRIAL.search(a["name"])),
        })
    return sorted(out, key=lambda x: -x["efficiency"])


def load_known_branch_ids():
    """Every branch_id we already hold - run-1 plus run-2."""
    known = set()
    for p in (_ROOT / "data" / "urls" / "talabat_restaurant_urls.jsonl",
              _ROOT / "data" / "urls" / "talabat_restaurant_urls_run2.jsonl"):
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    bid = json.loads(line).get("branch_id")
                    if bid:
                        known.add(bid)
    return known


async def probe_area(sess, area, known, sem, state, args):
    """Fetch page 1 and count vendors we do NOT already have."""
    from url_collector.parser import parse_listing_page
    url = f"{area['url']}?page=1"
    for attempt in range(3):
        # honour a cooldown opened by another probe
        while state["until"] > time.time():
            await asyncio.sleep(min(state["until"] - time.time(), 5))
        async with sem:
            try:
                sid = uuid.uuid4().hex[:8]
                px = (f"http://customer-{OXY_U}-cc-{OXY_C}-sessid-{sid}:"
                      f"{OXY_P}@pr.oxylabs.io:7777")
                r = await sess.get(url, timeout=60,
                                   proxies={"http": px, "https": px},
                                   headers={"Accept-Language": "en-US,en;q=0.9"})
                if r.status_code == 200:
                    res = parse_listing_page(r.text, 1)
                    ids = [v.branch_id for v in res.vendors if v.branch_id]
                    new = [b for b in ids if b not in known]
                    area["probe_seen"] = len(ids)
                    area["probe_new"] = len(new)
                    area["probe_new_rate"] = round(len(new) / len(ids), 3) if ids else 0.0
                    # what the whole area is worth at the observed rate
                    area["probe_expected_new"] = round(
                        area["probe_new_rate"] * 15 * (area["pages_left"] or 0))
                    state["ok"] += 1
                    return
                if r.status_code == 429:
                    state["until"] = time.time() + args.cooldown
                    state["429"] += 1
            except Exception:
                pass
        await asyncio.sleep(2 * (attempt + 1))
    area["probe_new_rate"] = None
    state["fail"] += 1


async def run_probe(args):
    areas = load_remaining()
    if args.limit:
        areas = areas[: args.limit]
    known = load_known_branch_ids()
    logger.info(f"known branch_ids: {len(known):,} | probing {len(areas)} areas "
                f"(1 request each) at {args.rate}/s")

    sem = asyncio.Semaphore(args.concurrency)
    state = {"ok": 0, "fail": 0, "429": 0, "until": 0.0}
    t0 = time.time()
    async with AsyncSession(impersonate="chrome124") as sess:
        tasks = [probe_area(sess, a, known, sem, state, args) for a in areas]
        for i, fut in enumerate(asyncio.as_completed(tasks), 1):
            await fut
            if i % 50 == 0:
                el = time.time() - t0
                logger.info(f"  {i}/{len(areas)} | ok {state['ok']} "
                            f"429s {state['429']} failed {state['fail']} | "
                            f"ETA {(len(areas)-i)/(i/el)/60:.0f} min")

    measured = [a for a in areas if a.get("probe_new_rate") is not None]
    measured.sort(key=lambda a: -a["probe_expected_new"])
    json.dump(measured, open(RES / "area_probe_ranked.json", "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    import csv as _csv
    with open(RES / "area_probe_ranked.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = _csv.DictWriter(f, fieldnames=["id", "name", "probe_seen", "probe_new",
                                           "probe_new_rate", "probe_expected_new",
                                           "pages_left", "character", "efficiency", "url"],
                            extrasaction="ignore")
        w.writeheader()
        w.writerows(measured)

    logger.success(f"probed {len(measured)}/{len(areas)} in {(time.time()-t0)/60:.1f} min "
                   f"| 429s {state['429']} | failed {state['fail']}")
    dead = sum(1 for a in measured if a["probe_new"] == 0)
    logger.info(f"  areas with ZERO new on page 1 (skip these): {dead}")
    logger.info("\n  TOP 25 BY MEASURED VALUE:")
    for i, a in enumerate(measured[:25], 1):
        logger.info(f"   {i:>2}. {a['name'][:28]:30} new {a['probe_new']:>2}/{a['probe_seen']:<2} "
                    f"({a['probe_new_rate']*100:>3.0f}%)  pages={a['pages_left']:>4} "
                    f"-> ~{a['probe_expected_new']:,} expected")
    logger.info(f"\n  -> smoke_results/area_probe_ranked.csv")
    return 0


async def classify(client, sem, area, results):
    q = f"{area['name']} UAE area residential commercial industrial restaurants cafes"
    async with sem:
        try:
            r = await client.post("https://api.tavily.com/search",
                                  json={"query": q, "max_results": 3,
                                        "search_depth": "basic"},
                                  headers={"Authorization": f"Bearer {TAVILY}"})
            if r.status_code != 200:
                area["character"] = "unknown"
                results.append(area)
                return
            blob = " ".join((x.get("title", "") + " " + (x.get("content") or ""))
                            for x in (r.json().get("results") or []))[:2500]
        except Exception:
            area["character"] = "unknown"
            results.append(area)
            return

    low = blob.lower()
    scores = {
        "industrial": len(re.findall(r"industrial|warehouse|factory|labour camp|logistics", low)),
        "residential": len(re.findall(r"residential|villa|apartment|community|family|neighbourhood", low)),
        "commercial": len(re.findall(r"mall|shopping|business|office|retail|commercial", low)),
        "tourist": len(re.findall(r"beach|hotel|resort|tourist|attraction|corniche", low)),
    }
    top = max(scores, key=scores.get)
    area["character"] = top if scores[top] else "unknown"
    area["character_scores"] = scores
    area["restaurant_mentions"] = len(re.findall(r"restaurant|cafe|dining|eatery", low))
    results.append(area)


async def main(args):
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | {message}")

    areas = load_remaining()
    logger.info(f"remaining areas: {len(areas)} | pages left: "
                f"{sum(a['pages_left'] for a in areas):,}")

    if args.dry_run or not TAVILY:
        if not TAVILY:
            logger.warning("no TAVILY_API_KEY - ranking only")
        for a in areas[:20]:
            logger.info(f"  {a['name'][:30]:32} vend={a['total_vendors']:>5} "
                        f"new={a['new_rate_p1']*100:>3.0f}% pages={a['pages_left']:>4} "
                        f"eff={a['efficiency']}")
        json.dump(areas, open(OUT, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        logger.info(f"-> {OUT.name}")
        return 0

    target = areas[: args.top]
    logger.info(f"classifying character of top {len(target)} via Tavily …")
    sem = asyncio.Semaphore(4)
    results = []
    async with httpx.AsyncClient(timeout=40) as client:
        await asyncio.gather(*(classify(client, sem, a, results) for a in target))

    from collections import Counter
    logger.success(f"classified {len(results)}")
    logger.info(f"  character mix: {dict(Counter(r.get('character') for r in results))}")

    # merge back, unclassified keep character=None
    by_id = {r["id"]: r for r in results}
    for a in areas:
        if a["id"] in by_id:
            a.update(by_id[a["id"]])

    json.dump(areas, open(OUT, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    import csv as _csv
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = _csv.DictWriter(f, fieldnames=["id", "name", "total_vendors", "new_rate_p1",
                                           "pages_left", "expected_new", "efficiency",
                                           "character", "restaurant_mentions",
                                           "looks_industrial", "url"],
                            extrasaction="ignore")
        w.writeheader()
        w.writerows(areas)

    lively = [a for a in results
              if a.get("character") in ("residential", "commercial", "tourist")]
    logger.info(f"\n  LIVELY areas (residential/commercial/tourist): {len(lively)}")
    logger.info(f"  industrial/unknown de-prioritised: {len(results)-len(lively)}")
    pg = sum(a["pages_left"] for a in lively)
    logger.info(f"  pages for lively set: {pg:,} (~{pg/0.6/3600:.1f} h)")
    logger.info("\n  top 20 LIVELY by efficiency:")
    for a in sorted(lively, key=lambda x: -x["efficiency"])[:20]:
        logger.info(f"    {a['name'][:28]:30} {a['character']:12} vend={a['total_vendors']:>5} "
                    f"pages={a['pages_left']:>4} eff={a['efficiency']}")
    logger.info(f"\n  -> {OUT_CSV.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=100)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--probe", action="store_true",
                   help="measure page-1 new-rate live, then rank on it")
    p.add_argument("--limit", type=int, help="probe only the first N areas")
    p.add_argument("--concurrency", type=int, default=6)
    p.add_argument("--rate", type=float, default=0.75)
    p.add_argument("--cooldown", type=float, default=120)
    a = p.parse_args()
    if a.probe:
        logger.remove()
        logger.add(sys.stderr, level="INFO",
                   format="<green>{time:HH:mm:ss}</green> | {message}")
        logger.add(Path(__file__).resolve().parent / "logs" / "probe.log",
                   level="DEBUG", encoding="utf-8", rotation="10 MB")
        sys.exit(asyncio.run(run_probe(a)))
    sys.exit(asyncio.run(main(a)))
