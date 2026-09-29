"""Remove Talabat's internal POS/plugin test records from the August deliverable.

These are not restaurants. Found while investigating why 4 August entries had no
logo - a test record has none because nobody ever uploaded one:

    785344  Test Order Plugin   5 items   url: .../dont-touch-tmart-nasserya-central
    794787  Pos Test 20         1 item    url: .../tmart-majaz-central
    792322  Pos Test 11         1 item    url: .../pos-test-11
    794784  Pos Test 21         1 item    url: .../tmart-soyouh-central

DELIBERATELY NOT REMOVED: "Felfela Test" (71 items), "Afghan Test Food" (55),
"Test Time" (29). Those look like real businesses whose names contain the word
"test" - Felfela is a known Egyptian chain. Deleting a real restaurant to tidy a
name pattern is the worse error, so they stay unless Sagar says otherwise.

Cleans three places, so nothing is left pointing at a record that no longer
exists: the export, the logo manifest, and the logo file on disk.

    python remove_test_records.py --dry-run
    python remove_test_records.py
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXPORT = HERE / "data" / "August_export.json"
MANIFEST = ROOT / "images" / "data" / "august_logos_manifest.jsonl"
LOGO_DIR = ROOT / "images" / "data" / "august_logos"

# id -> name, kept explicit so the list is auditable rather than a regex that
# might quietly widen and take a real restaurant with it
REMOVE = {
    "785344": "Test Order Plugin",
    "794787": "Pos Test 20",
    "792322": "Pos Test 11",
    "794784": "Pos Test 21",
}

sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    print("=" * 70)
    print(f"  REMOVE {len(REMOVE)} POS/PLUGIN TEST RECORDS")
    print("=" * 70)

    d = json.loads(EXPORT.read_text(encoding="utf-8"))
    n0 = len(d)
    base0 = sum(1 for r in d if not r.get("is_verified_location"))

    hit = [r for r in d if str(r.get("source_id")) in REMOVE]
    for r in hit:
        kind = "VERIFIED" if r.get("is_verified_location") else "base"
        print(f"  {r['source_id']:>9}  {str(r.get('name'))[:32]:34} "
              f"{kind:9} items={len(r.get('menu_items') or []):>4}")
    if not hit:
        print("  none present - already removed")

    out = [r for r in d if str(r.get("source_id")) not in REMOVE]
    base1 = sum(1 for r in out if not r.get("is_verified_location"))
    items0 = sum(len(r.get("menu_items") or []) for r in d)
    items1 = sum(len(r.get("menu_items") or []) for r in out)
    print(f"\n  records      {n0:,} -> {len(out):,}   ({n0-len(out)} removed)")
    print(f"  base         {base0:,} -> {base1:,}")
    print(f"  menu entries {items0:,} -> {items1:,}   ({items0-items1} removed)")

    # logo manifest + files
    man = []
    man_hit = 0
    if MANIFEST.exists():
        man = [json.loads(l) for l in open(MANIFEST, encoding="utf-8") if l.strip()]
        man_hit = sum(1 for r in man if str(r.get("source_id")) in REMOVE)
    dirs = [LOGO_DIR / sid for sid in REMOVE if (LOGO_DIR / sid).exists()]
    print(f"  logo manifest rows to drop : {man_hit}")
    print(f"  logo folders on disk to del: {len(dirs)}  {[d.name for d in dirs]}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    assert base1 == base0 - len(REMOVE), "unexpected number of base records removed"

    tmp = EXPORT.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    tmp.replace(EXPORT)
    print(f"\n  -> {EXPORT.name} ({EXPORT.stat().st_size/1e6:.0f} MB)")

    if man:
        keep = [r for r in man if str(r.get("source_id")) not in REMOVE]
        t2 = MANIFEST.with_suffix(".tmp")
        with open(t2, "w", encoding="utf-8") as f:
            for r in keep:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        t2.replace(MANIFEST)
        print(f"  -> {MANIFEST.name} ({len(keep):,} rows)")

    for p in dirs:
        shutil.rmtree(p)
    if dirs:
        print(f"  deleted {len(dirs)} logo folder(s)")

    left = [r for r in out if str(r.get("source_id")) in REMOVE]
    print(f"\n  test records remaining in the export: {len(left)}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
