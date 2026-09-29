"""August menu capture for the 7,261 genuinely-new Talabat brands.

FIRST CAPTURE ONLY - no baseline, no delta, no hashing, no Excel. Change
tracking starts next month against whatever this run stores, so this script
deliberately does one job: pull each restaurant's current menu and write it
flat. `talabat/menu/talabat_menu_tracker.py` is untouched.

Seven fields per menu item, exactly as specified:
    branch_id  item_id  item_name  item_key  category  price_aed  description

Where each comes from - all of it is in the page's __NEXT_DATA__ at
props.pageProps.initialMenuState.menuData.items:

    item_id      the item's own `id` (int, e.g. 383390315). It is Talabat's
                 server-side id and comes from the PAGE, not the URL - the URL
                 only carries branch_id in its path and the ?aid= zone.
    category     `originalSection` (the menu section as the restaurant named
                 it), not `sectionName`, which can be a display variant.
    price_aed    `price` verbatim. Talabat's web SSR is already AED, NOT fils -
                 do not divide by 100.
    item_key     lowercase + collapsed whitespace, identical to make_item_key()
                 in the existing tracker so August rows join cleanly onto the
                 15k already stored.

The ?aid= query parameter is LOAD-BEARING. Without it Talabat serves a page
with no initialMenuState at all and every restaurant silently yields 0 items.
The input URLs already carry it; never strip it.

Pacing: a global rate limiter plus a circuit breaker that pauses EVERY worker
on a 429. Retrying harder against a global limit turned a brief throttle into
73% total failure twice during the listing crawl - the breaker is what fixed it.

    python scrape_menus.py --limit 20      smoke test
    python scrape_menus.py                 full run
    python scrape_menus.py --retry-failed  only the failures
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from loguru import logger

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
load_dotenv(ROOT / ".env", override=True)

# Defaults target the August new-brand batch. --source/--outdir re-point the
# SAME proven scraper at any other cohort (e.g. the monthly refresh of the
# existing 15,199) rather than forking a second copy of it.
SRC = ROOT / "listing_comparison" / "output" / "talabat_genuinely_new_brands.jsonl"
DATA = HERE / "data"
LOGS = HERE / "logs"
ITEMS_OUT = REST_OUT = CHECKPOINT = FAILED = None


def set_paths(source=None, outdir=None):
    """Re-point input/outputs. Every output lives under outdir so two cohorts
    can never share a checkpoint - that would make one resume over the other."""
    global SRC, DATA, LOGS, ITEMS_OUT, REST_OUT, CHECKPOINT, FAILED
    if source:
        SRC = Path(source)
    if outdir:
        DATA = Path(outdir)
        LOGS = DATA / "logs"
    DATA.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    ITEMS_OUT = DATA / "menu_items.jsonl"          # one row per menu item
    REST_OUT = DATA / "restaurant_status.jsonl"    # one row per restaurant
    CHECKPOINT = DATA / "checkpoint.json"
    FAILED = DATA / "failed.jsonl"


set_paths()

U = os.getenv("OXYLABS_USERNAME", "")
P = os.getenv("OXYLABS_PASSWORD", "")
C = os.getenv("OXYLABS_COUNTRY", "ae")

CHECKPOINT_EVERY = 100

# NOTE: this scraper writes RAW text deliberately - no cleaning here.
# Cleaning is stage 2 (sanitize_menus.py) so the raw capture is preserved and
# every transformation is reproducible and auditable against it.
#
# A cleaning call DID live here and was a silent no-op: sanitize_items() keys
# off `category`/`item_name`/`description`, but Talabat's raw items use
# `originalSection`/`name`, so nothing matched and emojis passed straight
# through. Cleaning the raw dict shape is the trap - clean the OUTPUT shape.


def px(sid):
    # http:// even for https targets - the proxy hop is an HTTP CONNECT tunnel
    return f"http://customer-{U}-cc-{C}-sessid-{sid}:{P}@pr.oxylabs.io:7777"


def now():
    return datetime.now(timezone.utc).isoformat()


def make_item_key(name: str) -> str:
    """Identical to make_item_key() in talabat_menu_tracker.py - keep in sync."""
    return re.sub(r"\s+", " ", str(name).lower().strip())


def parse_menu(html: str) -> list[dict] | None:
    """Returns the raw item list, or None if the page carried no menu state."""
    tag = BeautifulSoup(html, "html.parser").find("script", {"id": "__NEXT_DATA__"})
    if not tag or not tag.string:
        return None
    try:
        nd = json.loads(tag.string)
    except (json.JSONDecodeError, ValueError):
        return None
    try:
        return nd["props"]["pageProps"]["initialMenuState"]["menuData"]["items"] or []
    except (KeyError, TypeError):
        return None                      # no menu state - distinct from 0 items


class Runner:
    def __init__(self, args):
        self.args = args
        self.done: set[int] = set()
        self.items_written = 0
        self.stats = Counter()
        self.fail_reasons = Counter()
        self.breaker_until = 0.0
        self.breaker_trips = 0
        self.rate_lock = asyncio.Lock()
        self.next_slot = 0.0
        self.lock = asyncio.Lock()
        self.items_f = None
        self.rest_f = None
        self.t0 = 0.0
        self.total = 0
        self.start_done = 0   # checkpoint size at launch; excluded from rate

    def load_state(self):
        if CHECKPOINT.exists():
            try:
                d = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
                self.done = set(d.get("done_branch_ids", []))
                logger.info(f"resuming: {len(self.done):,} restaurants already done")
            except Exception as e:
                logger.warning(f"checkpoint unreadable ({e}) - starting fresh")
        if ITEMS_OUT.exists():
            self.items_written = sum(1 for l in open(ITEMS_OUT, encoding="utf-8")
                                     if l.strip())
            logger.info(f"existing item rows: {self.items_written:,}")

    def save_checkpoint(self):
        tmp = CHECKPOINT.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "saved_at": now(),
            "restaurants_done": len(self.done),
            "items_written": self.items_written,
            "done_branch_ids": sorted(self.done),
        }), encoding="utf-8")
        tmp.replace(CHECKPOINT)

    # ---------------- pacing / breaker ----------------
    async def wait_breaker(self):
        while True:
            d = self.breaker_until - time.time()
            if d <= 0:
                return
            await asyncio.sleep(min(d, 5))

    async def trip_breaker(self):
        async with self.lock:
            t = time.time()
            if t < self.breaker_until:
                return                    # already open; don't stack
            self.breaker_trips += 1
            self.breaker_until = t + self.args.cooldown
            logger.warning(f"429 - breaker OPEN {self.args.cooldown}s "
                           f"(trip #{self.breaker_trips})")

    async def pace(self):
        if not self.args.rate:
            return
        gap = 1.0 / self.args.rate
        async with self.rate_lock:
            slot = max(time.time(), self.next_slot)
            self.next_slot = slot + gap
        w = slot - time.time()
        if w > 0:
            await asyncio.sleep(w)

    # ---------------- fetch ----------------
    async def fetch(self, sess, url, sem):
        sid = uuid.uuid4().hex[:8]
        reason = "unknown"
        for attempt in range(self.args.attempts):
            await self.wait_breaker()
            await self.pace()
            async with sem:
                try:
                    r = await sess.get(url, timeout=60,
                                       proxies={"http": px(sid), "https": px(sid)},
                                       headers={"Accept-Language": "en-US,en;q=0.9"})
                    if r.status_code == 200:
                        return r.text, None
                    reason = f"HTTP {r.status_code}"
                    if r.status_code in (403, 429, 503):
                        sid = uuid.uuid4().hex[:8]
                        if r.status_code == 429:
                            await self.trip_breaker()
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {str(exc)[:100]}"
                    sid = uuid.uuid4().hex[:8]
            await asyncio.sleep(min(self.args.backoff * (2 ** attempt), 60))
        return None, reason

    async def do_one(self, sess, rec, sem):
        bid = rec["branch_id"]
        if bid in self.done:
            return
        html, reason = await self.fetch(sess, rec["url"], sem)
        if html is None:
            async with self.lock:
                self.stats["failed"] += 1
                self.fail_reasons[reason] += 1
                with open(FAILED, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"branch_id": bid, "url": rec["url"],
                                        "reason": reason, "at": now()}) + "\n")
            return

        raw = parse_menu(html)
        if raw is None:
            # page loaded but had no menu state at all - a real signal, not a
            # zero-item menu. Recorded separately so the two never blur.
            status, rows = "no_menu_state", []
        else:
            rows = []
            for it in (i for i in raw if isinstance(i, dict)):
                name = str(it.get("name", "")).strip()
                rows.append({
                    "branch_id": bid,
                    "item_id": it.get("id"),
                    "item_name": name,
                    "item_key": make_item_key(name),
                    "category": str(it.get("originalSection", "")).strip(),
                    # web SSR price is already AED, not fils
                    "price_aed": round(float(it.get("price") or 0), 2),
                    "description": str(it.get("description", "")).strip(),
                })
            status = "ok" if rows else "empty_menu"

        async with self.lock:
            for r in rows:
                self.items_f.write(json.dumps(r, ensure_ascii=False) + "\n")
            self.items_written += len(rows)
            self.rest_f.write(json.dumps({
                "branch_id": bid, "name": rec.get("name"),
                "area_name": rec.get("area_name"), "url": rec["url"],
                "item_count": len(rows), "status": status,
                "scraped_at": now(),
            }, ensure_ascii=False) + "\n")
            self.done.add(bid)
            self.stats[status] += 1
            n = len(self.done)
            if n % CHECKPOINT_EVERY == 0:
                self.items_f.flush()
                self.rest_f.flush()
                self.save_checkpoint()
                el = time.time() - self.t0
                # rate must count only THIS run's work: self.done is seeded from
                # the checkpoint, so n/el reported 157 rest/s and "ETA 1 min"
                # after a resume - the resumed rows were credited to elapsed
                # time that never happened.
                did = n - self.start_done
                rate = did / el if el else 0
                left = max(self.total - did, 0)
                logger.info(
                    f"  {n:,} done ({did:,} this run) | {self.items_written:,} items | "
                    f"{rate:.2f} rest/s | ETA {left/max(rate,.01)/60:.0f} min | "
                    f"failed {self.stats['failed']} | breaker trips {self.breaker_trips}")

    async def run(self):
        self.load_state()
        recs = [json.loads(l) for l in open(SRC, encoding="utf-8") if l.strip()]
        logger.info(f"source: {SRC.name} | {len(recs):,} restaurants")

        if self.args.retry_failed:
            if not FAILED.exists():
                logger.error("no failed file")
                return 1
            ids = {json.loads(l)["branch_id"]
                   for l in open(FAILED, encoding="utf-8") if l.strip()}
            recs = [r for r in recs if r["branch_id"] in ids and
                    r["branch_id"] not in self.done]
            if not self.args.dry_run:
                FAILED.write_text("", encoding="utf-8")
            logger.info(f"retrying {len(recs):,} failed restaurants")
        else:
            recs = [r for r in recs if r["branch_id"] not in self.done]
        if self.args.limit:
            recs = recs[: self.args.limit]
            logger.warning(f"SMOKE TEST - {len(recs)} restaurants only")

        self.start_done = len(self.done)
        self.total = len(recs)
        if not self.total:
            logger.success("nothing left to do")
            return 0
        logger.info(f"to scrape: {self.total:,} | concurrency {self.args.concurrency} "
                    f"| rate {self.args.rate}/s")
        if self.args.dry_run:
            logger.warning(f"--dry-run: no requests "
                           f"(~{self.total/max(self.args.rate,.01)/60:.0f} min)")
            return 0

        self.items_f = open(ITEMS_OUT, "a", encoding="utf-8")
        self.rest_f = open(REST_OUT, "a", encoding="utf-8")
        sem = asyncio.Semaphore(self.args.concurrency)
        self.t0 = time.time()
        try:
            async with AsyncSession(impersonate="chrome124") as sess:
                await asyncio.gather(*(self.do_one(sess, r, sem) for r in recs))
        finally:
            self.items_f.flush(); self.items_f.close()
            self.rest_f.flush(); self.rest_f.close()
            self.save_checkpoint()

        el = time.time() - self.t0
        logger.success(f"DONE in {el/60:.1f} min | {len(self.done):,} restaurants | "
                       f"{self.items_written:,} menu items")
        for k in ("ok", "empty_menu", "no_menu_state", "failed"):
            logger.info(f"    {k:16} {self.stats[k]:,}")
        if self.stats["failed"]:
            pct = self.stats["failed"] / self.total * 100
            (logger.error if pct > 20 else logger.warning)(
                f"failure rate {pct:.0f}% - retry: python scrape_menus.py --retry-failed")
            for r, n in self.fail_reasons.most_common(5):
                logger.info(f"      {n:>5}  {r}")
        return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, help="smoke test on first N restaurants")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--rate", type=float, default=4.0, help="requests/sec overall")
    p.add_argument("--attempts", type=int, default=4)
    p.add_argument("--backoff", type=float, default=3.0)
    p.add_argument("--cooldown", type=float, default=120)
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--source", help="input jsonl of restaurants (branch_id + url)")
    p.add_argument("--outdir", help="write all outputs here instead of ./data")
    a = p.parse_args()
    set_paths(a.source, a.outdir)

    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
    logger.add(LOGS / "scrape.log", level="DEBUG", rotation="20 MB", encoding="utf-8")

    if not U or not P:
        logger.error("Oxylabs credentials missing in talabat/.env")
        return 1
    if not SRC.exists():
        logger.error(f"input missing: {SRC}")
        return 1
    return asyncio.run(Runner(a).run())


if __name__ == "__main__":
    sys.exit(main())
