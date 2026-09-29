"""
Image Downloader — Phase 2
=============================
Reads the URLs already extracted by scrape_menu_images.py (images_confirmed.jsonl)
and downloads the actual logo + menu-item image FILES to disk, organized per
restaurant, plus a manifest joining local file paths back to
(source_id, chain_id, restaurant_name) for IT to hand to the app.

Deliberately a SEPARATE script/phase from scrape_menu_images.py, not bolted
onto it: the page-scraping step needs the Oxylabs proxy (Talabat itself has
bot protection) and is slow (~1-2.5s delay per request); the image CDN
(images.deliveryhero.io) is a separate plain asset host with NO bot
protection — confirmed empirically (55.6 images/sec sustained with 20 plain
threads, zero proxy, on a real 267-image test batch — 2 failures, both
genuinely stale/removed source URLs, not a proxy or rate-limit issue).
Mixing the two would make the slow, rate-limited step the bottleneck for
work that doesn't need to be rate-limited at all, and would make each phase
harder to retry independently.

Resumability: TWO layers, restaurant-level and file-level. A checkpoint.json
(same pattern as scrape_menu_images.py) records which source_ids have been
fully processed, saved every SAVE_INTERVAL restaurants — this is what makes
progress traceable/inspectable mid-run and lets a restart skip straight to
the remaining work instead of re-walking all 15,198 restaurants. Underneath
that, individual file existence on disk is a second, finer-grained safety
net for a restaurant that was interrupted partway through its own images
(e.g. process killed after downloading 40 of its 60 images, before that
restaurant ever got checkpointed) — download_one() skips a file that's
already there regardless of what the checkpoint says.

Concurrency: no proxy involved here (unlike Phase 1 — the image CDN,
images.deliveryhero.io, is a separate plain asset host with no bot
protection, confirmed empirically: 55.6 images/sec at 20 threads with zero
errors on a real 267-image batch). An early attempt at 100 workers appeared
to break down — clustered "Read timed out" and SSL
DECRYPTION_FAILED_OR_BAD_RECORD_MAC errors — but that test ran directly
against the OneDrive-synced path (see "Staging" below), which turned out to
be the actual cause, not the worker count. Once re-tested cleanly on a
plain non-synced path, 100 workers had ZERO connection errors and was
~12% faster than 50 (100: 38.2 img/s / 182.2s; 50: 33.6 img/s / 207.1s,
both on the identical 100-restaurant/6,966-image sample, both with the same
8 genuinely-stale-URL failures and nothing else). download_one() also
retries transient errors (not 404s, which are permanently stale) a few
times with backoff, to absorb whatever occasional blips still happen.

Staging (important): this whole project lives inside a OneDrive-synced
folder, and writing hundreds of thousands of small files directly into it
turned out to be the DOMINANT source of instability — confirmed directly by
running the identical 50-worker/100-restaurant/6,966-image batch against
the OneDrive path vs. a plain local path: 496 failures + 783.5s on the
OneDrive path vs. 8 failures (all genuinely-stale 404s) + 192.5s on a plain
path. So downloads happen in STAGING_DOWNLOAD_DIR (a plain local folder,
not synced) during the run, and get moved into the real project location in
ONE bulk operation at the very end (finalize()) — OneDrive then only ever
sees one burst of already-complete files, not continuous churn for the full
run.

Full coverage: a restaurant is checkpointed as "done" once attempted, even
if a few of its images failed — so a plain re-run won't retry stragglers.
Run with --retry-failed to re-attempt everything in download_failed.jsonl
specifically (individual images, not whole restaurants); still-failing
entries after that overwrite the failed log, so repeated retries only ever
narrow down to what's genuinely permanent (deleted from the CDN).

Output layout — category + item name are encoded into the path itself so the
downloaded files are self-describing to a human browsing the folder, not
just to something that reads images_manifest.jsonl (which remains the
authoritative machine-readable join on item_id, for anything programmatic):
  data/downloaded/{source_id}/logo.<ext>
  data/downloaded/{source_id}/{section_slug}/{item_id}_{name_slug}.<ext>
  data/images_manifest.jsonl   (source_id, chain_id, restaurant_name, local
                                 paths + original URLs for both logo and
                                 every menu item)
  data/download_checkpoint.json (processed source_ids, for resume/tracing)

Run:
  python "talabat/images/download_images.py"                # full run
  python "talabat/images/download_images.py" --test         # test_images_confirmed.jsonl only
"""

import argparse
import json
import mimetypes
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
from loguru import logger

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"  # final destination — also where images_confirmed.jsonl (Phase 1's output) is read from

# Plain local folder, NOT OneDrive-synced — see module docstring "Staging".
STAGING_ROOT = Path(r"C:\talabat_images_staging")
STAGING_DATA_DIR = STAGING_ROOT / "data"
DOWNLOAD_DIR = STAGING_DATA_DIR / "downloaded"

CONCURRENT_WORKERS = 100  # see module docstring "Concurrency" for the full validated history
REQUEST_TIMEOUT = 15
SAVE_INTERVAL = 100  # checkpoint every N restaurants processed

# Talabat's own menuData.items commonly lists the SAME item_id twice — once
# under the generic "Picks for you" promo section, again under its real
# category (same item_id/name/image both times, confirmed by hand). Prefer
# the real-category occurrence when deduping so the manifest doesn't show a
# meaningless "Picks for you" label when a real one is available.
POPULAR_KEYWORDS = ("picks for you", "popular", "best seller")


def is_generic_section(section):
    if not section:
        return True
    low = section.lower()
    return any(kw in low for kw in POPULAR_KEYWORDS)


def dedupe_menu_items(items):
    by_id = {}
    for it in items:
        item_id = it.get("item_id")
        existing = by_id.get(item_id)
        if existing is None or (is_generic_section(existing.get("section")) and not is_generic_section(it.get("section"))):
            by_id[item_id] = it
    return list(by_id.values())


# Windows-illegal filename chars, control chars, and path separators — used
# to make section/item names safe as folder and file name components while
# keeping them human-readable (unlike a pure hash/ID, the whole point here).
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name_part(text, max_len=60, fallback="unknown"):
    if not text:
        return fallback
    cleaned = INVALID_FILENAME_CHARS.sub("", text).strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = cleaned.strip("-.")
    return cleaned[:max_len] or fallback


lock = threading.Lock()
stats = {"downloaded": 0, "skipped": 0, "failed": 0}
processed_source_ids = set()


def load_checkpoint(path):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f).get("processed_source_ids", []))
    return set()


def save_checkpoint(path, ids):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({
            "processed_source_ids": list(ids),
            "count": len(ids),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }, f)
    os.replace(tmp, path)


def guess_extension(url, content_type):
    # prefer a real extension already in the URL path (most images have one)
    path = urlsplit(url).path
    suffix = Path(path).suffix
    if suffix and len(suffix) <= 5 and suffix[1:].isalnum():
        return suffix
    # fall back to Content-Type (needed for hash-named URLs with no extension,
    # e.g. Häagen-Dazs's ".../MenuItems/26B7CE7F8D96A286D4A0501D8AB7C3CD")
    ext = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
    return ext or ".jpg"


DOWNLOAD_RETRY_ATTEMPTS = 3


def winlong(path):
    """Windows' legacy MAX_PATH (260 chars) is easy to exceed with
    source_id/category/item-name nesting under an already-long base path —
    confirmed directly: a test run hit '[Errno 2] No such file or directory'
    on writes purely from path length (273 chars), not a real missing-
    directory issue. Prefixing an absolute path with \\\\?\\ tells the
    Windows API to bypass that limit (supports up to ~32,767 chars);
    no-op on other platforms."""
    if os.name == "nt":
        s = str(path.resolve())
        return s if s.startswith("\\\\?\\") else "\\\\?\\" + s
    return str(path)


def download_one(url, dest_no_ext):
    """Returns the final Path on success, or None if dest already exists
    with SOME extension (skip), or raises on failure.

    Retries transient errors (timeouts, connection/SSL errors — seen in
    practice under high concurrency, e.g. clustered "Read timed out" and
    SSL DECRYPTION_FAILED_OR_BAD_RECORD_MAC errors when this was briefly run
    at 100 workers) with a short backoff. Does NOT retry a 404 — that's a
    permanently stale/removed URL on the CDN, confirmed by hand earlier
    (retrying won't make a genuinely deleted file reappear)."""
    existing = list(dest_no_ext.parent.glob(dest_no_ext.name + ".*"))
    if existing:
        with lock:
            stats["skipped"] += 1
        return existing[0]

    last_exc = None
    for attempt in range(DOWNLOAD_RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            ext = guess_extension(url, resp.headers.get("Content-Type", ""))
            dest = dest_no_ext.with_name(dest_no_ext.name + ext)
            os.makedirs(winlong(dest.parent), exist_ok=True)
            with open(winlong(dest), "wb") as f:
                f.write(resp.content)
            with lock:
                stats["downloaded"] += 1
            return dest
        except requests.exceptions.HTTPError:
            raise  # 404/etc — permanent, no point retrying
        except Exception as exc:
            last_exc = exc
            if attempt < DOWNLOAD_RETRY_ATTEMPTS - 1:
                time.sleep(0.5 * (attempt + 1))
    raise last_exc


def to_local_path_str(path):
    """Relative to this script's folder when possible (portable, matches
    the rest of the repo's convention); falls back to an absolute path if
    DOWNLOAD_DIR isn't actually under HERE (e.g. temporarily redirected
    elsewhere for testing)."""
    try:
        return path.relative_to(HERE).as_posix()
    except ValueError:
        return path.as_posix()


def process_restaurant(row, manifest_f, failed_f, final_download_dir):
    source_id = row["source_id"]
    rest_dir = DOWNLOAD_DIR / str(source_id)

    manifest_entry = {
        "source_id": source_id,
        "chain_id": row.get("chain_id"),
        "restaurant_name": row.get("restaurant_name"),
        "logo": None,
        "menu_items": [],
    }

    logo_url = row["images"].get("logo")
    if logo_url:
        try:
            path = download_one(logo_url, rest_dir / "logo")
            # path is still under the STAGING dir here — finalize() moves it
            # into final_download_dir at the very end of the run (and wipes
            # the staging copy), so the manifest must record where the file
            # will actually live afterward, not its transient staging path.
            final_path = final_download_dir / path.relative_to(DOWNLOAD_DIR)
            manifest_entry["logo"] = {"url": logo_url, "local_path": to_local_path_str(final_path)}
        except Exception as exc:
            logger.warning(f"logo failed source_id={source_id}: {exc}")
            with lock:
                stats["failed"] += 1
                failed_f.write(json.dumps({"source_id": source_id, "kind": "logo", "url": logo_url, "error": str(exc)}, ensure_ascii=False) + "\n")
                failed_f.flush()

    deduped_items = dedupe_menu_items(row["images"].get("menu_items", []))
    for item in deduped_items:
        url = item.get("image")
        if not url:
            continue
        try:
            section_slug = safe_name_part(item.get("section"), fallback="uncategorized")
            name_slug = safe_name_part(item.get("name"))
            dest_no_ext = rest_dir / section_slug / f"{item['item_id']}_{name_slug}"
            path = download_one(url, dest_no_ext)
            final_path = final_download_dir / path.relative_to(DOWNLOAD_DIR)
            manifest_entry["menu_items"].append({
                "item_id": item.get("item_id"),
                "name": item.get("name"),
                "section": item.get("section"),
                "url": url,
                "local_path": to_local_path_str(final_path),
            })
        except Exception as exc:
            logger.warning(f"menu_item failed source_id={source_id} item_id={item.get('item_id')}: {exc}")
            with lock:
                stats["failed"] += 1
                failed_f.write(json.dumps({"source_id": source_id, "kind": "menu_item", "item_id": item.get("item_id"), "url": url, "error": str(exc)}, ensure_ascii=False) + "\n")
                failed_f.flush()

    with lock:
        manifest_f.write(json.dumps(manifest_entry, ensure_ascii=False) + "\n")
        manifest_f.flush()
        processed_source_ids.add(source_id)

    return manifest_entry


def finalize(staging_download_dir, final_download_dir, staging_manifest_path, final_manifest_path):
    """Bulk-move everything from the non-synced staging area into the real
    OneDrive-synced project location, in one shot at the very end — see
    module docstring "Staging" for why this happens instead of downloading
    directly into the synced folder."""
    final_download_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for restaurant_dir in staging_download_dir.iterdir():
        if not restaurant_dir.is_dir():
            continue
        dest = final_download_dir / restaurant_dir.name
        if dest.exists():
            # MERGE, don't replace — matters for retry_failed(), which only
            # stages the handful of previously-failed files for a
            # restaurant, not its full set. Replacing the destination
            # wholesale would delete everything that already succeeded.
            for item in restaurant_dir.rglob("*"):
                if item.is_file():
                    target = dest / item.relative_to(restaurant_dir)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(item), str(target))
            shutil.rmtree(restaurant_dir)
        else:
            shutil.move(str(restaurant_dir), str(dest))
        moved += 1
    logger.info(f"Moved {moved:,} restaurant folders from staging -> {final_download_dir}")

    existing = {}
    if final_manifest_path.exists():
        with open(final_manifest_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    existing[entry["source_id"]] = entry
    with open(staging_manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                existing[entry["source_id"]] = entry
    with open(final_manifest_path, "w", encoding="utf-8") as f:
        for entry in existing.values():
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info(f"Final manifest: {len(existing):,} restaurants -> {final_manifest_path}")


def main(args):

    STAGING_DATA_DIR.mkdir(parents=True, exist_ok=True)
    input_path = DATA_DIR / ("test_images_confirmed.jsonl" if args.test else "images_confirmed.jsonl")
    manifest_path = STAGING_DATA_DIR / ("test_images_manifest.jsonl" if args.test else "images_manifest.jsonl")
    failed_path = STAGING_DATA_DIR / ("test_download_failed.jsonl" if args.test else "download_failed.jsonl")
    checkpoint_path = STAGING_DATA_DIR / ("test_download_checkpoint.json" if args.test else "download_checkpoint.json")
    log_path = STAGING_DATA_DIR / ("test_download.log" if args.test else "download.log")
    final_manifest_path = DATA_DIR / ("test_images_manifest.jsonl" if args.test else "images_manifest.jsonl")
    final_download_dir = DATA_DIR / ("test_downloaded" if args.test else "downloaded")

    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level="INFO")
    logger.add(log_path, level="INFO", rotation="50 MB", encoding="utf-8")

    if not input_path.exists():
        logger.error(f"{input_path} does not exist — run scrape_menu_images.py first")
        return

    rows = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    total_images = sum(1 + len(r["images"].get("menu_items", [])) for r in rows)
    logger.info(f"Loaded {len(rows):,} restaurants ({total_images:,} images total incl. logos) from {input_path.name}")

    already_done = load_checkpoint(checkpoint_path)
    processed_source_ids.update(already_done)
    if already_done:
        logger.info(f"Resuming: {len(already_done):,} restaurants already fully processed (checkpoint + on-disk files)")

    remaining = [r for r in rows if r["source_id"] not in already_done]
    logger.info(f"Remaining: {len(remaining):,} restaurants | workers={CONCURRENT_WORKERS} (no proxy needed for the image CDN)")

    if not remaining:
        logger.info("Nothing to do.")
        return

    t0 = time.time()
    manifest_mode = "a" if already_done else "w"
    with open(manifest_path, manifest_mode, encoding="utf-8") as manifest_f, \
         open(failed_path, manifest_mode, encoding="utf-8") as failed_f, \
         ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:

        futures = {executor.submit(process_restaurant, row, manifest_f, failed_f, final_download_dir): row["source_id"] for row in remaining}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                fut.result()
            except Exception as exc:
                logger.error(f"Unhandled error processing a restaurant: {exc}")

            if i % SAVE_INTERVAL == 0 or i == len(remaining):
                with lock:
                    save_checkpoint(checkpoint_path, processed_source_ids)
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta_min = (len(remaining) - i) / rate / 60 if rate > 0 else 0
                logger.info(
                    f"Progress: {i:,}/{len(remaining):,} restaurants | "
                    f"downloaded={stats['downloaded']:,} skipped={stats['skipped']:,} failed={stats['failed']:,} | "
                    f"{rate:.1f} restaurants/sec | ETA {eta_min:.1f} min"
                )

    save_checkpoint(checkpoint_path, processed_source_ids)

    # The checkpoint only saves every SAVE_INTERVAL restaurants, but each
    # restaurant's manifest line is appended immediately on completion — so
    # a run interrupted between two checkpoint saves and then resumed will
    # re-process (and re-append a manifest line for) whatever finished after
    # the last save. download_one() skips the actual re-downloads via
    # on-disk file existence, but the manifest itself needs a final dedup
    # pass to guarantee one line per restaurant regardless of how a run was
    # interrupted/resumed.
    with open(manifest_path, "r", encoding="utf-8") as f:
        seen = {}
        for line in f:
            line = line.strip()
            if line:
                entry = json.loads(line)
                seen[entry["source_id"]] = entry  # last occurrence wins
    with open(manifest_path, "w", encoding="utf-8") as f:
        for entry in seen.values():
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info(f"Manifest deduped: {len(seen):,} unique restaurants -> {manifest_path}")

    logger.info(f"Finalizing: moving staging -> {final_download_dir} (one bulk operation, see module docstring)")
    finalize(DOWNLOAD_DIR, final_download_dir, manifest_path, final_manifest_path)

    # failed log also needs to land somewhere findable for retry_failed() —
    # merge into the final one by (source_id, kind, item_id) so re-running
    # main() doesn't just pile up duplicate entries for the same failure.
    final_failed_path = DATA_DIR / ("test_download_failed.jsonl" if args.test else "download_failed.jsonl")
    merged_failed = {}
    if final_failed_path.exists():
        for rec in load_jsonl(final_failed_path):
            merged_failed[(rec["source_id"], rec["kind"], rec.get("item_id"))] = rec
    for rec in load_jsonl(failed_path):
        merged_failed[(rec["source_id"], rec["kind"], rec.get("item_id"))] = rec
    with open(final_failed_path, "w", encoding="utf-8") as f:
        for rec in merged_failed.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    elapsed = time.time() - t0
    logger.info("=" * 65)
    logger.info("FINISHED")
    logger.info(f"  Restaurants processed this run : {len(remaining):,}")
    logger.info(f"  Images downloaded              : {stats['downloaded']:,}")
    logger.info(f"  Images already present (skipped): {stats['skipped']:,}")
    logger.info(f"  Images failed                  : {stats['failed']:,}")
    logger.info(f"  Elapsed                        : {elapsed:.1f}s")
    logger.info(f"  Final images -> {final_download_dir}")
    logger.info(f"  Final manifest -> {final_manifest_path}")
    logger.info(f"  Final failed log ({len(merged_failed):,} total) -> {final_failed_path}")
    if merged_failed:
        logger.info(f"  Run with --retry-failed to re-attempt everything in that failed log")
    logger.info("=" * 65)


def load_jsonl(path):
    rows = []
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def retry_failed(args):
    """Re-attempts every entry in download_failed.jsonl — full coverage is
    the goal, so nothing gets silently left behind just because it failed
    on the first pass (same principle as re-running scrape_menu_images.py
    for its 51 stragglers, applied here at the individual-image level
    instead of the whole-restaurant level, since one restaurant can have 58
    successful images and 2 stragglers). Still-failing entries after this
    (e.g. genuinely deleted CDN files, confirmed 404 on every attempt so
    far) overwrite download_failed.jsonl, so running this again only ever
    narrows down to what's truly permanent."""
    final_failed_path = DATA_DIR / ("test_download_failed.jsonl" if args.test else "download_failed.jsonl")
    final_manifest_path = DATA_DIR / ("test_images_manifest.jsonl" if args.test else "images_manifest.jsonl")
    final_download_dir = DATA_DIR / ("test_downloaded" if args.test else "downloaded")
    input_path = DATA_DIR / ("test_images_confirmed.jsonl" if args.test else "images_confirmed.jsonl")
    log_path = STAGING_DATA_DIR / ("test_retry.log" if args.test else "retry.log")

    STAGING_DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level="INFO")
    logger.add(log_path, level="INFO", rotation="20 MB", encoding="utf-8")

    failed = load_jsonl(final_failed_path)
    if not failed:
        logger.info(f"{final_failed_path} is empty or missing — nothing to retry.")
        return
    logger.info(f"Loaded {len(failed):,} failed entries from {final_failed_path.name}")

    original_by_sid = {r["source_id"]: r for r in load_jsonl(input_path)}
    manifest_by_sid = {e["source_id"]: e for e in load_jsonl(final_manifest_path)}

    by_source = {}
    for f in failed:
        by_source.setdefault(f["source_id"], []).append(f)

    still_failed = []
    recovered = 0

    def retry_one(source_id, fail_entries):
        nonlocal recovered
        original = original_by_sid.get(source_id)
        rest_dir = DOWNLOAD_DIR / str(source_id)
        entry = manifest_by_sid.setdefault(source_id, {
            "source_name": "talabat", "source_id": source_id,
            "chain_id": original.get("chain_id") if original else None,
            "restaurant_name": original.get("restaurant_name") if original else None,
            "logo": None, "menu_items": [],
        })
        existing_item_ids = {it["item_id"] for it in entry["menu_items"]}

        for f in fail_entries:
            try:
                if f["kind"] == "logo":
                    path = download_one(f["url"], rest_dir / "logo")
                    final_path = final_download_dir / path.relative_to(DOWNLOAD_DIR)
                    entry["logo"] = {"url": f["url"], "local_path": to_local_path_str(final_path)}
                else:
                    item = None
                    if original:
                        item = next((it for it in original["images"].get("menu_items", []) if it.get("item_id") == f.get("item_id")), None)
                    name = item["name"] if item else str(f.get("item_id"))
                    section = item.get("section") if item else None
                    dest_no_ext = rest_dir / safe_name_part(section, fallback="uncategorized") / f"{f['item_id']}_{safe_name_part(name)}"
                    path = download_one(f["url"], dest_no_ext)
                    final_path = final_download_dir / path.relative_to(DOWNLOAD_DIR)
                    if f["item_id"] not in existing_item_ids:
                        entry["menu_items"].append({"item_id": f.get("item_id"), "name": name, "section": section, "url": f["url"], "local_path": to_local_path_str(final_path)})
                        existing_item_ids.add(f["item_id"])
                with lock:
                    recovered += 1
            except Exception as exc:
                logger.warning(f"still failing source_id={source_id} kind={f['kind']} item_id={f.get('item_id')}: {exc}")
                still_failed.append({**f, "error": str(exc)})

    logger.info(f"Retrying across {len(by_source):,} restaurants with at least one failure | workers={CONCURRENT_WORKERS}")
    with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
        futures = [executor.submit(retry_one, sid, entries) for sid, entries in by_source.items()]
        for i, fut in enumerate(as_completed(futures), 1):
            fut.result()
            if i % 20 == 0 or i == len(futures):
                logger.info(f"Retried {i:,}/{len(futures):,} restaurants | recovered so far={recovered:,}")

    staging_manifest_path = STAGING_DATA_DIR / "retry_manifest.jsonl"
    with open(staging_manifest_path, "w", encoding="utf-8") as f:
        for entry in manifest_by_sid.values():
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    finalize(DOWNLOAD_DIR, final_download_dir, staging_manifest_path, final_manifest_path)

    with open(final_failed_path, "w", encoding="utf-8") as f:
        for rec in still_failed:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    logger.info("=" * 65)
    logger.info("RETRY FINISHED")
    logger.info(f"  Attempted        : {len(failed):,}")
    logger.info(f"  Recovered        : {recovered:,}")
    logger.info(f"  Still failing    : {len(still_failed):,}")
    if still_failed:
        logger.info(f"  These are likely permanently gone from the CDN if they've failed across multiple retry passes -> {final_failed_path}")
    logger.info("=" * 65)


if __name__ == "__main__":
    _parser = argparse.ArgumentParser()
    _parser.add_argument("--test", action="store_true", help="Use test_images_confirmed.jsonl instead of the full images_confirmed.jsonl")
    _parser.add_argument("--retry-failed", action="store_true", help="Re-attempt everything in download_failed.jsonl instead of doing a normal run")
    _args = _parser.parse_args()

    if _args.retry_failed:
        retry_failed(_args)
    else:
        main(_args)
