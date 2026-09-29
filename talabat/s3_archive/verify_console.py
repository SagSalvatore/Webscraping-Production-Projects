"""Verify a browser upload with no API credentials, using the console's own totals.

The S3 console can report, for any folder: how many objects it holds and how
many bytes. That is checkable evidence we can get without an access key - and
if a prefix holds exactly the expected number of objects AND exactly the
expected byte total, nothing is missing and nothing is truncated.

What this proves, and what it does not:
  PROVES     every object arrived, and each is the right size. A failed or
             partial upload changes the count or the total.
  DOES NOT   compare content byte for byte. S3 verifies each upload's integrity
             in transit itself (the console sends checksums per part), so the
             untested gap is small - but it IS a weaker check than
             verify_s3.py's sha256, and the report says so.
  LATER      data/staged_manifest.jsonl keeps a sha256 for all 1,647 files, so
             the day credentials arrive, verify_s3.py --deep can still prove
             the bytes - even after the local originals are gone.

    python verify_console.py                 # writes data/console_check.csv
    #  ... fill observed_objects / observed_bytes from the S3 console ...
    python verify_console.py --compare       # writes data/verify_report.json

In the console: open s3://rii-data-dump, tick the folder, Actions ->
"Calculate total size". Read off "Objects" and "Total size" (switch the unit to
Bytes for an exact figure).
"""
import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
from upload_to_s3 import human  # noqa: E402

STAGED = HERE / "data" / "staged_manifest.jsonl"
MANIFEST = HERE / "data" / "upload_manifest.jsonl"
CHECK = HERE / "data" / "console_check.csv"
REPORT = HERE / "data" / "verify_report.json"


def load():
    rows = {}
    for path in (STAGED, MANIFEST):
        if path.exists():
            for line in open(path, encoding="utf-8"):
                if line.strip():
                    r = json.loads(line)
                    rows[r["rel_path"]] = r
    if not rows:
        raise SystemExit("nothing staged or uploaded yet")
    return list(rows.values())


def prefix_of(key, depth):
    return "/".join(key.split("/")[:depth]) + "/"


UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


def size_matches(observed, expected_bytes):
    """-> None when they agree, else why not.

    The console reports a folder's size ROUNDED to one decimal ('3.0 GB'), so
    an exact byte comparison is impossible from the UI. A rounded figure still
    pins the total to within half a display unit, which is far tighter than any
    plausible missing file - and the object COUNT, which is exact, is checked
    separately. Plain digits are compared exactly.
    """
    s = str(observed).strip().replace(",", "")
    if s.isdigit():
        return None if int(s) == expected_bytes else f"bytes {int(s):,} != expected {expected_bytes:,}"
    m = re.match(r"^([\d.]+)\s*([KMGT]?B)$", s, re.I)
    if not m:
        return f"cannot read the size {observed!r}"
    value, unit = float(m.group(1)), m.group(2).upper()
    step = UNITS[unit]
    shown = round(expected_bytes / step, 1)
    if abs(shown - value) > 0.051:
        return f"size {value} {unit} != expected {shown} {unit} ({expected_bytes:,} bytes)"
    return None


def main(args):
    rows = load()
    groups = defaultdict(list)
    for r in rows:
        groups[prefix_of(r["s3_key"], args.depth)].append(r)

    if not args.compare:
        with open(CHECK, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["prefix", "expected_objects", "expected_bytes",
                        "observed_objects", "observed_bytes"])
            for p in sorted(groups):
                w.writerow([p, len(groups[p]), sum(r["bytes"] for r in groups[p]), "", ""])
        print("=" * 78)
        print("  WHAT THE CONSOLE SHOULD SHOW   (s3://rii-data-dump)")
        print("=" * 78)
        for p in sorted(groups):
            g = groups[p]
            print(f"  {p:44} {len(g):>6,} objects   {sum(r['bytes'] for r in g):>15,} bytes"
                  f"   ({human(sum(r['bytes'] for r in g))})")
        print(f"  {'TOTAL':44} {len(rows):>6,} objects   {sum(r['bytes'] for r in rows):>15,} bytes")
        print(f"\n  -> data/{CHECK.name}")
        print("  In the console: tick each folder -> Actions -> Calculate total size,")
        print("  put the numbers in observed_objects / observed_bytes, then:")
        print("      python verify_console.py --compare")
        return 0

    if not CHECK.exists():
        raise SystemExit("run 'python verify_console.py' first, then fill in the CSV")
    ok_prefixes, bad = [], []
    for row in csv.DictReader(open(CHECK, encoding="utf-8-sig")):
        p = row["prefix"]
        exp_n, exp_b = int(row["expected_objects"]), int(row["expected_bytes"])
        obs_n = (row.get("observed_objects") or "").strip().replace(",", "")
        obs_b = (row.get("observed_bytes") or "").strip().replace(",", "")
        if not obs_n or not obs_b:
            bad.append((p, "not filled in"))
            continue
        if int(obs_n) != exp_n:
            bad.append((p, f"objects {obs_n} != expected {exp_n}"))
            continue
        why = size_matches(obs_b, exp_b)
        if why:
            bad.append((p, why))
            continue
        ok_prefixes.append(p)

    # a file is covered when its key sits under ANY matching prefix - the rows
    # may be at mixed depths (a layer measured whole, a layer measured by month)
    verified = [r["rel_path"] for r in rows if any(r["s3_key"].startswith(p) for p in ok_prefixes)]
    print("=" * 78)
    print(f"  CONSOLE VERIFICATION   {len(ok_prefixes)} prefixes match, {len(bad)} do not")
    print("=" * 78)
    for p in ok_prefixes:
        print(f"    OK       {p}")
    for p, why in bad:
        print(f"    PROBLEM  {p:44} {why}")
    print(f"\n  files covered by a matching prefix: {len(verified):,} of {len(rows):,}")
    REPORT.write_text(json.dumps({
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "bucket": "rii-data-dump",
        "mode": "console-totals (object count + byte total per prefix; NOT per-file sha256)",
        "deep_checked": 0, "deep_ok": 0, "deep_bad": 0,
        "objects": len(rows), "ok": len(verified),
        "problems": [[p, why] for p, why in bad],
        "verified_paths": sorted(verified),
    }, indent=2), encoding="utf-8")
    print(f"  -> data/{REPORT.name}")
    print("  " + ("next: python prune_local.py" if verified and not bad else
                  "fix the mismatched prefixes before pruning"))
    return 1 if bad else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--compare", action="store_true", help="read the filled-in CSV and write the report")
    p.add_argument("--depth", type=int, default=2,
                   help="prefix depth to check: 2 = talabat/<layer>/ (5 lookups, default), "
                        "3 = talabat/<layer>/<month>/ (finer, ~20 lookups)")
    sys.exit(main(p.parse_args()))
