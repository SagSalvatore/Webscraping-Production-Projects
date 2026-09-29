"""Prove the S3 copy is real and identical BEFORE anything is deleted locally.

Three levels, cheapest first:
  head   (always)  the object exists, its size matches, and the sha256 we
                   recorded at upload is on the object's metadata
  local  (default) re-hash the file on disk and compare - catches a file that
                   changed after it was uploaded, which would otherwise be
                   deleted while S3 holds the older bytes
  deep   (--deep N) download N objects in full and hash the bytes that come
                   back - the only check that proves the payload, not the label

Writes data/verify_report.json. prune_local.py refuses to run without it, and
refuses to delete anything this run did not mark "ok".

    python verify_s3.py                 # head + local re-hash, every file
    python verify_s3.py --deep 25       # ... plus 25 full downloads
    python verify_s3.py --fast          # head only (no local re-hash)
"""
import argparse
import hashlib
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
TALABAT = HERE.parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
from layout import BUCKET  # noqa: E402
from upload_to_s3 import client, human, sha256_of  # noqa: E402

MANIFEST = HERE / "data" / "upload_manifest.jsonl"
STAGED = HERE / "data" / "staged_manifest.jsonl"
REPORT = HERE / "data" / "verify_report.json"


def main(args):
    # Both sources: what upload_to_s3.py sent, and what was staged for a
    # browser upload (stage_for_console_upload.py). Either way the check is the
    # same - the object in S3 must match the sha256 recorded here.
    sources = [p for p in (STAGED, MANIFEST) if p.exists()]
    if not sources:
        raise SystemExit("nothing to verify yet - run upload_to_s3.py --execute "
                         "or stage_for_console_upload.py --execute first")
    rows = {}
    for path in sources:                       # MANIFEST last: a real upload wins
        for line in open(path, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                rows[r["rel_path"]] = r        # last write wins (a re-upload)
    rows = list(rows.values())
    print(f"  sources: {', '.join(p.name for p in sources)}")
    s3 = client()
    st, problems, ok_paths = Counter(), [], []
    t0 = time.time()
    for i, r in enumerate(rows, 1):
        local = TALABAT / r["rel_path"]
        try:
            head = s3.head_object(Bucket=BUCKET, Key=r["s3_key"])
        except Exception as exc:
            problems.append((r["rel_path"], f"not in S3: {str(exc)[:80]}"))
            st["missing in S3"] += 1
            continue
        if head["ContentLength"] != r["bytes"]:
            problems.append((r["rel_path"], f"size {head['ContentLength']} != {r['bytes']}"))
            st["size mismatch"] += 1
            continue
        if head.get("Metadata", {}).get("sha256") != r["sha256"]:
            problems.append((r["rel_path"], "sha256 metadata differs"))
            st["sha256 metadata mismatch"] += 1
            continue
        if not local.exists():
            st["ok (local already gone)"] += 1
            ok_paths.append(r["rel_path"])
            continue
        if not args.fast:
            if local.stat().st_size != r["bytes"] or sha256_of(local) != r["sha256"]:
                problems.append((r["rel_path"], "local file changed since upload - re-upload it"))
                st["local changed since upload"] += 1
                continue
        st["ok"] += 1
        ok_paths.append(r["rel_path"])
        if i % 100 == 0 or i == len(rows):
            print(f"    checked {i:,}/{len(rows):,} | ok {st['ok']:,} | problems {len(problems)}", flush=True)

    deep_ok = deep_bad = 0
    if args.deep:
        pool = [r for r in rows if r["rel_path"] in set(ok_paths)]
        for r in random.Random(7).sample(pool, min(args.deep, len(pool))):
            h = hashlib.sha256()
            body = s3.get_object(Bucket=BUCKET, Key=r["s3_key"])["Body"]
            for chunk in iter(lambda: body.read(8 * 1024 * 1024), b""):
                h.update(chunk)
            if h.hexdigest() == r["sha256"]:
                deep_ok += 1
            else:
                deep_bad += 1
                problems.append((r["rel_path"], "DEEP: downloaded bytes hash differently"))
                if r["rel_path"] in ok_paths:
                    ok_paths.remove(r["rel_path"])

    print("=" * 78)
    print(f"  VERIFY s3://{BUCKET}   {len(rows):,} objects in {time.time() - t0:.0f}s")
    print("=" * 78)
    for k, v in st.most_common():
        print(f"    {k:34} {v:>7,}")
    if args.deep:
        print(f"    deep download check                 {deep_ok:>7,} ok, {deep_bad} bad")
    print(f"    bytes verified                 {human(sum(r['bytes'] for r in rows if r['rel_path'] in set(ok_paths))):>12}")
    for p in problems[:15]:
        print(f"    PROBLEM {p[0][:60]}  {p[1]}")
    REPORT.write_text(json.dumps({
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "bucket": BUCKET,
        "mode": "head-only" if args.fast else "head+local-rehash",
        "deep_checked": args.deep, "deep_ok": deep_ok, "deep_bad": deep_bad,
        "objects": len(rows), "ok": len(ok_paths), "problems": problems,
        "verified_paths": sorted(ok_paths),
    }, indent=2), encoding="utf-8")
    print(f"\n  -> data/{REPORT.name}" + ("   next: python prune_local.py" if not problems else
                                          "   FIX THE PROBLEMS FIRST - do not prune"))
    return 1 if problems else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--fast", action="store_true", help="skip the local re-hash (head check only)")
    p.add_argument("--deep", type=int, default=0, help="also download and hash this many objects")
    sys.exit(main(p.parse_args()))
