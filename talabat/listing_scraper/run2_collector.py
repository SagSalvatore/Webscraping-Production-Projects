"""Second-runner — collect NEW Talabat UAE branches not already in our universe.

Design decisions, all evidence-based from the smoke tests:

  * VALUE ORDER, not CSV order. Areas are crawled by (page-1 new-rate desc,
    page-count asc). The first 100 areas are ~3,700 pages and mostly 100% new
    (peripheral emirates: Hatta, Al Aqah, Khatt, UAQ Airport), while saturated
    central-Dubai areas cost 400+ pages for 8-20% new. Stopping early therefore
    still captures the best data.

  * FULL DEPTH per area. A depth probe showed new-yield holds at ~26% even at
    page 450, so truncating an area to its first N pages would lose real data.

  * DEDUP seeded with all 17,211 existing branch_ids, so the output contains
    ONLY branches we do not already have.

  * Concurrency 40. Benchmarked: 10->2.9 req/s, 25->4.5, 40->5.0, 60->4.6.
    Talabat's response time is the ceiling, not the proxy - direct connections
    measured no faster (3.5-4.1 req/s), so the proxy stays for IP rotation.

  * Every failed page is written to run2_failed_pages.jsonl so a retry pass can
    target exactly those instead of re-running the whole crawl.

    python run2_collector.py --tier 100      # top 100 areas  (~12 min)
    python run2_collector.py --tier 300      # top 300 areas  (~1.7 h)
    python run2_collector.py                 # all mapped areas (~5 h)
    python run2_collector.py --retry-failed  # only pages that previously failed
"""
import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from loguru import logger

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")
from url_collector.parser import parse_listing_page          # noqa: E402

HERE = Path(__file__).resolve().parent
RES = HERE / "smoke_results"
OUT_DIR = _ROOT / "data" / "urls"
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = HERE / "logs"
LOG_DIR.mkdir(exist_ok=True)

AREA_MAP = RES / "area_map.json"
PRIORITY = RES / "area_priority.json"
# industrial zones are warehouse/labour land - deliberately excluded
LIVELY = {"residential", "commercial", "tourist"}
# Defaults reproduce the August run exactly. --seed / --out override them so a
# later cycle cannot (a) re-collect what earlier cycles already hold, or
# (b) write into a previous cycle's output file. Both were real hazards: the
# seed below is run-1 ONLY, so September seeded from it would return all 7,129
# August restaurants as new, and OUT_JSONL is August's own file.
EXISTING = OUT_DIR / "talabat_restaurant_urls.jsonl"
OUT_JSONL = OUT_DIR / "talabat_restaurant_urls_run2.jsonl"
CHECKPOINT = OUT_DIR / "run2_checkpoint.json"
FAILED = OUT_DIR / "run2_failed_pages.jsonl"
SUMMARY = OUT_DIR / "run2_summary.json"


def apply_path_overrides(args):
    """Repoint the module-level paths for a named cycle.

    Kept as an explicit function rather than plumbing paths through the class,
    so the August behaviour is untouched when no flags are passed.
    """
    global EXISTING, OUT_JSONL, CHECKPOINT, FAILED, SUMMARY
    if args.seed:
        EXISTING = Path(args.seed)
    if args.out:
        OUT_JSONL = Path(args.out)
        stem = OUT_JSONL.stem
        CHECKPOINT = OUT_JSONL.with_name(f"{stem}_checkpoint.json")
        FAILED = OUT_JSONL.with_name(f"{stem}_failed_pages.jsonl")
        SUMMARY = OUT_JSONL.with_name(f"{stem}_summary.json")

U = os.getenv("OXYLABS_USERNAME", "")
P = os.getenv("OXYLABS_PASSWORD", "")
C = os.getenv("OXYLABS_COUNTRY", "ae")

CHECKPOINT_EVERY = 200          # pages between checkpoint flushes


def px(sid):
    # http:// scheme is required: the proxy tunnel is HTTP CONNECT. Using
    # https:// makes curl TLS-handshake the proxy itself and fail obscurely.
    return f"http://customer-{U}-cc-{C}-sessid-{sid}:{P}@pr.oxylabs.io:7777"


def now():
    return datetime.now(timezone.utc).isoformat()


class Run2:
    def __init__(self, args):
        self.args = args
        self.new_ids: set[int] = set()
        self.existing: set[int] = set()
        self.done_pages: set[str] = set()
        self.failed: list[dict] = []
        self.fail_reasons: Counter = Counter()
        # Circuit breaker. Two runs died the same way: ~300 pages clean, then
        # a burst of 429s, and the per-page retries (865 pages x 5 attempts)
        # fired thousands of extra requests into an endpoint that was already
        # throttling - turning a brief slowdown into 73% total failure and
        # spending proxy credit on requests that could not succeed.
        # When 429s arrive, every worker waits; retrying harder cannot work
        # because the limit is global, not per-connection.
        self.breaker_until = 0.0
        self.breaker_trips = 0
        # global pacing: spaces out request STARTS across all workers, so the
        # ceiling is respected by construction rather than discovered by
        # tripping it.
        self.rate_lock = asyncio.Lock()
        self.next_slot = 0.0
        self.rows_written = 0
        self.pages_done = 0
        self.lock = asyncio.Lock()
        self.out = None

    # -------------------------------------------------- state
    def load_state(self):
        # Two accepted shapes. JSONL is the original: one crawl row per line.
        # JSON is september/build_universe.py's output, which unions every
        # branch_id ever evaluated - not just one earlier crawl's rows.
        if EXISTING.suffix == ".json":
            d = json.loads(EXISTING.read_text(encoding="utf-8"))
            ids = d["branch_ids"] if isinstance(d, dict) else d
            self.existing.update(int(b) for b in ids)
            if isinstance(d, dict) and d.get("retry_ids"):
                logger.info(f"seed excludes {len(d['retry_ids']):,} previously "
                            f"failed fetches - they will be offered again")
        else:
            with open(EXISTING, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self.existing.add(json.loads(line)["branch_id"])
        logger.info(f"existing universe: {len(self.existing):,} branch_ids "
                    f"(from {EXISTING.name})")

        if OUT_JSONL.exists():
            with open(OUT_JSONL, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self.new_ids.add(json.loads(line)["branch_id"])
            logger.info(f"run2 already has: {len(self.new_ids):,} new branches")

        if CHECKPOINT.exists():
            try:
                d = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
                self.done_pages = set(d.get("done_pages", []))
                logger.info(f"resuming: {len(self.done_pages):,} pages already done")
            except Exception as e:
                logger.warning(f"checkpoint unreadable ({e}) - starting fresh")

    def save_checkpoint(self):
        tmp = CHECKPOINT.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "saved_at": now(),
            "pages_done": len(self.done_pages),
            "new_branches": len(self.new_ids),
            "done_pages": sorted(self.done_pages),
        }), encoding="utf-8")
        tmp.replace(CHECKPOINT)

    # -------------------------------------------------- fetch
    async def fetch(self, sess, url, sem):
        """Returns (html, reason). reason is None on success, else why it died.

        The reason is recorded because a silent `except Exception` once turned
        1,183 failed pages into an unexplainable black box - we could not tell
        throttling from proxy exhaustion without re-testing URLs by hand.
        """
        sid = uuid.uuid4().hex[:8]
        reason = "unknown"
        for attempt in range(self.args.attempts):
            await self.wait_for_breaker()
            await self.pace()
            async with sem:
                try:
                    r = await sess.get(url, timeout=75,
                                       proxies={"http": px(sid), "https": px(sid)},
                                       headers={"Accept-Language": "en-US,en;q=0.9",
                                                "Referer": url.split("?")[0]})
                    if r.status_code == 200:
                        return r.text, None
                    reason = f"HTTP {r.status_code}"
                    if r.status_code in (403, 429, 503):
                        sid = uuid.uuid4().hex[:8]      # rotate on block
                        if r.status_code == 429:
                            await self.trip_breaker()
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {str(exc)[:120]}"
                    sid = uuid.uuid4().hex[:8]
            # exponential, not linear: ~15s total was far too little once
            # Talabat starts refusing, and every page failed together.
            await asyncio.sleep(min(self.args.backoff * (2 ** attempt), 60))
        return None, reason

    async def trip_breaker(self):
        """Pause every worker. Extends, never shortens, an active pause."""
        async with self.lock:
            now_t = time.time()
            if now_t < self.breaker_until:
                return                          # already paused; don't stack
            self.breaker_trips += 1
            self.breaker_until = now_t + self.args.cooldown
            logger.warning(f"429 - circuit breaker OPEN for "
                           f"{self.args.cooldown}s (trip #{self.breaker_trips})")

    async def wait_for_breaker(self):
        while True:
            delay = self.breaker_until - time.time()
            if delay <= 0:
                return
            await asyncio.sleep(min(delay, 5))

    async def pace(self):
        """Hand out evenly-spaced start slots when --rate is set."""
        if not self.args.rate:
            return
        gap = 1.0 / self.args.rate
        async with self.rate_lock:
            now_t = time.time()
            slot = max(now_t, self.next_slot)
            self.next_slot = slot + gap
        wait = slot - time.time()
        if wait > 0:
            await asyncio.sleep(wait)

    async def do_page(self, sess, area, page, sem):
        key = f"{area['id']}::{page}"
        if key in self.done_pages:
            return
        url = f"{area['url']}?page={page}"
        html, reason = await self.fetch(sess, url, sem)

        if html is None:
            async with self.lock:
                rec = {"area_id": area["id"], "area": area["name"],
                       "page": page, "url": url, "at": now(), "reason": reason}
                self.failed.append(rec)
                self.fail_reasons[reason] += 1
                with open(FAILED, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return

        res = parse_listing_page(html, page)
        rows = []
        for v in res.vendors:
            if not v.branch_id:
                continue
            # only keep genuinely NEW branches
            if v.branch_id in self.existing or v.branch_id in self.new_ids:
                continue
            rows.append({
                "url": f"{v.url.split('?')[0]}?aid={area['id']}",
                "branch_id": v.branch_id,
                "restaurant_id": v.restaurant_id,
                "branch_slug": v.branch_slug,
                "name": v.name,
                "lat": v.lat, "lon": v.lon,
                "area_name": area["name"], "area_id": area["id"],
                "page_found": page, "scraped_at": now(),
            })

        async with self.lock:
            for r in rows:
                if r["branch_id"] in self.new_ids:
                    continue                       # race guard
                self.new_ids.add(r["branch_id"])
                self.out.write(json.dumps(r, ensure_ascii=False) + "\n")
                self.rows_written += 1
            self.done_pages.add(key)
            self.pages_done += 1
            if self.pages_done % CHECKPOINT_EVERY == 0:
                self.out.flush()
                self.save_checkpoint()
                el = time.time() - self.t0
                rate = self.pages_done / el
                left = (self.total_pages - self.pages_done) / rate if rate else 0
                logger.info(
                    f"  {self.pages_done:,}/{self.total_pages:,} pages | "
                    f"NEW {len(self.new_ids):,} | {rate:.1f} pg/s | "
                    f"ETA {left/60:.0f} min | failed {len(self.failed)}"
                )

    # -------------------------------------------------- run
    async def run(self):
        self.load_state()

        if self.args.retry_failed:
            if not FAILED.exists():
                logger.error("no failed-page file to retry")
                return 1
            seen = set()
            tasks = []
            for line in FAILED.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                k = f"{r['area_id']}::{r['page']}"
                if k in seen or k in self.done_pages:
                    continue
                seen.add(k)
                tasks.append(({"id": r["area_id"], "name": r["area"],
                               "url": r["url"].split("?")[0]}, r["page"]))
            if not self.args.dry_run:
                # must NOT clear under --dry-run: a "show me the plan" call
                # would silently destroy the retry queue it was inspecting.
                FAILED.write_text("", encoding="utf-8")  # re-failures re-append
            logger.info(f"retrying {len(tasks):,} previously-failed pages")
        elif self.args.from_probe:
            # MEASURED ranking (prioritize_areas.py --probe): page 1 of every
            # remaining area fetched against the live known-branch_id set.
            # This replaces --priority-top, whose estimate scored -0.099
            # Spearman against actual yield. No character filter is applied:
            # measured median new/15 was 1 for BOTH 'industrial' and 'lively'
            # areas, so the label does not discriminate.
            probed = json.loads((RES / "area_probe_ranked.json").read_text(encoding="utf-8"))
            probed = [a for a in probed if a.get("probe_new")]      # skip dead areas
            areas = probed[self.args.priority_skip:
                           self.args.priority_skip + self.args.from_probe]
            amap = {a["id"]: a for a in json.loads(AREA_MAP.read_text(encoding="utf-8"))}
            tasks = [(a, p) for a in areas
                     for p in range(1, (amap.get(a["id"], {}).get("total_pages") or 0) + 1)]
            logger.info(f"MEASURED run | {len(probed)} areas with >0 new on page 1 | "
                        f"taking {len(areas)}")
            for i, a in enumerate(areas, 1):
                logger.info(f"   {i:>2}. {a['name'][:28]:30} aid={a['id']:<6} "
                            f"new {a['probe_new']}/15 ({a['probe_new_rate']*100:.0f}%) "
                            f"pages={a['pages_left']:>4}")
        elif self.args.priority_top:
            # Ranked run: take the top N from prioritize_areas.py instead of
            # raw new_rate order. Only LIVELY areas (residential/commercial/
            # tourist) - industrial zones are labour/warehouse land and were
            # de-prioritised deliberately. Ordering is by `efficiency`
            # (vendors x new_rate / pages_left), i.e. new listings per page
            # spent, which is what the proxy budget actually buys.
            pri = json.loads(PRIORITY.read_text(encoding="utf-8"))
            lively = [a for a in pri if a.get("character") in LIVELY]
            lively.sort(key=lambda a: -a["efficiency"])
            # --priority-skip advances past areas already crawled, so "the next
            # 50" means the next 50 UNSEEN areas, not the top 50 again. The
            # checkpoint would skip finished pages anyway, but being explicit
            # keeps the queued-page count honest.
            areas = lively[self.args.priority_skip:
                           self.args.priority_skip + self.args.priority_top]
            # pages_left is what REMAINS; the page range must still start at 1
            # because done pages are filtered out by the checkpoint below.
            amap = {a["id"]: a for a in json.loads(AREA_MAP.read_text(encoding="utf-8"))}
            tasks = [(a, p) for a in areas
                     for p in range(1, (amap.get(a["id"], {}).get("total_pages") or 0) + 1)]
            logger.info(f"PRIORITY run | lively areas available: {len(lively)} | "
                        f"taking top {len(areas)}")
            for i, a in enumerate(areas, 1):
                logger.info(f"   {i:>2}. {a['name'][:28]:30} aid={a['id']:<6} "
                            f"{a['character']:12} vendors={a['total_vendors']:>5} "
                            f"pages_left={a['pages_left']:>4} eff={a['efficiency']}")
        else:
            areas = json.loads(AREA_MAP.read_text(encoding="utf-8"))
            areas = [a for a in areas if a.get("total_pages")]
            # value order: highest page-1 new-rate first, cheapest first as tiebreak
            areas.sort(key=lambda a: (-(a.get("new_rate_p1") or 0), a["total_pages"]))
            if self.args.tier:
                areas = areas[: self.args.tier]
            tasks = [(a, p) for a in areas for p in range(1, a["total_pages"] + 1)]
            logger.info(f"areas: {len(areas):,} | pages queued: {len(tasks):,}")

        tasks = [(a, p) for a, p in tasks if f"{a['id']}::{p}" not in self.done_pages]
        self.total_pages = len(tasks)
        if not self.total_pages:
            logger.success("nothing left to do")
            return 0
        logger.info(f"pages to fetch: {self.total_pages:,} | concurrency {self.args.concurrency}")

        if self.args.dry_run:
            logger.warning(f"--dry-run: no requests. Would fetch {self.total_pages:,} "
                           f"pages (~{self.total_pages/0.6/60:.0f} min at 0.6 pg/s)")
            return 0

        self.out = open(OUT_JSONL, "a", encoding="utf-8")
        sem = asyncio.Semaphore(self.args.concurrency)
        self.t0 = time.time()
        try:
            async with AsyncSession(impersonate="chrome124") as sess:
                await asyncio.gather(*(self.do_page(sess, a, p, sem) for a, p in tasks))
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.warning("interrupted - flushing state")
            raise
        finally:
            self.out.flush()
            self.out.close()
            self.save_checkpoint()

        el = time.time() - self.t0
        by_area = defaultdict(int)
        with open(OUT_JSONL, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    by_area[json.loads(line)["area_name"]] += 1
        SUMMARY.write_text(json.dumps({
            "completed_at": now(), "elapsed_sec": round(el),
            "pages_fetched": self.pages_done,
            "new_branches": len(self.new_ids),
            "failed_pages": len(self.failed),
            "per_area": dict(sorted(by_area.items(), key=lambda t: -t[1])),
        }, indent=2, ensure_ascii=False), encoding="utf-8")

        logger.success(f"DONE in {el/60:.1f} min | {self.pages_done:,} pages | "
                       f"{len(self.new_ids):,} NEW branches | {len(self.failed)} failed pages")
        if self.failed:
            pct = len(self.failed) / (self.pages_done + len(self.failed)) * 100
            lvl = logger.error if pct > 20 else logger.warning
            lvl(f"FAILURE RATE {pct:.0f}% - this run is INCOMPLETE")
            for reason, n in self.fail_reasons.most_common(6):
                logger.info(f"    {n:>5}  {reason}")
            logger.info(f"retry them with:  python run2_collector.py --retry-failed")
        return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tier", type=int, default=0, help="only top N areas by value")
    p.add_argument("--priority-top", type=int, default=0,
                   help="top N LIVELY areas from area_priority.json (by efficiency)")
    p.add_argument("--from-probe", type=int, default=0,
                   help="top N areas by MEASURED probe value (preferred)")
    p.add_argument("--priority-skip", type=int, default=0,
                   help="skip the first N lively areas (already crawled)")
    p.add_argument("--concurrency", type=int, default=40)
    p.add_argument("--attempts", type=int, default=4, help="tries per page")
    p.add_argument("--backoff", type=float, default=1.5,
                   help="base seconds; doubles each attempt, capped at 60")
    p.add_argument("--cooldown", type=float, default=90,
                   help="seconds ALL workers pause when a 429 is seen")
    p.add_argument("--rate", type=float, default=0,
                   help="max requests/sec overall (0 = unlimited)")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--seed",
                   help="dedup seed: a crawl .jsonl, or september/build_universe.py's "
                        ".json of every branch_id ever evaluated. Without this the "
                        "seed is run-1 ONLY, so a later cycle re-collects "
                        "everything found since.")
    p.add_argument("--out",
                   help="output .jsonl for THIS cycle. Checkpoint, failed-page "
                        "and summary files are derived from it, so a new cycle "
                        "never writes into a previous one's files.")
    p.add_argument("--dry-run", action="store_true",
                   help="show the area/page plan, make no requests")
    args = p.parse_args()
    apply_path_overrides(args)

    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
    logger.add(LOG_DIR / "run2.log", level="DEBUG", rotation="20 MB", encoding="utf-8")

    if not U or not P:
        logger.error("Oxylabs credentials missing in talabat/.env")
        return 1
    return asyncio.run(Run2(args).run())


if __name__ == "__main__":
    sys.exit(main())
