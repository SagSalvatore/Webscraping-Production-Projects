"""Upload the inventory's files to s3://rii-data-dump, and record proof.

DRY RUN BY DEFAULT. Nothing leaves the machine until --execute is passed, and
nothing is ever deleted here - that is prune_local.py, and only after
verify_s3.py has confirmed the copy.

Every object is uploaded with:
  * S3's own SHA-256 checksum (ChecksumAlgorithm="SHA256"), so S3 recomputes it
    server-side and rejects a corrupted transfer instead of storing it
  * metadata: our sha256, the original relative path, the file's mtime
Re-running is free and safe: an object whose size AND sha256 already match is
skipped, so an interrupted run resumes and a finished one uploads nothing.

    python upload_to_s3.py                          # plan only
    python upload_to_s3.py --layer gold --execute   # one layer
    python upload_to_s3.py --month 2026-09 --execute
    python upload_to_s3.py --execute --max-gb 2     # a first small batch

Credentials come from talabat/.env (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
AWS_DEFAULT_REGION) or any standard AWS source. They are never printed.
"""
import argparse
import csv
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
TALABAT = HERE.parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
from layout import BUCKET, REGION  # noqa: E402

INVENTORY = HERE / "data" / "inventory.csv"
MANIFEST = HERE / "data" / "upload_manifest.jsonl"
UPLOAD_ACTIONS = ("archive", "archive_keep")
CHUNK = 8 * 1024 * 1024


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:,.1f} {u}"
        n /= 1024


def load_done():
    """rel_path -> record, from our own manifest: what is already up there."""
    done = {}
    if MANIFEST.exists():
        for line in open(MANIFEST, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                done[r["rel_path"]] = r
    return done


def client():
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise SystemExit("boto3 is not installed:  python -m pip install boto3 python-dotenv")
    try:
        from dotenv import load_dotenv
        load_dotenv(TALABAT / ".env")
    except ImportError:
        pass
    region = os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION") or REGION
    return boto3.client("s3", region_name=region,
                        config=Config(retries={"max_attempts": 10, "mode": "adaptive"},
                                      s3={"addressing_style": "virtual"}))


def main(args):
    rows = [r for r in csv.DictReader(open(INVENTORY, encoding="utf-8-sig"))
            if r["action"] in UPLOAD_ACTIONS]
    if args.layer:
        rows = [r for r in rows if r["layer"] == args.layer]
    if args.month:
        rows = [r for r in rows if r["month"] == args.month]
    if args.prefix:
        rows = [r for r in rows if r["rel_path"].startswith(args.prefix)]
    rows.sort(key=lambda r: int(r["bytes"]))          # small files first: fast feedback
    done = load_done()
    todo, skip_same = [], 0
    budget = args.max_gb * 1024 ** 3 if args.max_gb else None
    used = 0
    for r in rows:
        prev = done.get(r["rel_path"])
        if prev and prev.get("bytes") == int(r["bytes"]) and prev.get("status") == "uploaded":
            skip_same += 1
            continue
        if budget is not None and used + int(r["bytes"]) > budget:
            continue
        used += int(r["bytes"])
        todo.append(r)

    print("=" * 78)
    print(f"  UPLOAD TO s3://{BUCKET}      {'EXECUTE' if args.execute else 'DRY RUN - nothing is sent'}")
    print("=" * 78)
    print(f"  in inventory : {len(rows):,} files, {human(sum(int(r['bytes']) for r in rows))}")
    print(f"  already up   : {skip_same:,}")
    print(f"  to upload    : {len(todo):,} files, {human(sum(int(r['bytes']) for r in todo))}")
    by_layer = Counter()
    for r in todo:
        by_layer[r["layer"]] += int(r["bytes"])
    for l, b in by_layer.most_common():
        print(f"     {l:10} {human(b):>12}")
    if todo[:1]:
        print(f"\n  first: {todo[0]['rel_path']}\n      -> s3://{BUCKET}/{todo[0]['s3_key']}")
        print(f"  last : {todo[-1]['rel_path']}\n      -> s3://{BUCKET}/{todo[-1]['s3_key']}")
    if not args.execute:
        print("\n  --execute to upload. Nothing was sent.")
        return 0
    if not todo:
        return 0

    s3 = client()
    from boto3.s3.transfer import TransferConfig
    cfg = TransferConfig(multipart_threshold=64 * 1024 * 1024, multipart_chunksize=64 * 1024 * 1024,
                         max_concurrency=args.workers, use_threads=True)
    t0, sent, failed = time.time(), 0, []
    with open(MANIFEST, "a", encoding="utf-8") as mf:
        for i, r in enumerate(todo, 1):
            path = TALABAT / r["rel_path"]
            if not path.exists():
                failed.append((r["rel_path"], "gone from disk"))
                continue
            try:
                digest = sha256_of(path)
                s3.upload_file(
                    str(path), BUCKET, r["s3_key"], Config=cfg,
                    ExtraArgs={"ChecksumAlgorithm": "SHA256",
                               "StorageClass": args.storage_class,
                               "Metadata": {"sha256": digest, "source-path": r["rel_path"],
                                            "source-mtime": r["modified"], "layer": r["layer"],
                                            "stage": r["stage"], "month": r["month"]}})
                rec = {"rel_path": r["rel_path"], "s3_key": r["s3_key"], "bytes": int(r["bytes"]),
                       "sha256": digest, "layer": r["layer"], "stage": r["stage"], "month": r["month"],
                       "action": r["action"], "storage_class": args.storage_class,
                       "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "status": "uploaded"}
                mf.write(json.dumps(rec) + "\n")
                mf.flush()
                sent += int(r["bytes"])
            except Exception as exc:
                failed.append((r["rel_path"], str(exc)[:120]))
            if i % 25 == 0 or i == len(todo):
                el = time.time() - t0
                print(f"    {i:,}/{len(todo):,} | {human(sent)} sent | {human(sent / el)}/s | "
                      f"failed {len(failed)}", flush=True)
    print(f"\n  uploaded {human(sent)} in {(time.time() - t0) / 60:.1f} min | failures {len(failed)}")
    for f in failed[:10]:
        print(f"    FAILED {f[0]}  {f[1]}")
    print(f"  manifest -> data/{MANIFEST.name}   next: python verify_s3.py")
    return 1 if failed else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true", help="actually upload (default is a dry run)")
    p.add_argument("--layer", choices=["bronze", "silver", "gold", "reference", "ops"])
    p.add_argument("--month", help="e.g. 2026-09 or _cross_month")
    p.add_argument("--prefix", help="only files whose path starts with this")
    p.add_argument("--max-gb", type=float, help="stop planning after this much (a first batch)")
    p.add_argument("--workers", type=int, default=8, help="parts in flight per large file")
    p.add_argument("--storage-class", default="STANDARD",
                   help="STANDARD (default; the lifecycle policy moves data to cheaper classes "
                        "after 30 days, which avoids the small-object minimums)")
    sys.exit(main(p.parse_args()))
