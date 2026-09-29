"""Fallback for when there are no API credentials: upload from the browser.

Builds a folder tree whose shape IS the S3 key layout, so dragging its top
`talabat` folder into the bucket root reproduces exactly the keys inventory.py
planned. Files are HARDLINKED, not copied, so a 15 GB tree costs ~0 extra disk
(same volume only; it falls back to a copy if a link is refused).

    python stage_for_console_upload.py                      # plan
    python stage_for_console_upload.py --execute
    python stage_for_console_upload.py --layer gold --execute
    python stage_for_console_upload.py --clean              # remove the tree

Then: S3 console -> rii-data-dump -> Upload -> Add folder -> pick
C:\\talabat_s3_staging\\talabat -> Upload.

It also writes checksums.csv (path, sha256, bytes) beside the tree. A browser
upload cannot be verified as it goes, so prune_local.py will NOT delete
anything staged this way until a later verify_s3.py run - with read access -
confirms the objects. Keep that in mind before deleting.
"""
import argparse
import csv
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TALABAT = HERE.parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
from upload_to_s3 import human, sha256_of  # noqa: E402

INVENTORY = HERE / "data" / "inventory.csv"
STAGING = Path(r"C:\talabat_s3_staging")


def main(args):
    if args.clean:
        if STAGING.exists():
            shutil.rmtree(STAGING)
            print(f"  removed {STAGING}")
        else:
            print("  nothing staged")
        return 0

    rows = [r for r in csv.DictReader(open(INVENTORY, encoding="utf-8-sig"))
            if r["action"].startswith("archive")]
    if args.layer:
        rows = [r for r in rows if r["layer"] == args.layer]
    if args.month:
        rows = [r for r in rows if r["month"] == args.month]
    total = sum(int(r["bytes"]) for r in rows)
    print("=" * 78)
    print(f"  STAGE FOR BROWSER UPLOAD   {'EXECUTE' if args.execute else 'DRY RUN'}")
    print("=" * 78)
    print(f"  {len(rows):,} files, {human(total)}  ->  {STAGING}")
    if not args.execute:
        print(f"  e.g. {rows[0]['s3_key']}")
        print("\n  --execute to build the tree (hardlinks, no extra disk used).")
        return 0

    linked = copied = failed = 0
    manifest = HERE / "data" / "staged_manifest.jsonl"
    with open(manifest, "w", encoding="utf-8") as mf:
        for i, r in enumerate(rows, 1):
            src = TALABAT / r["rel_path"]
            dst = STAGING / r["s3_key"]
            if not src.exists():
                failed += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst.unlink()
            try:
                os.link(src, dst)                  # same volume: no extra bytes
                linked += 1
            except OSError:
                shutil.copy2(src, dst)             # different volume
                copied += 1
            # The browser cannot tell us a checksum, so record it now. When
            # credentials arrive, verify_s3.py reads this and checks every
            # object that was uploaded by hand - the same gate prune_local.py
            # demands before it deletes anything.
            if not args.no_checksums:
                mf.write(json.dumps({
                    "rel_path": r["rel_path"], "s3_key": r["s3_key"], "bytes": int(r["bytes"]),
                    "sha256": sha256_of(src), "layer": r["layer"], "stage": r["stage"],
                    "month": r["month"], "action": r["action"],
                    "storage_class": "STANDARD", "uploaded_at": "", "status": "staged_console"}) + "\n")
            if i % 200 == 0 or i == len(rows):
                print(f"    staged {i:,}/{len(rows):,}", flush=True)
    print(f"\n  hardlinked {linked:,} | copied {copied:,} | missing {failed:,}")
    if not args.no_checksums:
        print(f"  checksums -> data/{manifest.name} (verify_s3.py reads this later)")
    print(f"  tree: {STAGING / 'talabat'}")
    print("  S3 console -> rii-data-dump -> Upload -> Add folder -> select that folder.")
    print("  When it finishes, run verify_s3.py (needs read access) before pruning anything.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true")
    p.add_argument("--clean", action="store_true", help="delete the staging tree")
    p.add_argument("--layer", choices=["bronze", "silver", "gold", "reference", "ops"])
    p.add_argument("--month")
    p.add_argument("--no-checksums", action="store_true",
                   help="skip hashing (faster, but the upload can never be verified)")
    sys.exit(main(p.parse_args()))
