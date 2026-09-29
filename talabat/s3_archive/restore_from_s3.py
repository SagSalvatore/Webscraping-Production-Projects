"""Bring archived files back to disk, to the exact path they came from.

The point of the archive is that deleting locally is safe, which is only true
if getting a file back is one command. Sources, in order: the upload manifest
(everything uploaded) and data/deleted_index.csv (what was actually removed).

    python restore_from_s3.py --path unified/data/talabat_unified_202608.jsonl
    python restore_from_s3.py --prefix menu_refresh/data/refresh_202609/ --execute
    python restore_from_s3.py --month 2026-08 --layer gold --execute
    python restore_from_s3.py --list --month 2026-07        # just show what exists

A restore never overwrites a file that is already on disk unless --overwrite.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TALABAT = HERE.parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
from layout import BUCKET  # noqa: E402
from upload_to_s3 import client, human, sha256_of  # noqa: E402

MANIFEST = HERE / "data" / "upload_manifest.jsonl"


def main(args):
    rows = {}
    for line in open(MANIFEST, encoding="utf-8") if MANIFEST.exists() else []:
        if line.strip():
            r = json.loads(line)
            rows[r["rel_path"]] = r
    sel = list(rows.values())
    if args.path:
        sel = [r for r in sel if r["rel_path"] == args.path]
    if args.prefix:
        sel = [r for r in sel if r["rel_path"].startswith(args.prefix)]
    if args.month:
        sel = [r for r in sel if r["month"] == args.month]
    if args.layer:
        sel = [r for r in sel if r["layer"] == args.layer]
    if not sel:
        print("  nothing in the manifest matches that selection")
        return 1
    total = sum(r["bytes"] for r in sel)
    print(f"  {len(sel):,} objects, {human(total)}")
    for r in sel[:20]:
        on_disk = (TALABAT / r["rel_path"]).exists()
        print(f"    {human(r['bytes']):>10}  {'on disk' if on_disk else 'archived'}  {r['rel_path'][:70]}")
    if len(sel) > 20:
        print(f"    ... and {len(sel) - 20:,} more")
    if args.list or not args.execute:
        print("\n  --execute to download." if not args.list else "")
        return 0

    s3 = client()
    got = skipped = 0
    for r in sel:
        dest = TALABAT / r["rel_path"]
        if dest.exists() and not args.overwrite:
            skipped += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(BUCKET, r["s3_key"], str(dest))
        if sha256_of(dest) != r["sha256"]:
            print(f"    CHECKSUM MISMATCH after download: {r['rel_path']}")
            return 1
        got += 1
        if got % 25 == 0:
            print(f"    restored {got:,}/{len(sel):,}", flush=True)
    print(f"\n  restored {got:,} files | already on disk {skipped:,} | every checksum matched")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--path", help="one exact relative path")
    p.add_argument("--prefix", help="everything under this relative path")
    p.add_argument("--month")
    p.add_argument("--layer", choices=["bronze", "silver", "gold", "reference", "ops"])
    p.add_argument("--list", action="store_true", help="only show what is archived")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    sys.exit(main(p.parse_args()))
