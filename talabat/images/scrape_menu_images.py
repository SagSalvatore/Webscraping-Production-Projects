"""
Menu Image + Logo Scraper — Talabat UAE
=========================================
Scrapes the restaurant logo and every menu item's image for all 15,198
unique restaurants in talabat/Restaurant Identifier/data/restaurant_urls_for_scraping.jsonl.

Extraction technique (validated by hand against a live page before writing
this — see conversation): Talabat's restaurant pages are a Next.js app, and
the ENTIRE page state — including the full menu — is embedded as plain JSON
in a single <script id="__NEXT_DATA__" type="application/json"> tag. No
JS-eval or fragile HTML scraping needed, just:
    props.pageProps.initialMenuState.restaurant.logo        -> logo (small)
    props.pageProps.initialMenuState.menuData.items[]       -> each item has
        both "image" (resized, e.g. ?width=172&height=172) and
        "originalImage" (same file, no resize query params — the true
        original upload resolution).

"Regular size, not small" requirement: deliveryhero's image CDN serves a
downscaled thumbnail when a ?width=/&height= query string is present, and
the full original when it's stripped — confirmed empirically (logo:
180x180 -> 200x200; a sample menu item: 172x172/23.8KB -> 400x300/60.8KB).
menu items already expose the stripped version directly as "originalImage";
the restaurant logo does not have a parallel field, so the same stripping
is applied manually via strip_query_string().

Architecture mirrors talabat/Restaurant Identifier/restaurant_identifier.py
(the earlier, already-completed classification phase of this same project):
  - curl_cffi (impersonate=chrome124) + Oxylabs residential proxy, one
    session per worker, rotated every SESSION_REFRESH_INTERVAL requests
  - ThreadPoolExecutor, append-only JSONL (crash-safe), source_id-based
    checkpoint (resume across restarts)
  - Retry with backoff on 429 and on a missing __NEXT_DATA__ block (the
    latter can mean a transient bot-challenge page rather than a real 404)

Run:
  python "talabat/images/scrape_menu_images.py"                 # full run
  python "talabat/images/scrape_menu_images.py" --test 3        # dry run on first 3 URLs only
"""

import argparse
import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

# ── paths ─────────────────────────────────────────────────────────────────────
TALABAT_ROOT = Path(__file__).resolve().parent.parent  # talabat/
ENV_PATH = TALABAT_ROOT / ".env"
INPUT_JSONL = TALABAT_ROOT / "Restaurant Identifier" / "data" / "restaurant_urls_for_scraping.jsonl"
OUT_DIR = Path(__file__).resolve().parent / "data"
OUT_DIR.mkdir(exist_ok=True)

# set at runtime in main() depending on --test, so a dry run never touches
# the real checkpoint / output files used by the full production run
CONFIRMED_JSONL = OUT_DIR / "images_confirmed.jsonl"
FAILED_JSONL = OUT_DIR / "images_failed.jsonl"
CHECKPOINT = OUT_DIR / "checkpoint.json"

# ── tuning ────────────────────────────────────────────────────────────────────
CONCURRENT_WORKERS = 10
MIN_DELAY = 1.0
MAX_DELAY = 2.5
RETRY_ATTEMPTS = 3
SESSION_REFRESH_INTERVAL = 30
SAVE_INTERVAL = 25

# Set by --logo-only. August needs logos alone, and carrying every menu item's
# image URL is what made July's images_confirmed.jsonl 223 MB; logo-only keeps
# it around 2 MB. The extraction is unchanged - only what gets WRITTEN differs,
# so the menu-item path stays available for a future run.
LOGO_ONLY = False

# ── credentials ───────────────────────────────────────────────────────────────
load_dotenv(dotenv_path=ENV_PATH)
USERNAME = os.getenv("OXYLABS_USERNAME", "")
PASSWORD = os.getenv("OXYLABS_PASSWORD", "")
COUNTRY = os.getenv("OXYLABS_COUNTRY", "")

# ── thread-safe state ─────────────────────────────────────────────────────────
lock = threading.Lock()
session_counts = {"confirmed": 0, "failed": 0}
processed_ids = set()

BROWSER_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


# ── checkpoint ────────────────────────────────────────────────────────────────
def load_checkpoint():
    if CHECKPOINT.exists():
        with open(CHECKPOINT, "r", encoding="utf-8") as f:
            return set(json.load(f).get("processed_source_ids", []))
    return set()


def save_checkpoint(ids):
    tmp = str(CHECKPOINT) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({
            "processed_source_ids": list(ids),
            "count": len(ids),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }, f)
    os.replace(tmp, CHECKPOINT)


# ── file I/O ──────────────────────────────────────────────────────────────────
def append_jsonl(path, record):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── proxy / session ───────────────────────────────────────────────────────────
def create_session():
    if not USERNAME or not PASSWORD:
        raise RuntimeError("Oxylabs credentials missing. Set OXYLABS_USERNAME / OXYLABS_PASSWORD in talabat/.env")
    session_id = random.randint(10000, 99999)
    proxy_user = f"customer-{USERNAME}-cc-{COUNTRY}-sessid-{session_id}"
    proxy_url = f"http://{proxy_user}:{PASSWORD}@pr.oxylabs.io:7777"

    session = cffi_requests.Session(impersonate="chrome124")
    session.proxies = {"http": proxy_url, "https": proxy_url}
    session.headers.update({
        "User-Agent": random.choice(BROWSER_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    })
    return session, session_id


# ── extraction ────────────────────────────────────────────────────────────────
def strip_query_string(url):
    """deliveryhero's image CDN serves a downscaled thumbnail when a
    ?width=/&height= query string is present, and the true original when
    it's stripped — confirmed empirically against a live logo URL
    (180x180 with ?width=180 -> 200x200 stripped)."""
    if not url:
        return url
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def extract_images(html):
    """Returns (logo_url, menu_items) from the page's __NEXT_DATA__ JSON, or
    None if that script tag isn't present (page didn't render normally —
    could be a transient bot-challenge page rather than a real 404)."""
    m = NEXT_DATA_PATTERN.search(html)
    if not m:
        return None

    data = json.loads(m.group(1))
    menu_state = data.get("props", {}).get("pageProps", {}).get("initialMenuState", {})
    restaurant = menu_state.get("restaurant", {})
    items = menu_state.get("menuData", {}).get("items", [])

    logo = strip_query_string(restaurant.get("logo"))

    menu_items = []
    for it in items:
        # originalImage is already the no-resize-params version; only fall
        # back to manually stripping "image" for the rare item that has one
        # but not the other (not observed in testing, but cheap to guard)
        image = it.get("originalImage") or strip_query_string(it.get("image"))
        if not image:
            continue  # isWithImage=False items with no real image at all
        menu_items.append({
            "item_id": it.get("id"),
            "name": it.get("name"),
            "section": it.get("originalSection"),
            "image": image,
        })

    return logo, menu_items


# ── per-URL processing ────────────────────────────────────────────────────────
def process_row(session, row):
    url = row.get("map_url", "")
    source_id = row.get("source_id")
    now = datetime.now(timezone.utc).isoformat()

    try:
        resp = session.get(url, timeout=25)

        if resp.status_code == 429:
            return "RATE_LIMITED"

        if resp.status_code != 200:
            return {
                "status": "http_error",
                "source_id": source_id,
                "url": url,
                "http_status": resp.status_code,
                "fetched_at": now,
            }

        extracted = extract_images(resp.text)
        if extracted is None:
            return "NO_NEXT_DATA"  # retried like rate-limiting, see worker()

        logo, menu_items = extracted
        images = {"logo": logo}
        if not LOGO_ONLY:
            images["menu_items"] = menu_items
        return {
            "status": "confirmed",
            "source_id": source_id,
            "chain_id": row.get("chain_id"),
            "restaurant_name": row.get("restaurant_name"),
            "images": images,
            "menu_item_count": len(menu_items),
            "fetched_at": now,
        }

    except Exception as exc:
        return {
            "status": "exception",
            "source_id": source_id,
            "url": url,
            "error": str(exc),
            "fetched_at": now,
        }


# ── worker ────────────────────────────────────────────────────────────────────
def worker(worker_id, rows):
    session = None
    local_processed = 0

    try:
        for i, row in enumerate(rows):
            if session is None or local_processed % SESSION_REFRESH_INTERVAL == 0:
                if session:
                    session.close()
                session, sid = create_session()
                verb = "created" if local_processed == 0 else "rotated"
                print(f"[W{worker_id}] Session {verb} (id={sid})")

            source_id = row.get("source_id")
            print(f"[W{worker_id}] ({i+1}/{len(rows)}) source_id={source_id}")

            result = None
            for attempt in range(RETRY_ATTEMPTS):
                result = process_row(session, row)

                if result == "RATE_LIMITED":
                    wait = (2 ** attempt) * 20
                    print(f"[W{worker_id}] Rate limited — waiting {wait}s, then rotating session")
                    time.sleep(wait)
                    if session:
                        session.close()
                    session, sid = create_session()
                    result = None
                    continue

                if result == "NO_NEXT_DATA" and attempt < RETRY_ATTEMPTS - 1:
                    print(f"[W{worker_id}] No __NEXT_DATA__ (possible bot-challenge page) — retrying with fresh session")
                    time.sleep(5)
                    if session:
                        session.close()
                    session, sid = create_session()
                    result = None
                    continue

                if isinstance(result, dict) and result.get("status") == "exception" and attempt < RETRY_ATTEMPTS - 1:
                    print(f"[W{worker_id}] Retry {attempt+2}/{RETRY_ATTEMPTS}: {result.get('error', '')[:80]}")
                    time.sleep(5)
                    continue

                break

            with lock:
                if isinstance(result, dict) and result.get("status") == "confirmed":
                    append_jsonl(CONFIRMED_JSONL, result)
                    session_counts["confirmed"] += 1
                else:
                    rec = result if isinstance(result, dict) else {"source_id": source_id, "url": row.get("map_url"), "status": str(result)}
                    append_jsonl(FAILED_JSONL, rec)
                    session_counts["failed"] += 1

                processed_ids.add(source_id)
                total = sum(session_counts.values())

                if total % SAVE_INTERVAL == 0:
                    save_checkpoint(processed_ids)
                    c, f = session_counts["confirmed"], session_counts["failed"]
                    print(f"[checkpoint] total={total} | confirmed={c} | failed={f}")

            local_processed += 1
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    except Exception as exc:
        print(f"[W{worker_id}] Critical error: {exc}")

    finally:
        if session:
            session.close()
        print(f"[W{worker_id}] Done. Local processed={local_processed}")


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    global CONFIRMED_JSONL, FAILED_JSONL, CHECKPOINT

    parser = argparse.ArgumentParser()
    parser.add_argument("--retry-failed", action="store_true",
                        help="re-attempt only the source_ids in the failed "
                             "list (they are checkpointed as done, so a plain "
                             "resume skips them)")
    parser.add_argument("--logo-only", action="store_true",
                        help="write only the logo URL, not every menu-item image")
    parser.add_argument("--input", help="override the input JSONL")
    parser.add_argument("--out-prefix", help="prefix for output files, so a "
                        "cohort cannot overwrite another's checkpoint")
    parser.add_argument("--test", type=int, default=0, help="Only process the first N rows, into separate test_* output files (no checkpoint reuse) — for validation before a full run")
    args = parser.parse_args()

    global LOGO_ONLY, INPUT_JSONL
    LOGO_ONLY = bool(args.logo_only)
    if args.input:
        INPUT_JSONL = Path(args.input)

    # A cohort must never share a checkpoint with another - resuming across
    # cohorts would mark August's restaurants done because July's ran.
    if args.out_prefix:
        pre = args.out_prefix
        CONFIRMED_JSONL = OUT_DIR / f"{pre}_images_confirmed.jsonl"
        FAILED_JSONL = OUT_DIR / f"{pre}_images_failed.jsonl"
        CHECKPOINT = OUT_DIR / f"{pre}_checkpoint.json"

    if args.test:
        pre = f"{args.out_prefix}_test" if args.out_prefix else "test"
        CONFIRMED_JSONL = OUT_DIR / f"{pre}_images_confirmed.jsonl"
        FAILED_JSONL = OUT_DIR / f"{pre}_images_failed.jsonl"
        CHECKPOINT = OUT_DIR / f"{pre}_checkpoint.json"
        for p in (CONFIRMED_JSONL, FAILED_JSONL, CHECKPOINT):
            p.unlink(missing_ok=True)  # fresh slate every test run

    print("=" * 65)
    print("MENU IMAGE + LOGO SCRAPER — Talabat UAE")
    if args.test:
        print(f"*** TEST MODE: first {args.test} rows only ***")
    print(f"Workers : {CONCURRENT_WORKERS}")
    print(f"Delay   : {MIN_DELAY}-{MAX_DELAY}s per worker")
    print(f"Input   : {INPUT_JSONL}")
    print(f"Output  : {OUT_DIR}")
    print("=" * 65)

    if not USERNAME or not PASSWORD:
        print("ERROR: Oxylabs credentials not set in talabat/.env")
        return

    rows = []
    with open(INPUT_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    print(f"Loaded {len(rows):,} rows")

    if args.test:
        rows = rows[: args.test]
        print(f"TEST MODE -> processing {len(rows)} rows")

    already_done = load_checkpoint()

    # --retry-failed: a FAILED row is still written to the checkpoint, so a
    # plain resume reports "Nothing to do" and the failures are never
    # re-attempted. Measured on the August run: 7 failures, all transient
    # (SSL read, CONNECT tunnel, timeout, one 502) - exactly the kind that
    # succeed on a second try. This drops them out of the checkpoint so the
    # normal path picks them up.
    if getattr(args, "retry_failed", False):
        failed_ids = set()
        if FAILED_JSONL.exists():
            with open(FAILED_JSONL, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        sid = json.loads(line).get("source_id")
                        if sid:
                            failed_ids.add(sid)
        if not failed_ids:
            print(f"{FAILED_JSONL.name} is empty or missing - nothing to retry.")
            return
        print(f"RETRY MODE: re-attempting {len(failed_ids):,} previously failed")
        already_done = already_done - failed_ids
        # start the failed list clean; whatever fails again is re-appended
        FAILED_JSONL.unlink(missing_ok=True)

    processed_ids.update(already_done)
    if already_done:
        print(f"Resuming: {len(already_done):,} source_ids already processed")

    remaining = [r for r in rows if r.get("source_id") not in already_done]
    print(f"Remaining: {len(remaining):,}")

    if not remaining:
        print("Nothing to do.")
        return

    workers = min(CONCURRENT_WORKERS, len(remaining))
    batch_size = max(1, -(-len(remaining) // workers))
    batches = [remaining[i : i + batch_size] for i in range(0, len(remaining), batch_size)]
    while len(batches) > workers:
        batches[-2].extend(batches.pop())
    print(f"Batch sizes: {[len(b) for b in batches]}")
    print()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(worker, i + 1, batch): i for i, batch in enumerate(batches)}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:
                print(f"Worker {futures[fut]+1} raised: {exc}")

    save_checkpoint(processed_ids)

    c, f = session_counts["confirmed"], session_counts["failed"]
    print()
    print("=" * 65)
    print("FINISHED")
    print(f"  Confirmed (logo + menu images) : {c:,}")
    print(f"  Failed                         : {f:,}")
    print(f"  Total this session             : {c + f:,}")
    print("=" * 65)


if __name__ == "__main__":
    main()
