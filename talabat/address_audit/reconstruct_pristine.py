"""Rebuild a pristine, pre-repair copy of each deliverable.

WHY THIS IS NEEDED. patch() wrote a fixed backup name, so the second area repair
overwrote the first one's backup and the original copy left the disk. Every
change is still recorded, so the original is reconstructible - but it has to be
rebuilt deliberately and then proved, not assumed.

THE SHORTEST SAFE PATH. Rather than replay three edits backwards over the
current file, start from the closest real snapshot that still exists:

    <file>.pre_contactfix.bak   = the state AFTER the area smear fix and
                                  BEFORE the contact fix

That snapshot already holds the ORIGINAL contact_phone, maps_url, and the
original area for every branch the later fallback fix touched (it had not run
yet). Exactly one thing is off in it: the area on the branches repaired by the
smear fix. Reverting that single field from area_fix_applied.csv reproduces the
original file, with one revert map instead of a chain of three.

VERIFIED, not asserted. The rebuilt file is streamed against the snapshot it
came from: only location.area may differ, only on the expected branches, and
every reverted value must equal the audit's area_before.

Written as <file>.ORIGINAL.bak - never over an existing file.

    python reconstruct_pristine.py --dry-run
    python reconstruct_pristine.py
"""
import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ijson
import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from apply_area_fix import CLOSES, OPEN, SEPS, TARGETS, enc      # noqa: E402

DATA = HERE / "data"
AREA_AUDIT = DATA / "area_fix_applied.csv"
sys.stdout.reconfigure(encoding="utf-8")


def revert_map(dataset):
    """source_id -> the area it had BEFORE the smear fix, for this dataset."""
    out = {}
    with open(AREA_AUDIT, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["dataset"] == dataset:
                out[r["source_id"]] = r["area_before"]
    return out


def rebuild(snapshot, out_path, shape, style, revert, dry):
    n, reverted = 0, 0
    f = None if dry else open(out_path, "wb")
    try:
        if f and OPEN[style]:
            f.write(OPEN[style])
        first = True
        with open(snapshot, "rb") as src:
            it = (ijson.items(src, "item", use_float=True) if shape == "json"
                  else (orjson.loads(l) for l in src if l.strip()))
            for rec in it:
                n += 1
                sid = str(rec.get("source_id"))
                if sid in revert and not rec.get("is_verified_location"):
                    old = revert[sid]
                    # the audit stores "" for a value that was null
                    rec["location"]["area"] = old if old != "" else None
                    reverted += 1
                if f:
                    if style == "jsonl":
                        f.write(enc(rec, style) + b"\n")
                    else:
                        if not first:
                            f.write(SEPS[style])
                        f.write(enc(rec, style))
                first = False
        if f and CLOSES[style]:
            f.write(CLOSES[style])
    finally:
        if f:
            f.close()
    return n, reverted


def verify(snapshot, rebuilt, shape, revert):
    """Only location.area may differ, only on expected ids, and each reverted
    value must equal the audit's area_before."""
    bad = Counter()
    with open(snapshot, "rb") as fa, open(rebuilt, "rb") as fb:
        ia = (ijson.items(fa, "item", use_float=True) if shape == "json"
              else (orjson.loads(l) for l in fa if l.strip()))
        ib = (ijson.items(fb, "item", use_float=True) if shape == "json"
              else (orjson.loads(l) for l in fb if l.strip()))
        for a, b in zip(ia, ib):
            if a.get("source_id") != b.get("source_id"):
                bad["record order changed"] += 1
                break
            la, lb = dict(a.get("location") or {}), dict(b.get("location") or {})
            aa, ab = la.pop("area", None), lb.pop("area", None)
            if la != lb:
                bad["another location field changed"] += 1
            a2, b2 = dict(a), dict(b)
            a2.pop("location"), b2.pop("location")
            if orjson.dumps(a2, option=orjson.OPT_SORT_KEYS) != \
               orjson.dumps(b2, option=orjson.OPT_SORT_KEYS):
                bad["a non-location field changed"] += 1
            sid = str(b.get("source_id"))
            if aa != ab:
                if sid not in revert:
                    bad["area changed on an UNEXPECTED record"] += 1
                else:
                    want = revert[sid] if revert[sid] != "" else None
                    if ab != want:
                        bad["reverted value does not match the audit"] += 1
    return bad


def main(args):
    print("=" * 78)
    print("  RECONSTRUCT the pristine pre-repair copies")
    print("=" * 78)
    for name, path, shape, style in TARGETS:
        snap = path.with_suffix(path.suffix + ".pre_contactfix.bak")
        out = path.with_suffix(path.suffix + ".ORIGINAL.bak")
        if not snap.exists():
            print(f"  {name:16} no .pre_contactfix.bak - cannot rebuild")
            continue
        if out.exists() and not args.dry_run:
            print(f"  {name:16} {out.name} already exists - left alone")
            continue
        rev = revert_map(name)
        n, rv = rebuild(snap, out, shape, style, rev, args.dry_run)
        if args.dry_run:
            print(f"  {name:16} {n:>7,} records | would revert {rv:>5,} areas "
                  f"-> {out.name}")
            continue
        bad = verify(snap, out, shape, rev)
        ok = (not bad) or set(bad) == set()
        print(f"  {name:16} {n:>7,} records | reverted {rv:>5,} areas | "
              f"verify {'PASS' if ok else 'FAIL ' + str(dict(bad))}")
        if not ok:
            out.unlink(missing_ok=True)
            raise SystemExit(f"{name}: reconstruction failed - output deleted")
        print(f"                   -> {out.name} "
              f"({out.stat().st_size/1e6:.0f} MB)")
    if args.dry_run:
        print("\n  --dry-run: nothing written")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
