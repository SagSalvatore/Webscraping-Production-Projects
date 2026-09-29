"""Package one separated cohort's logos for the Tech handover, and prove it.

Runs after separate_cohort_logos.py. Nothing is shipped on trust:
  - every manifest row with a logo must point at a file that exists
  - every file must open as an image (Pillow verify) - a saved HTML error page
    or a truncated download would otherwise ship as 'logo.jpg'
  - every cohort restaurant must be in the manifest exactly once

Output, beside the manifest:
  data/{cohort}_logos_manifest.csv        the manifest flattened for a spreadsheet
  data/{cohort}_logos_{tag}.zip           data/{cohort}_logos/** + both manifests

Zip paths mirror the manifest's local_path ('data/september_logos/753762/logo.jpg'),
so the manifest resolves from the zip root exactly as August's did.

    python package_cohort_logos.py --cohort september --tag 202609
"""
import argparse
import csv
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    manifest = DATA / f"{args.cohort}_logos_manifest.jsonl"
    targets = DATA / f"{args.cohort}_logo_targets.jsonl"
    rows = [json.loads(l) for l in open(manifest, encoding="utf-8") if l.strip()]
    want = [json.loads(l)["source_id"] for l in open(targets, encoding="utf-8") if l.strip()]
    st, bad, fmt = Counter(), [], Counter()

    ids = Counter(str(r["source_id"]) for r in rows)
    st["manifest rows"] = len(rows)
    st["duplicate source_ids in manifest"] = sum(1 for n in ids.values() if n > 1)
    st["targets missing from manifest"] = len(set(map(str, want)) - set(ids))
    files = []
    for r in rows:
        lg = r.get("logo")
        if not lg:
            st["no logo (" + (r.get("no_logo_reason") or "download failed") + ")"] += 1
            continue
        p = HERE / lg["local_path"]
        if not p.exists():
            bad.append((r["source_id"], "file missing", lg["local_path"]))
            continue
        try:
            with Image.open(p) as im:
                im.verify()
            with Image.open(p) as im:
                fmt[f"{im.format} {im.size[0]}x{im.size[1]}" if args.sizes else im.format] += 1
        except Exception as exc:
            bad.append((r["source_id"], f"not a readable image: {exc}", lg["local_path"]))
            continue
        st["logos verified"] += 1
        files.append((p, lg["local_path"].replace("\\", "/")))
    size = sum(p.stat().st_size for p, _ in files)

    out_csv = DATA / f"{args.cohort}_logos_manifest.csv"
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_id", "restaurant_name", "chain_id", "logo_url", "local_path", "no_logo_reason"])
        for r in rows:
            lg = r.get("logo") or {}
            w.writerow([r["source_id"], r.get("restaurant_name"), r.get("chain_id"), lg.get("url", ""),
                        lg.get("local_path", ""), r.get("no_logo_reason", "")])

    print("=" * 70)
    print(f"  PACKAGE '{args.cohort}' LOGOS")
    print("=" * 70)
    for k, v in st.items():
        print(f"    {k:42} {v:>6,}")
    print(f"    image formats: {dict(fmt)}")
    print(f"    payload: {len(files):,} files, {size / 1e6:.1f} MB")
    if bad:
        print(f"\n  PROBLEMS: {len(bad)}")
        for b in bad[:10]:
            print(f"    {b}")
    gate = bad or st["duplicate source_ids in manifest"] or st["targets missing from manifest"]
    if gate:
        print("\n  NOT PACKAGED - fix the problems above first")
        return 1
    if args.dry_run:
        print("\n  --dry-run: no zip written")
        return 0

    out_zip = DATA / f"{args.cohort}_logos_{args.tag}.zip"
    tmp = out_zip.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(manifest, manifest.name)
        z.write(out_csv, out_csv.name)
        for p, rel in files:
            z.write(p, rel)
    tmp.replace(out_zip)
    with zipfile.ZipFile(out_zip) as z:
        names = z.namelist()
        test = z.testzip()
    print(f"\n  -> {out_zip.name}  {out_zip.stat().st_size / 1e6:.1f} MB | {len(names):,} entries | "
          f"CRC check: {'OK' if test is None else 'FAILED at ' + test}")
    return 0 if test is None else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", required=True)
    p.add_argument("--tag", required=True, help="suffix for the zip name, e.g. 202609")
    p.add_argument("--sizes", action="store_true", help="tally image dimensions too")
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
