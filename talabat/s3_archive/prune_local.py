"""Delete local files ONLY where S3 holds a verified, identical copy.

This is the only script here that destroys anything, so every gate is on by
default and deletion needs --execute:

  * the file must be action=archive in the inventory. Never archive_keep
    (vocabularies, caches that cost credits, the current deliverable) and never
    skip (code).
  * it must appear in data/verify_report.json's verified list, from a run of
    verify_s3.py NEWER than the upload manifest.
  * its size and sha256 on disk must still match what was uploaded, re-checked
    here unless --trust-verify.
  * 'covered' files (the loose logo folders inside an uploaded zip) are deleted
    only when their cover object is itself verified.

Every deletion is written to data/deleted_index.csv - path, size, sha256 and
the s3:// URI it now lives at - so anything deleted can still be found.

    python prune_local.py                       # plan only
    python prune_local.py --execute             # delete verified archive files
    python prune_local.py --layer bronze --execute
"""
import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
TALABAT = HERE.parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
from layout import BUCKET  # noqa: E402
from upload_to_s3 import human, sha256_of  # noqa: E402

INVENTORY = HERE / "data" / "inventory.csv"
MANIFEST = HERE / "data" / "upload_manifest.jsonl"
STAGED = HERE / "data" / "staged_manifest.jsonl"
REPORT = HERE / "data" / "verify_report.json"
DELETED = HERE / "data" / "deleted_index.csv"


def covers_for(verified_keys):
    """month -> True when that month's logo zip is verified, so the loose
    logo folders it contains may go."""
    out = set()
    for k in verified_keys:
        if "/gold/" in k and "/logos/" in k and k.endswith(".zip"):
            out.add(k.split("/gold/")[1].split("/")[0])
    return out


def main(args):
    if not REPORT.exists():
        raise SystemExit("run verify_s3.py first - nothing is deleted without a verification report")
    newest = max((p.stat().st_mtime for p in (MANIFEST, STAGED) if p.exists()), default=0)
    if REPORT.stat().st_mtime < newest:
        raise SystemExit("data/verify_report.json is older than the manifest - re-run verify_s3.py")
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    print(f"  verification: {report.get('mode', 'unknown')}  ({report.get('checked_at', '?')})")
    if report.get("problems"):
        print(f"  verify_report.json lists {len(report['problems'])} problems; "
              f"only files it marked ok can be pruned.")
    verified = set(report["verified_paths"])
    up = {}
    for path in (STAGED, MANIFEST):        # browser-staged first, a real upload wins
        if not path.exists():
            continue
        for line in open(path, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                up[r["rel_path"]] = r
    inv = list(csv.DictReader(open(INVENTORY, encoding="utf-8-sig")))
    months_covered = covers_for({up[p]["s3_key"] for p in verified if p in up})

    plan, held = [], Counter()
    for r in inv:
        rel, action = r["rel_path"], r["action"]
        if args.layer and r["layer"] != args.layer:
            continue
        if args.month and r["month"] != args.month:
            continue
        path = TALABAT / rel
        if not path.exists():
            continue
        if action == "covered":
            if r["month"] in months_covered:
                plan.append((r, None))
            else:
                held["covered, but its zip is not verified"] += 1
            continue
        if action != "archive":
            held[f"not deletable by policy ({action})"] += 1
            continue
        if rel not in verified or rel not in up:
            held["not verified in S3"] += 1
            continue
        rec = up[rel]
        if path.stat().st_size != rec["bytes"]:
            held["changed on disk since upload"] += 1
            continue
        plan.append((r, rec))

    total = sum(int(r["bytes"]) for r, _ in plan)
    print("=" * 78)
    print(f"  PRUNE LOCAL   {'EXECUTE - files will be deleted' if args.execute else 'DRY RUN - nothing is deleted'}")
    print("=" * 78)
    print(f"  deletable : {len(plan):,} files, {human(total)}")
    for k, v in held.most_common():
        print(f"  held back : {v:>7,}  {k}")
    by_layer = Counter()
    for r, _ in plan:
        by_layer[r["layer"] or "covered"] += int(r["bytes"])
    for l, b in by_layer.most_common():
        print(f"     {l:12} {human(b):>12}")
    if not args.execute:
        print("\n  --execute to delete. Nothing was touched.")
        return 0

    new_index = not DELETED.exists()
    deleted = freed = 0
    with open(DELETED, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new_index:
            w.writerow(["rel_path", "bytes", "sha256", "s3_uri", "layer", "stage", "month", "deleted_at"])
        for r, rec in plan:
            path = TALABAT / r["rel_path"]
            if rec and not args.trust_verify:
                if sha256_of(path) != rec["sha256"]:
                    held["hash changed at the last moment"] += 1
                    continue
            size = path.stat().st_size
            try:
                path.unlink()
            except OSError as exc:
                print(f"    could not delete {r['rel_path']}: {exc}")
                continue
            deleted += 1
            freed += size
            w.writerow([r["rel_path"], size, (rec or {}).get("sha256", ""),
                        f"s3://{BUCKET}/{(rec or {}).get('s3_key', '') or 'inside ' + r['month'] + ' logos zip'}",
                        r["layer"], r["stage"], r["month"], time.strftime("%Y-%m-%dT%H:%M:%S")])
            if deleted % 200 == 0:
                print(f"    deleted {deleted:,} | freed {human(freed)}", flush=True)
    # leave no empty folders behind
    removed_dirs = 0
    for d in sorted((p for p in TALABAT.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        try:
            if not any(d.iterdir()):
                d.rmdir()
                removed_dirs += 1
        except OSError:
            pass
    print(f"\n  deleted {deleted:,} files | freed {human(freed)} | empty folders removed {removed_dirs}")
    print(f"  index -> data/{DELETED.name}  (every file's s3:// location)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true", help="actually delete (default is a dry run)")
    p.add_argument("--layer", choices=["bronze", "silver", "gold", "reference", "ops"])
    p.add_argument("--month")
    p.add_argument("--trust-verify", action="store_true",
                   help="skip the last-moment re-hash (faster, slightly weaker)")
    sys.exit(main(p.parse_args()))
