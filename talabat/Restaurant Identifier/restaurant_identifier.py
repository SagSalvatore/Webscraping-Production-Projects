"""
Restaurant Identifier — Talabat UAE
====================================
Reads all 17k+ scraped Talabat URLs, fetches each page, and extracts the
<script type="application/ld+json"> block to determine whether the URL is a
food restaurant (by @type=Restaurant + matching servesCuisine).

Upgrades over TALABAT_FAST_MENU.py:
  - curl_cffi (impersonate=chrome124) for TLS fingerprint bypass
  - 10 workers instead of 2
  - Shorter per-request delay (1.0-2.5s vs 3-6s)
  - Append-mode JSONL output (no data loss on crash)
  - Branch-ID-based checkpoint (resume across restarts)
  - Separate output files: confirmed / non_restaurant / failed
"""

import json
import re
import os
import time
import random
import csv
import threading
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from curl_cffi import requests as cffi_requests

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent          # talabat/
ENV_PATH = ROOT / ".env"
INPUT_JSONL = ROOT / "data" / "urls" / "talabat_restaurant_urls.jsonl"
CUISINE_CSV = ROOT / "talabat_restro_filteration.csv"
OUT_DIR = Path(__file__).resolve().parent / "data"
OUT_DIR.mkdir(exist_ok=True)

CONFIRMED_JSONL = OUT_DIR / "restaurants_confirmed.jsonl"
NON_REST_JSONL  = OUT_DIR / "non_restaurants.jsonl"
FAILED_JSONL    = OUT_DIR / "failed_urls.jsonl"
CHECKPOINT      = OUT_DIR / "checkpoint.json"
CONFIRMED_CSV   = OUT_DIR / "restaurants_confirmed.csv"

# ── tuning ────────────────────────────────────────────────────────────────────
CONCURRENT_WORKERS       = 10
MIN_DELAY                = 1.0    # seconds between requests per worker
MAX_DELAY                = 2.5
RETRY_ATTEMPTS           = 3
SESSION_REFRESH_INTERVAL = 30     # rotate IP every N requests per worker
SAVE_INTERVAL            = 25     # checkpoint every N total processed

# ── credentials ───────────────────────────────────────────────────────────────
load_dotenv(dotenv_path=ENV_PATH)
USERNAME = os.getenv("OXYLABS_USERNAME", "")
PASSWORD = os.getenv("OXYLABS_PASSWORD", "")
COUNTRY  = os.getenv("OXYLABS_COUNTRY", "ae")

# ── thread-safe state ─────────────────────────────────────────────────────────
lock           = threading.Lock()
session_counts = {"confirmed": 0, "non_rest": 0, "failed": 0}
processed_ids  = set()

BROWSER_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


# ── whitelist ─────────────────────────────────────────────────────────────────
def load_cuisine_whitelist() -> set:
    whitelist = set()
    with open(CUISINE_CSV, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i == 0:
                continue
            name = line.strip()
            if name:
                whitelist.add(name.lower())
    return whitelist

CUISINE_WHITELIST = load_cuisine_whitelist()
print(f"Whitelist loaded: {len(CUISINE_WHITELIST)} cuisines")


# ── checkpoint ────────────────────────────────────────────────────────────────
def load_checkpoint() -> set:
    if CHECKPOINT.exists():
        with open(CHECKPOINT, "r", encoding="utf-8") as f:
            data = json.load(f)
            ids = data.get("processed_branch_ids", [])
            return set(ids)
    return set()

def save_checkpoint(ids: set):
    tmp = str(CHECKPOINT) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({
            "processed_branch_ids": list(ids),
            "count": len(ids),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }, f)
    os.replace(tmp, CHECKPOINT)


# ── file I/O ──────────────────────────────────────────────────────────────────
def append_jsonl(path: Path, record: dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── proxy / session ───────────────────────────────────────────────────────────
def create_session():
    if not USERNAME or not PASSWORD:
        raise RuntimeError(
            "Oxylabs credentials missing. Set OXYLABS_USERNAME / OXYLABS_PASSWORD in talabat/.env"
        )
    session_id = random.randint(10000, 99999)
    proxy_user = f"customer-{USERNAME}-cc-{COUNTRY}-sessid-{session_id}"
    proxy_url  = f"http://{proxy_user}:{PASSWORD}@pr.oxylabs.io:7777"

    session = cffi_requests.Session(impersonate="chrome124")
    session.proxies = {"http": proxy_url, "https": proxy_url}
    session.headers.update({
        "User-Agent":       random.choice(BROWSER_UAS),
        "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language":  "en-US,en;q=0.9",
        "Accept-Encoding":  "gzip, deflate, br",
        "Cache-Control":    "no-cache",
        "Pragma":           "no-cache",
        "Sec-Fetch-Dest":   "document",
        "Sec-Fetch-Mode":   "navigate",
        "Sec-Fetch-Site":   "none",
    })
    return session, session_id


# ── JSON-LD extraction ────────────────────────────────────────────────────────
_LD_PATTERN = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

def extract_restaurant_ld(html: str) -> dict | None:
    """Return the first JSON-LD block whose @type is 'Restaurant', or None."""
    for raw in _LD_PATTERN.findall(html):
        try:
            data = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            continue

        # Handle @graph wrapper
        if isinstance(data, dict) and "@graph" in data:
            for node in data["@graph"]:
                if isinstance(node, dict) and node.get("@type") == "Restaurant":
                    return node

        if isinstance(data, dict) and data.get("@type") == "Restaurant":
            return data

    return None


# ── cuisine matching ──────────────────────────────────────────────────────────
def match_cuisines(serves_cuisine) -> list:
    if not serves_cuisine:
        return []
    if isinstance(serves_cuisine, list):
        parts = [c.strip() for c in serves_cuisine]
    else:
        parts = [c.strip() for c in str(serves_cuisine).split(",")]
    return [c for c in parts if c.lower() in CUISINE_WHITELIST]


# ── per-URL processing ────────────────────────────────────────────────────────
def process_row(session, row: dict, worker_id: int):
    url       = row.get("url", "")
    branch_id = row.get("branch_id")
    now       = datetime.now(timezone.utc).isoformat()

    try:
        resp = session.get(url, timeout=20)

        if resp.status_code == 429:
            return "RATE_LIMITED"

        if resp.status_code != 200:
            return {
                "status":      "http_error",
                "branch_id":   branch_id,
                "url":         url,
                "http_status": resp.status_code,
                "filtered_at": now,
            }

        ld = extract_restaurant_ld(resp.text)

        if ld is None:
            return {
                "status":      "no_ld_json",
                "branch_id":   branch_id,
                "name":        row.get("name", ""),
                "url":         url,
                "lat":         row.get("lat"),
                "lon":         row.get("lon"),
                "area_name":   row.get("area_name", ""),
                "reason":      "no_ld_json_on_page",
                "filtered_at": now,
            }

        ld_type        = ld.get("@type", "")
        ld_name        = ld.get("name", "")
        serves_cuisine = ld.get("servesCuisine", "")
        geo            = ld.get("geo", {}) or {}
        matched        = match_cuisines(serves_cuisine)

        serves_str = (
            ", ".join(serves_cuisine) if isinstance(serves_cuisine, list)
            else str(serves_cuisine)
        )

        if ld_type == "Restaurant" and matched:
            return {
                "status":           "confirmed",
                "branch_id":        branch_id,
                "restaurant_id":    row.get("restaurant_id"),
                "name":             row.get("name", "") or ld_name,
                "url":              url,
                "lat":              row.get("lat"),
                "lon":              row.get("lon"),
                "area_name":        row.get("area_name", ""),
                "area_id":          row.get("area_id"),
                "ld_type":          ld_type,
                "ld_name":          ld_name,
                "serves_cuisine":   serves_str,
                "matched_cuisines": matched,
                "ld_lat":           geo.get("latitude"),
                "ld_lon":           geo.get("longitude"),
                "filtered_at":      now,
            }

        reason = (
            "non_restaurant_type" if ld_type != "Restaurant"
            else "no_cuisine_match"
        )
        return {
            "status":           "non_restaurant",
            "branch_id":        branch_id,
            "name":             row.get("name", "") or ld_name,
            "url":              url,
            "lat":              row.get("lat"),
            "lon":              row.get("lon"),
            "area_name":        row.get("area_name", ""),
            "ld_type":          ld_type,
            "serves_cuisine":   serves_str,
            "matched_cuisines": matched,
            "reason":           reason,
            "filtered_at":      now,
        }

    except Exception as exc:
        return {
            "status":      "exception",
            "branch_id":   branch_id,
            "url":         url,
            "error":       str(exc),
            "filtered_at": now,
        }


# ── worker ────────────────────────────────────────────────────────────────────
def worker(worker_id: int, rows: list):
    session = None
    local_processed = 0

    try:
        for i, row in enumerate(rows):

            # Create / rotate session
            if session is None or local_processed % SESSION_REFRESH_INTERVAL == 0:
                if session:
                    session.close()
                session, sid = create_session()
                verb = "created" if local_processed == 0 else "rotated"
                print(f"[W{worker_id}] Session {verb} (id={sid})")

            branch_id = row.get("branch_id")
            print(f"[W{worker_id}] ({i+1}/{len(rows)}) branch={branch_id}")

            result = None
            for attempt in range(RETRY_ATTEMPTS):
                result = process_row(session, row, worker_id)

                if result == "RATE_LIMITED":
                    wait = (2 ** attempt) * 20
                    print(f"[W{worker_id}] Rate limited — waiting {wait}s, then rotating session")
                    time.sleep(wait)
                    if session:
                        session.close()
                    session, sid = create_session()
                    result = None
                    continue

                if result and result.get("status") == "exception" and attempt < RETRY_ATTEMPTS - 1:
                    print(f"[W{worker_id}] Retry {attempt+2}/{RETRY_ATTEMPTS}: {result.get('error', '')[:80]}")
                    time.sleep(5)
                    continue

                break  # success or final failure

            # --- store result
            with lock:
                status = result.get("status") if result else "null"

                if status == "confirmed":
                    append_jsonl(CONFIRMED_JSONL, result)
                    session_counts["confirmed"] += 1
                elif status in ("non_restaurant", "no_ld_json", "http_error"):
                    append_jsonl(NON_REST_JSONL, result)
                    session_counts["non_rest"] += 1
                else:
                    rec = result or {"branch_id": branch_id, "url": row.get("url"), "status": "null"}
                    append_jsonl(FAILED_JSONL, rec)
                    session_counts["failed"] += 1

                processed_ids.add(branch_id)
                total = sum(session_counts.values())

                if total % SAVE_INTERVAL == 0:
                    save_checkpoint(processed_ids)
                    c = session_counts["confirmed"]
                    n = session_counts["non_rest"]
                    f = session_counts["failed"]
                    print(f"[checkpoint] total={total} | confirmed={c} | non_rest={n} | failed={f}")

            local_processed += 1
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    except Exception as exc:
        print(f"[W{worker_id}] Critical error: {exc}")

    finally:
        if session:
            session.close()
        print(f"[W{worker_id}] Done. Local processed={local_processed}")


# ── CSV export ────────────────────────────────────────────────────────────────
CONFIRMED_FIELDS = [
    "branch_id", "restaurant_id", "name", "url", "lat", "lon",
    "area_name", "area_id", "ld_type", "ld_name", "serves_cuisine",
    "matched_cuisines", "ld_lat", "ld_lon", "filtered_at",
]

def generate_csv():
    rows = []
    if CONFIRMED_JSONL.exists():
        with open(CONFIRMED_JSONL, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))

    if not rows:
        print("No confirmed rows to export.")
        return

    with open(CONFIRMED_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CONFIRMED_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            copy = dict(row)
            if isinstance(copy.get("matched_cuisines"), list):
                copy["matched_cuisines"] = ", ".join(copy["matched_cuisines"])
            writer.writerow(copy)

    print(f"CSV exported: {CONFIRMED_CSV} ({len(rows)} rows)")


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("RESTAURANT IDENTIFIER — Talabat UAE")
    print(f"Workers : {CONCURRENT_WORKERS}")
    print(f"Delay   : {MIN_DELAY}–{MAX_DELAY}s per worker")
    print(f"Input   : {INPUT_JSONL}")
    print(f"Output  : {OUT_DIR}")
    print("=" * 65)

    if not USERNAME or not PASSWORD:
        print("ERROR: Oxylabs credentials not set in talabat/.env")
        return

    # Load all rows
    rows = []
    with open(INPUT_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    print(f"Loaded {len(rows)} rows")

    # Resume checkpoint
    already_done = load_checkpoint()
    processed_ids.update(already_done)
    if already_done:
        print(f"Resuming: {len(already_done)} branch_ids already processed")

    remaining = [r for r in rows if r.get("branch_id") not in already_done]
    print(f"Remaining: {len(remaining)}")

    if not remaining:
        print("All URLs processed. Exporting CSV...")
        generate_csv()
        return

    # Estimate runtime
    avg_delay = (MIN_DELAY + MAX_DELAY) / 2
    est_secs  = (len(remaining) * avg_delay) / CONCURRENT_WORKERS
    print(f"Est. time: ~{est_secs/60:.0f} min at {CONCURRENT_WORKERS} workers × {avg_delay}s avg delay")
    print()

    # Split into N worker batches
    batch_size = max(1, -(-len(remaining) // CONCURRENT_WORKERS))  # ceiling div
    batches = [remaining[i:i+batch_size] for i in range(0, len(remaining), batch_size)]
    # Merge any tiny tail batch into the last real batch
    while len(batches) > CONCURRENT_WORKERS:
        batches[-2].extend(batches.pop())
    print(f"Batch sizes: {[len(b) for b in batches]}")
    print()

    with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
        futures = {executor.submit(worker, i + 1, batch): i for i, batch in enumerate(batches)}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:
                print(f"Worker {futures[fut]+1} raised: {exc}")

    # Final checkpoint + CSV
    save_checkpoint(processed_ids)
    generate_csv()

    c = session_counts["confirmed"]
    n = session_counts["non_rest"]
    f = session_counts["failed"]
    total = c + n + f

    print()
    print("=" * 65)
    print("FINISHED")
    print(f"  Confirmed restaurants : {c:,}")
    print(f"  Non-restaurants       : {n:,}")
    print(f"  Failed / errors       : {f:,}")
    print(f"  Total this session    : {total:,}")
    print(f"  Grand total processed : {len(processed_ids):,}")
    print("=" * 65)


if __name__ == "__main__":
    main()
