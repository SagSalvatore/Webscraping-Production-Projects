"""Stage 3 - Tavily, FALLBACK ONLY. Not run for every restaurant.

Serper answers "how many UAE locations" directly. Tavily answers a different
question that Places cannot: the NARRATIVE - "founded in Lebanon, 40 outlets
across the GCC" - which is what separates MNC Chain from Local Chain.

It is therefore fired only where Serper leaves a real gap:
  A. uae_locations == 0  -> no Maps presence found. Is it genuinely a single
                            unlisted outlet, or a name too generic to match?
  B. uae_locations >= 2 AND brand not in the MNC seed -> Chain confirmed, but
                            Local vs MNC still needs evidence.

Query is "{name} uae restaurant" - the SAME template as Serper, per Sagar.
(The older tavily/reclass_low_confidence.py used "{name} Dubai" because the
UAE phrasing pulled generic market-research articles. That was a different
prompt and a different downstream consumer; here the same wording that scored
15/15 on Serper is used so both sources describe the same entity. If snippets
come back generic rather than brand-specific, this is the first thing to
revisit - the observation behind the old choice was real.)

Keys rotate on 429. IMPORTANT: a 429 is a RATE LIMIT, not an exhausted key -
retiring keys on the first 429 once dropped resolution from 130/194 to 21/194.
Only retire on an explicit usage-limit message in the body.

    python tavily_fallback.py --dry-run
    python tavily_fallback.py --limit 50
    python tavily_fallback.py
"""
import argparse
import asyncio
import csv
import json
import sys
import time
from collections import Counter

import httpx
from loguru import logger

from config import (LOGS, MNC_SEED, PLACES_OUT, TAVILY_CACHE, TAVILY_QUERY,
                    TAV_KEYS)
from rules import norm_name

TAVILY_URL = "https://api.tavily.com/search"
# MEASURED: 12 concurrent collapsed throughput to ~0.2/s - Tavily's free tier
# throttles hard and holds connections, so requests pile up in backoff. Same
# lesson as the Talabat crawler: pacing beats hammering.
CONCURRENCY = 4
CHECKPOINT_EVERY = 100
MAX_CHARS = 1200


def load_keys():
    keys = []
    try:
        with open(TAV_KEYS, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                k = (r.get("Keys") or "").strip()
                if k.startswith("tvly"):
                    keys.append(k)
    except FileNotFoundError:
        pass
    return keys


def load_cache():
    if TAVILY_CACHE.exists():
        try:
            return json.loads(TAVILY_CACHE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("tavily cache unreadable - starting fresh")
    return {}


def save_cache(c):
    tmp = TAVILY_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    tmp.replace(TAVILY_CACHE)


def needs_tavily(footprint):
    """Which brands actually warrant a search - see module docstring."""
    out = []
    for brand, v in footprint.items():
        if v.get("failed"):
            out.append((brand, "serper_failed"))
        elif v["uae_locations"] == 0:
            out.append((brand, "no_maps_presence"))
        elif v["uae_locations"] >= 2 and norm_name(brand) not in MNC_SEED:
            out.append((brand, "chain_needs_mnc_vs_local"))
    return out


class KeyPool:
    def __init__(self, keys):
        self.keys = list(keys)
        self.i = 0
        self.dead = set()
        self.lock = asyncio.Lock()

    async def get(self):
        async with self.lock:
            for _ in range(len(self.keys)):
                k = self.keys[self.i % len(self.keys)]
                self.i += 1
                if k not in self.dead:
                    return k
            return None

    async def retire(self, k, body: str):
        # ONLY on an explicit plan/usage limit. A bare 429 is a rate limit.
        if any(s in (body or "").lower()
               for s in ("usage limit", "plan limit", "quota exceeded",
                         "credits exhausted", "exceeded your")):
            async with self.lock:
                self.dead.add(k)
                logger.warning(f"key retired (usage limit): ...{k[-6:]} "
                               f"| {len(self.dead)}/{len(self.keys)} dead")

    async def retire_hard(self, k, why: str):
        """401/403 - the key itself is invalid or the account is exhausted.
        Distinct from a 429, which is transient and must NOT retire a key."""
        async with self.lock:
            if k not in self.dead:
                self.dead.add(k)
                logger.warning(f"key retired ({why}): ...{k[-6:]} "
                               f"| {len(self.dead)}/{len(self.keys)} dead")


def preflight(keys):
    """One cheap call per key before the run. Drops ONLY permanently dead keys.

    A 429 here means the key is RATE LIMITED right now, not exhausted - it will
    work again shortly. An earlier version dropped keys on any non-200 and,
    after a burst of traffic had rate-limited every key, concluded "every tavily
    key is dead" and refused to run at all. That is the same mistake recorded
    from a previous project: retiring on the first 429 once cut resolution from
    130/194 to 21/194.

    Only 401/403 (invalid key, or account usage exhausted) are permanent.
    """
    import requests
    good, limited, dead = [], [], []
    for k in keys:
        try:
            r = requests.post(TAVILY_URL, timeout=30, json={
                "api_key": k, "query": "test uae restaurant", "max_results": 1})
            if r.status_code == 200:
                good.append(k)
            elif r.status_code in (401, 403):
                dead.append(k)
                logger.warning(f"  key ...{k[-6:]} DEAD: HTTP {r.status_code}")
            else:
                # transient (429, 5xx) - keep it, the run will back off
                limited.append(k)
                logger.info(f"  key ...{k[-6:]} rate-limited now, keeping it "
                            f"(HTTP {r.status_code})")
        except Exception as exc:
            limited.append(k)
            logger.info(f"  key ...{k[-6:]} probe failed ({type(exc).__name__}), keeping it")
    logger.info(f"  keys: {len(good)} ready, {len(limited)} rate-limited, {len(dead)} dead")
    return good + limited


async def search(client, brand, reason, pool, sem, cache, stats):
    if brand in cache:
        return
    q = TAVILY_QUERY.format(name=brand)
    for attempt in range(4):
        key = await pool.get()
        if not key:
            stats["no_keys_left"] += 1
            return
        async with sem:
            try:
                r = await client.post(TAVILY_URL, timeout=25, json={
                    "api_key": key, "query": q, "max_results": 4,
                    "search_depth": "basic"})
                if r.status_code == 200:
                    d = r.json()
                    txt = " ".join(
                        f"{x.get('title','')}. {x.get('content','')}"
                        for x in (d.get("results") or []))[:MAX_CHARS]
                    cache[brand] = {"query": q, "reason": reason, "text": txt,
                                    "n_results": len(d.get("results") or [])}
                    stats["ok" if txt else "empty"] += 1
                    return
                if r.status_code == 429:
                    stats["429"] += 1
                    await pool.retire(key, r.text)
                    await asyncio.sleep(1.5 * (2 ** attempt))
                    continue
                if r.status_code in (401, 403):
                    # invalid / exhausted key - permanent. Drop and retry with
                    # another immediately. A 429 above is transient and must
                    # never reach this branch.
                    stats[f"http_{r.status_code}"] += 1
                    await pool.retire_hard(key, f"HTTP {r.status_code}")
                    continue
                stats[f"http_{r.status_code}"] += 1
                await asyncio.sleep(1.5 * (2 ** attempt))
            except Exception as exc:
                stats[type(exc).__name__] += 1
                await asyncio.sleep(1.5 * (2 ** attempt))
    cache[brand] = {"query": q, "reason": reason, "text": "", "failed": True}
    stats["failed"] += 1


async def main(args):
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
    logger.add(LOGS / "tavily.log", level="DEBUG", encoding="utf-8", rotation="10 MB")

    if not PLACES_OUT.exists():
        logger.error(f"{PLACES_OUT.name} missing - run serper_places.py first")
        return 1
    footprint = json.loads(PLACES_OUT.read_text(encoding="utf-8"))
    want = needs_tavily(footprint)
    logger.info(f"brands with a Serper footprint: {len(footprint):,}")
    logger.info(f"  need Tavily: {len(want):,} | {dict(Counter(r for _, r in want))}")

    keys = load_keys()
    logger.info(f"  tavily keys in file: {len(keys)}")
    if not keys:
        logger.error("no tavily keys in tav_keys.csv")
        return 1
    if not args.dry_run and not args.no_preflight:
        keys = preflight(keys)
        logger.info(f"  keys usable after preflight: {len(keys)}")
        if not keys:
            logger.error("every tavily key is dead")
            return 1

    if args.only_reason:
        before = len(want)
        want = [(b, r) for b, r in want if r == args.only_reason]
        logger.info(f"  --only-reason {args.only_reason}: {len(want):,} of {before:,}")

    cache = load_cache()
    todo = [(b, r) for b, r in want if b not in cache]
    if args.limit:
        todo = todo[: args.limit]
        logger.warning(f"--limit: {len(todo)} brands only")
    logger.info(f"  cached {len(want)-len(todo):,} | to search {len(todo):,}")

    if args.dry_run:
        logger.warning("--dry-run: no calls")
        return 0

    stats = Counter()
    pool = KeyPool(keys)
    sem = asyncio.Semaphore(CONCURRENCY)
    t0 = time.time()
    try:
        async with httpx.AsyncClient() as client:
            tasks = [search(client, b, r, pool, sem, cache, stats) for b, r in todo]
            for i, fut in enumerate(asyncio.as_completed(tasks), 1):
                await fut
                if i % CHECKPOINT_EVERY == 0:
                    save_cache(cache)
                    el = time.time() - t0
                    logger.info(f"  {i:,}/{len(todo):,} | {i/el:.1f}/s | {dict(stats)}")
    finally:
        save_cache(cache)
    logger.success(f"tavily done in {(time.time()-t0)/60:.1f} min | {dict(stats)}")
    logger.info(f"  keys retired: {len(pool.dead)}/{len(keys)}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--no-preflight", action="store_true",
                   help="skip the per-key health check")
    p.add_argument("--only-reason",
                   choices=["chain_needs_mnc_vs_local", "no_maps_presence",
                            "serper_failed"],
                   help="scope to one trigger. chain_needs_mnc_vs_local is the "
                        "high-value set: it is the only one where Tavily "
                        "changes an answer. no_maps_presence brands default to "
                        "Independent with or without a search.")
    p.add_argument("--cohort", default="aug",
                   help="aug | sep - consumed by config at import time, "
                        "declared here only so argparse accepts it")
    sys.exit(asyncio.run(main(p.parse_args())))
