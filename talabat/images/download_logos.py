"""
Logo Downloader — logo-only subset of download_images.py
============================================================
Current need is logo-only (menu-item images may be wanted later — that full
path stays untouched in download_images.py). Rather than duplicate the
already-validated download/retry/staging/checkpoint machinery, this script
imports download_images.py directly and reuses it, adding just the
logo-only iteration on top.

Same staging + resumability model as download_images.py (see that module's
docstring for the full reasoning behind both) — not re-explained here.
Logos land at the exact same data/downloaded/{source_id}/logo.<ext> path the
full script would use, so running download_images.py later for menu items
slots section folders in alongside an already-downloaded logo with no
conflict (download_one() skips a file that already exists on disk).

Speed: this is ~15,198 single GET requests total (one logo per restaurant)
vs. ~1M+ for the full menu+logo run — a ~98% smaller job, which is most of
where the speed gain actually comes from. Worker count matches
download_images.py's validated 100 — that figure had zero connection errors
in real testing there (see that module's "Concurrency" docstring section).

A production run of this script was tried once at 150 workers (reasoned as
low-risk from the above, but NOT independently validated at that exact
number) and produced ~1,472 failures (~12%), almost all
"[WinError 10048] Only one usage of each socket address is normally
permitted" — Windows ephemeral port exhaustion: 150 threads opening/closing
HTTPS connections that fast outran how quickly Windows recycles local ports
out of TIME_WAIT (~120s default), which download_images.py's 100-worker
figure apparently stays under. Reverted to 100 here as a result. Do not
raise this past 100 without re-validating on a real run first.

Output:
  data/downloaded/{source_id}/logo.<ext>   (same path download_images.py uses)
  data/logos_manifest.jsonl                (source_id, chain_id, restaurant_name, logo)
  data/logo_download_checkpoint.json
  data/logo_download_failed.jsonl

Run:
  python "talabat/images/download_logos.py"                # full run
  python "talabat/images/download_logos.py" --test         # test_images_confirmed.jsonl only
  python "talabat/images/download_logos.py" --retry-failed # re-attempt logo_download_failed.jsonl
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import download_images as dl
from loguru import logger

DATA_DIR = dl.DATA_DIR
STAGING_DATA_DIR = dl.STAGING_DATA_DIR
DOWNLOAD_DIR = dl.DOWNLOAD_DIR

# Overridable with --workers. 100 was July's validated figure, but the August
# run hit it hard: 1,396 of 7,133 downloads failed with WinError 10048 -
# Windows ephemeral port exhaustion. 7,133 files in 145s is ~49/sec, and every
# closed socket sits in TIME_WAIT for ~120s, so the port pool drained even at
# 100. The ceiling is not a fixed number - it depends on how many ports the
# machine already has tied up - so it is a flag now, defaulted low enough to be
# safe rather than fast. Downloading logos is minutes either way.
CONCURRENT_WORKERS = 30
SAVE_INTERVAL = 200  # checkpoint cadence — full run is short enough this barely matters

# download_one() (imported from download_images) updates ITS OWN module-level
# lock/stats internally — reusing the same objects here (rather than
# defining separate ones) means our progress reporting actually reflects
# what download_one() is doing, instead of a shadow counter stuck at zero.
lock = dl.lock
stats = dl.stats
processed_source_ids = set()


def resolve_paths(args):
    """Every output path in one place, prefixed by cohort.

    July ran with no prefix, so its files keep their original names and are
    never touched by a later month. Each new cohort (august, september, ...)
    gets its own manifest, checkpoint, failed list and log, which is what makes
    the script re-runnable month after month: WITHOUT this, September would
    resume on August's checkpoint and skip every restaurant as already done.

    The IMAGE directory is deliberately NOT prefixed. source_id is Talabat's
    branch_id and does not collide across cohorts (verified: 0 of August's
    7,133 ids appear in July's 15,198), so all logos accumulate into one
    library at data/downloaded/{source_id}/logo.<ext>, and download_one()
    skips anything already on disk.
    """
    pre = ""
    if getattr(args, "cohort", None):
        pre += f"{args.cohort}_"
    if getattr(args, "test", False):
        pre += "test_"

    inp = (Path(args.input) if getattr(args, "input", None)
           else DATA_DIR / f"{pre}images_confirmed.jsonl")
    return {
        "input": inp,
        "manifest": STAGING_DATA_DIR / f"{pre}logos_manifest.jsonl",
        "failed": STAGING_DATA_DIR / f"{pre}logo_download_failed.jsonl",
        "checkpoint": STAGING_DATA_DIR / f"{pre}logo_download_checkpoint.json",
        "log": STAGING_DATA_DIR / f"{pre}logo_download.log",
        "retry_log": STAGING_DATA_DIR / f"{pre}logo_retry.log",
        "final_manifest": DATA_DIR / f"{pre}logos_manifest.jsonl",
        "final_failed": DATA_DIR / f"{pre}logo_download_failed.jsonl",
        # shared across cohorts on purpose - see docstring
        "final_downloads": DATA_DIR / ("test_downloaded" if getattr(args, "test", False)
                                       else "downloaded"),
        "retry_manifest": STAGING_DATA_DIR / f"{pre}logo_retry_manifest.jsonl",
    }


def process_restaurant_logo(row, manifest_f, failed_f, final_download_dir):
    source_id = row["source_id"]
    rest_dir = DOWNLOAD_DIR / str(source_id)

    manifest_entry = {
        "source_id": source_id,
        "chain_id": row.get("chain_id"),
        "restaurant_name": row.get("restaurant_name"),
        "logo": None,
    }

    logo_url = row["images"].get("logo")

    # Talabat serves "/assets/images/img-placeholder.svg" (a RELATIVE url) when
    # a restaurant has uploaded no logo. That is an absence, not a failure -
    # recording it as failed would send us retrying something that will never
    # exist.
    if logo_url and not str(logo_url).lower().startswith("http"):
        with lock:
            stats["no_logo"] = stats.get("no_logo", 0) + 1
            manifest_entry["logo"] = None
            manifest_entry["no_logo_reason"] = "placeholder"
            manifest_f.write(json.dumps(manifest_entry, ensure_ascii=False) + "\n")
            manifest_f.flush()
            processed_source_ids.add(source_id)
        return manifest_entry

    if logo_url:
        try:
            path = dl.download_one(logo_url, rest_dir / "logo")
            # path is still under the STAGING dir here — finalize() moves it
            # into final_download_dir at the very end of the run (and wipes
            # the staging copy), so the manifest must record where the file
            # will actually live afterward, not its transient staging path.
            final_path = final_download_dir / path.relative_to(DOWNLOAD_DIR)
            manifest_entry["logo"] = {"url": logo_url, "local_path": dl.to_local_path_str(final_path)}
        except Exception as exc:
            logger.warning(f"logo failed source_id={source_id}: {exc}")
            with lock:
                stats["failed"] += 1
                failed_f.write(json.dumps({"source_id": source_id, "url": logo_url, "error": str(exc)}, ensure_ascii=False) + "\n")
                failed_f.flush()

    with lock:
        manifest_f.write(json.dumps(manifest_entry, ensure_ascii=False) + "\n")
        manifest_f.flush()
        processed_source_ids.add(source_id)

    return manifest_entry


def main(args):
    STAGING_DATA_DIR.mkdir(parents=True, exist_ok=True)
    P = resolve_paths(args)
    input_path = P["input"]
    manifest_path = P["manifest"]
    failed_path = P["failed"]
    checkpoint_path = P["checkpoint"]
    log_path = P["log"]
    final_manifest_path = P["final_manifest"]
    final_download_dir = P["final_downloads"]

    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level="INFO")
    logger.add(log_path, level="INFO", rotation="20 MB", encoding="utf-8")

    if not input_path.exists():
        logger.error(f"{input_path} does not exist — run scrape_menu_images.py first")
        return

    rows = dl.load_jsonl(input_path)
    logger.info(f"Loaded {len(rows):,} restaurants (logo-only run) from {input_path.name}")

    already_done = dl.load_checkpoint(checkpoint_path)
    processed_source_ids.update(already_done)
    if already_done:
        logger.info(f"Resuming: {len(already_done):,} restaurants already processed")

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

        futures = {executor.submit(process_restaurant_logo, row, manifest_f, failed_f, final_download_dir): row["source_id"] for row in remaining}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                fut.result()
            except Exception as exc:
                logger.error(f"Unhandled error processing a restaurant: {exc}")

            if i % SAVE_INTERVAL == 0 or i == len(remaining):
                with lock:
                    dl.save_checkpoint(checkpoint_path, processed_source_ids)
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta_sec = (len(remaining) - i) / rate if rate > 0 else 0
                logger.info(
                    f"Progress: {i:,}/{len(remaining):,} restaurants | "
                    f"downloaded={stats['downloaded']:,} skipped={stats['skipped']:,} failed={stats['failed']:,} | "
                    f"{rate:.1f} logos/sec | ETA {eta_sec:.0f}s"
                )

    dl.save_checkpoint(checkpoint_path, processed_source_ids)

    # Same manifest-dedup pass as download_images.py, and for the same reason:
    # checkpoint saves lag SAVE_INTERVAL behind per-restaurant manifest
    # writes, so an interrupted+resumed run can re-append a line.
    with open(manifest_path, "r", encoding="utf-8") as f:
        seen = {}
        for line in f:
            line = line.strip()
            if line:
                entry = json.loads(line)
                seen[entry["source_id"]] = entry
    with open(manifest_path, "w", encoding="utf-8") as f:
        for entry in seen.values():
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info(f"Manifest deduped: {len(seen):,} unique restaurants -> {manifest_path}")

    logger.info(f"Finalizing: moving staging -> {final_download_dir} (one bulk operation, see download_images.py docstring)")
    dl.finalize(DOWNLOAD_DIR, final_download_dir, manifest_path, final_manifest_path)

    final_failed_path = P["final_failed"]
    merged_failed = {}
    if final_failed_path.exists():
        for rec in dl.load_jsonl(final_failed_path):
            merged_failed[rec["source_id"]] = rec
    for rec in dl.load_jsonl(failed_path):
        merged_failed[rec["source_id"]] = rec
    with open(final_failed_path, "w", encoding="utf-8") as f:
        for rec in merged_failed.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    elapsed = time.time() - t0
    logger.info("=" * 65)
    logger.info("FINISHED")
    logger.info(f"  Restaurants processed this run : {len(remaining):,}")
    logger.info(f"  Logos downloaded               : {stats['downloaded']:,}")
    logger.info(f"  Logos already present (skipped): {stats['skipped']:,}")
    logger.info(f"  Logos failed                   : {stats['failed']:,}")
    logger.info(f"  Elapsed                        : {elapsed:.1f}s ({elapsed/60:.1f} min)")
    logger.info(f"  Final images -> {final_download_dir}")
    logger.info(f"  Final manifest -> {final_manifest_path}")
    if merged_failed:
        logger.info(f"  {len(merged_failed):,} failed -> {final_failed_path} (run --retry-failed to re-attempt)")
    logger.info("=" * 65)


def retry_failed(args):
    """Logo-only retry — simpler than download_images.py's version since
    there's at most one failure per restaurant (the logo itself), not a
    list of per-item failures to group."""
    P = resolve_paths(args)
    final_failed_path = P["final_failed"]
    final_manifest_path = P["final_manifest"]
    final_download_dir = P["final_downloads"]
    log_path = P["retry_log"]

    STAGING_DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level="INFO")
    logger.add(log_path, level="INFO", rotation="20 MB", encoding="utf-8")

    failed = dl.load_jsonl(final_failed_path)
    if not failed:
        logger.info(f"{final_failed_path} is empty or missing — nothing to retry.")
        return
    logger.info(f"Retrying {len(failed):,} failed logos | workers={CONCURRENT_WORKERS}")

    manifest_by_sid = {e["source_id"]: e for e in dl.load_jsonl(final_manifest_path)}
    still_failed = []
    recovered = 0

    def retry_one(f):
        nonlocal recovered
        source_id = f["source_id"]
        rest_dir = DOWNLOAD_DIR / str(source_id)
        try:
            path = dl.download_one(f["url"], rest_dir / "logo")
            final_path = final_download_dir / path.relative_to(DOWNLOAD_DIR)
            entry = manifest_by_sid.setdefault(source_id, {"source_id": source_id, "chain_id": None, "restaurant_name": None, "logo": None})
            entry["logo"] = {"url": f["url"], "local_path": dl.to_local_path_str(final_path)}
            with lock:
                recovered += 1
        except Exception as exc:
            logger.warning(f"still failing source_id={source_id}: {exc}")
            still_failed.append({**f, "error": str(exc)})

    with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
        futures = [executor.submit(retry_one, f) for f in failed]
        for i, fut in enumerate(as_completed(futures), 1):
            fut.result()
            if i % 20 == 0 or i == len(futures):
                logger.info(f"Retried {i:,}/{len(futures):,} | recovered so far={recovered:,}")

    staging_manifest_path = P["retry_manifest"]
    with open(staging_manifest_path, "w", encoding="utf-8") as f:
        for entry in manifest_by_sid.values():
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    dl.finalize(DOWNLOAD_DIR, final_download_dir, staging_manifest_path, final_manifest_path)

    with open(final_failed_path, "w", encoding="utf-8") as f:
        for rec in still_failed:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    logger.info("=" * 65)
    logger.info("RETRY FINISHED")
    logger.info(f"  Attempted     : {len(failed):,}")
    logger.info(f"  Recovered     : {recovered:,}")
    logger.info(f"  Still failing : {len(still_failed):,}")
    if still_failed:
        logger.info(f"  Likely permanently gone from the CDN if failing across multiple retry passes -> {final_failed_path}")
    logger.info("=" * 65)


if __name__ == "__main__":
    _parser = argparse.ArgumentParser()
    _parser.add_argument("--workers", type=int,
                         help="download concurrency (default 30). Raising this "
                              "risks WinError 10048 port exhaustion - measured "
                              "1,396 failures at 100.")
    _parser.add_argument("--cohort", help="cohort name, e.g. august / september. "
                         "Prefixes every manifest, checkpoint, failed list and "
                         "log so months never share state. Omit for July's "
                         "original unprefixed files.")
    _parser.add_argument("--input", help="override the input JSONL "
                         "(default: data/<cohort>_images_confirmed.jsonl)")
    _parser.add_argument("--test", action="store_true", help="Use test_images_confirmed.jsonl instead of the full images_confirmed.jsonl")
    _parser.add_argument("--retry-failed", action="store_true", help="Re-attempt everything in logo_download_failed.jsonl instead of doing a normal run")
    _args = _parser.parse_args()
    if _args.workers:
        CONCURRENT_WORKERS = _args.workers

    if _args.retry_failed:
        retry_failed(_args)
    else:
        main(_args)
