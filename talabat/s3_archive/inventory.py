"""Scan talabat/ once and say, for every file, where it goes in S3.

Nothing is uploaded and nothing is deleted - this only writes the plan that
upload_to_s3.py and prune_local.py then follow, so the plan can be read and
argued with first.

  data/inventory.csv        one row per file: size, layer, stage, month, action, s3_key
  data/inventory_summary.json   the same totals this prints

Read the 'review' section of the output first: those are files no rule claims,
and nothing is ever uploaded or deleted from that list.

    python inventory.py
    python inventory.py --top 40          # more of the biggest files
    python inventory.py --root <path>     # scan somewhere else
"""
import argparse
import csv
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
from layout import BUCKET, SKIP_DIRS, classify, s3_key  # noqa: E402

TALABAT = HERE.parent
OUT_DIR = HERE / "data"


def walk(root):
    """Every file under root, skipping build junk. os.scandir, not glob - this
    tree sits in OneDrive and a naive walk takes minutes."""
    stack = [Path(root)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if e.name not in SKIP_DIRS:
                                stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False):
                            yield Path(e.path), e.stat()
                    except OSError:
                        continue
        except (PermissionError, OSError):
            continue


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:,.1f} {u}" if u != "B" else f"{n:,} B"
        n /= 1024


def main(args):
    root = Path(args.root).resolve() if args.root else TALABAT
    t0 = time.time()
    rows = []
    by_action, by_layer, by_folder = Counter(), Counter(), Counter()
    size_action, size_layer, size_stage, size_folder = Counter(), Counter(), Counter(), Counter()
    for path, st in walk(root):
        rel = path.relative_to(root).as_posix()
        layer, stage, month, action, reason = classify(rel, st.st_size)
        key = s3_key(rel, layer, stage, month) if layer else ""
        top = rel.split("/")[0] if "/" in rel else "(root files)"
        rows.append({"rel_path": rel, "bytes": st.st_size,
                     "modified": time.strftime("%Y-%m-%d", time.localtime(st.st_mtime)),
                     "top_folder": top, "layer": layer or "", "stage": stage or "",
                     "month": month or "", "action": action, "s3_key": key, "reason": reason})
        by_action[action] += 1
        size_action[action] += st.st_size
        if layer:
            by_layer[layer] += 1
            size_layer[layer] += st.st_size
            size_stage[(layer, stage)] += st.st_size
        by_folder[top] += 1
        size_folder[top] += st.st_size

    OUT_DIR.mkdir(exist_ok=True)
    with open(OUT_DIR / "inventory.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: -r["bytes"]))

    total = sum(r["bytes"] for r in rows)
    up = [r for r in rows if r["action"].startswith("archive")]
    free = [r for r in rows if r["action"] in ("archive", "covered")]
    print("=" * 78)
    print(f"  TALABAT INVENTORY   {len(rows):,} files | {human(total)} | scanned in {time.time() - t0:.0f}s")
    print(f"  bucket s3://{BUCKET}")
    print("=" * 78)
    print(f"\n  BY FOLDER ({len(by_folder)})")
    for folder, sz in sorted(size_folder.items(), key=lambda kv: -kv[1]):
        print(f"    {folder:28} {human(sz):>12}   {by_folder[folder]:>7,} files")
    print("\n  BY ACTION")
    for a, n in by_action.most_common():
        print(f"    {a:14} {human(size_action[a]):>12}   {n:>7,} files")
    print("\n  BY LAYER / STAGE (what would be uploaded)")
    for (layer, stage), sz in sorted(size_stage.items(), key=lambda kv: (kv[0][0], -kv[1])):
        print(f"    {layer:10} {stage:26} {human(sz):>12}")
    print(f"\n  UPLOAD  {len(up):,} files, {human(sum(r['bytes'] for r in up))}")
    print(f"  FREES   {len(free):,} files, {human(sum(r['bytes'] for r in free))} once verified in S3")
    print(f"\n  BIGGEST FILES")
    for r in sorted(rows, key=lambda r: -r["bytes"])[: args.top]:
        print(f"    {human(r['bytes']):>12}  {r['action']:13} {r['rel_path'][:78]}")
    review = [r for r in rows if r["action"] == "review"]
    print(f"\n  NEEDS A DECISION (no rule matched): {len(review):,} files, "
          f"{human(sum(r['bytes'] for r in review))}")
    seen = Counter()
    for r in sorted(review, key=lambda r: -r["bytes"]):
        d = str(Path(r["rel_path"]).parent)
        seen[d] += 1
        if seen[d] <= 3:
            print(f"    {human(r['bytes']):>12}  {r['rel_path'][:78]}")
    (OUT_DIR / "inventory_summary.json").write_text(json.dumps({
        "scanned_root": str(root), "files": len(rows), "bytes": total,
        "by_action": {a: {"files": by_action[a], "bytes": size_action[a]} for a in by_action},
        "by_layer": {l: {"files": by_layer[l], "bytes": size_layer[l]} for l in by_layer},
        "by_folder": {f: {"files": by_folder[f], "bytes": size_folder[f]} for f in by_folder},
        "upload_bytes": sum(r["bytes"] for r in up),
        "reclaimable_bytes": sum(r["bytes"] for r in free),
    }, indent=2), encoding="utf-8")
    print(f"\n  -> data/inventory.csv  |  data/inventory_summary.json")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root")
    p.add_argument("--top", type=int, default=25)
    sys.exit(main(p.parse_args()))
