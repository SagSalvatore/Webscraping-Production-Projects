"""Split one cohort's logos out of the shared library into their own folder.

The downloader accumulates every month into data/downloaded/{source_id}/, which
is right for a growing internal library but wrong for a handover: August's 7,129
logos sit mixed with July's 15,193 and nothing on disk says which is which.

This MOVES (not copies) the cohort's folders into data/{cohort}_logos/ and
rewrites the manifest's local_path to match. Move, because C: is at 17.3 GB and
a copy would duplicate the payload for no reason - and because a logo belongs to
exactly one cohort, so leaving it in both places invites the two to drift.

Membership comes from the cohort MANIFEST, not from a directory listing: the
manifest is the record of what this cohort actually fetched, and source_ids do
not collide across cohorts (verified: 0 of August's 7,133 appear in July's
15,198), so nothing of July's can be caught by accident.

    python separate_cohort_logos.py --cohort august --dry-run
    python separate_cohort_logos.py --cohort august
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    manifest = DATA / f"{args.cohort}_logos_manifest.jsonl"
    src_root = DATA / "downloaded"
    dst_root = DATA / f"{args.cohort}_logos"

    if not manifest.exists():
        print(f"  {manifest.name} not found")
        return 1

    rows = [json.loads(l) for l in open(manifest, encoding="utf-8") if l.strip()]
    with_logo = [r for r in rows if r.get("logo")]
    print("=" * 70)
    print(f"  SEPARATE '{args.cohort}' LOGOS")
    print("=" * 70)
    print(f"  manifest rows      : {len(rows):,}")
    print(f"  with a logo        : {len(with_logo):,}")
    print(f"  no logo (skipped)  : {len(rows) - len(with_logo):,}")
    print(f"  from : {src_root}")
    print(f"  to   : {dst_root}")

    present = missing = already = 0
    bytes_moved = 0
    plan = []
    for r in with_logo:
        sid = str(r["source_id"])
        s, d = src_root / sid, dst_root / sid
        if d.exists():
            already += 1
            continue
        if not s.exists():
            missing += 1
            continue
        present += 1
        bytes_moved += sum(f.stat().st_size for f in s.rglob("*") if f.is_file())
        plan.append((sid, s, d, r))

    print(f"\n  to move            : {present:,}  ({bytes_moved/1e6:.1f} MB)")
    print(f"  already separated  : {already:,}")
    print(f"  missing on disk    : {missing:,}")

    if args.dry_run:
        print("\n  --dry-run: nothing moved")
        return 0

    dst_root.mkdir(exist_ok=True)
    moved = 0
    for sid, s, d, r in plan:
        shutil.move(str(s), str(d))
        moved += 1
        if moved % 1000 == 0:
            print(f"    moved {moved:,}/{len(plan):,}", flush=True)

    # the manifest must point at where the files now live, or it is a map to
    # nothing the moment anyone tries to use it
    fixed = 0
    for r in rows:
        lg = r.get("logo")
        if not lg or not lg.get("local_path"):
            continue
        p = lg["local_path"].replace("\\", "/")
        marker = "data/downloaded/"
        if marker in p:
            lg["local_path"] = p.replace(marker, f"data/{args.cohort}_logos/")
            fixed += 1
    tmp = manifest.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(manifest)

    files = list(dst_root.rglob("*"))
    n_files = sum(1 for f in files if f.is_file())
    size = sum(f.stat().st_size for f in files if f.is_file())
    print(f"\n  moved {moved:,} folders | manifest paths rewritten: {fixed:,}")
    print(f"  {dst_root.name}: {n_files:,} files, {size/1e6:.1f} MB")
    left = sum(1 for _ in src_root.iterdir()) if src_root.exists() else 0
    print(f"  {src_root.name} still holds {left:,} folders (other cohorts)")

    # prove every manifest path resolves
    bad = [r["source_id"] for r in rows
           if r.get("logo") and not (HERE / r["logo"]["local_path"]).exists()]
    print(f"  manifest paths that do not resolve: {len(bad)}")
    if bad:
        print(f"    e.g. {bad[:5]}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", default="august")
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
