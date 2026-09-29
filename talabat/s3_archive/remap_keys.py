"""Point the manifest at where objects REALLY are, when an upload landed elsewhere.

The S3 console appends the selected folder's name to wherever you are standing,
so adding `bronze` while inside `talabat/bronze/` puts everything at
`talabat/bronze/bronze/...`. The bytes are fine; only our record of the key is
wrong - and a wrong key means verify_s3.py reports "not in S3" and
restore_from_s3.py cannot find the file.

This rewrites the s3_key of matching rows in data/staged_manifest.jsonl (and
upload_manifest.jsonl if present), after backing each one up. It touches no
data, in S3 or on disk.

    python remap_keys.py --from talabat/bronze/ --to talabat/bronze/bronze/
    python remap_keys.py --from talabat/bronze/ --to talabat/bronze/bronze/ --execute
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8")
FILES = [HERE / "data" / "staged_manifest.jsonl", HERE / "data" / "upload_manifest.jsonl"]


def main(args):
    print("=" * 78)
    print(f"  REMAP KEYS   {args.src}  ->  {args.dst}   "
          f"{'EXECUTE' if args.execute else 'DRY RUN'}")
    print("=" * 78)
    total = 0
    for path in FILES:
        if not path.exists():
            continue
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        hit = [r for r in rows if r["s3_key"].startswith(args.src)]
        print(f"  {path.name:26} {len(hit):>6,} of {len(rows):,} rows match")
        for r in hit[:2]:
            print(f"      {r['s3_key']}\n   -> {args.dst + r['s3_key'][len(args.src):]}")
        if not args.execute or not hit:
            total += len(hit)
            continue
        shutil.copy2(path, path.with_suffix(f".jsonl.bak_{time.strftime('%Y%m%d_%H%M%S')}"))
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                if r["s3_key"].startswith(args.src):
                    r["s3_key"] = args.dst + r["s3_key"][len(args.src):]
                f.write(json.dumps(r) + "\n")
        total += len(hit)
    print(f"\n  {total:,} keys {'rewritten (originals backed up)' if args.execute else 'would be rewritten'}")
    if args.execute:
        print("  next: python verify_console.py --depth 3   (expectations now match S3)")
    else:
        print("  --execute to apply.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="src", required=True, help="key prefix as recorded now")
    p.add_argument("--to", dest="dst", required=True, help="key prefix as it really is in S3")
    p.add_argument("--execute", action="store_true")
    sys.exit(main(p.parse_args()))
