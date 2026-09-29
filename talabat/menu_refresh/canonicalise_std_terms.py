"""Canonicalise std_term casing and re-derive taxonomy in a flat menu stage file.

WHY. Any stage file mapped before map_std_terms.py learned to canonicalise casing
carries ONE std_term under TWO spellings - the Excel tiers emit "grill", the
delivered vocabulary is "Grill". September's menu_items_final.jsonl had 83 such
twins over 65,787 rows (Hot & Cold Beverages 5,273 / hot & cold beverages 1,036).
The EXPORT never showed them - build_august_export.py smart_titles std_term on
the way out, and all 306 September export terms already match Tech's casing - but
the stage file is read directly by other steps, so it is fixed at the source.

TAXONOMY IS RE-DERIVED IN THE SAME PASS because it is a function of std_term and
the reference behind it changed: menu/final_std_terms_taxonomy.csv mapped the
std_term "Marketing/Non-Standard Menu" to taxonomy core_food, so flowers, candles
and gift sets counted as core food. Fixed to "marketing/non-standard menu"
(Sagar, Sept 2026). The export carries no taxonomy, so this never reached Tech.

ONLY `std_term` AND `taxonomy` CAN CHANGE, and that is proved, not assumed:
  * every other key must be identical per row
  * every row whose two fields did not change must re-serialise BYTE-IDENTICAL
    to its original line, so the rewrite cannot have reformatted anything
  * casing comes from canonical_casing in prior_item_labels.json - sourced from
    the delivered unified file - so a spelling can only be ALIGNED, never invented

The original is kept under a free backup slot (never overwritten), the output is
written to a temp file and swapped in, then re-read from disk and re-checked.

    python canonicalise_std_terms.py --file ../september/data/menus/menu_items_final.jsonl \
        --prior ../september/data/prior_item_labels.json --dry-run
"""
import argparse
import csv
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "August_menu"))
sys.path.insert(0, str(ROOT / "address_audit"))
from apply_area_fix import free_backup                               # noqa: E402
from map_std_terms import load_taxonomy, resolve_taxonomy            # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
FIELDS = ("std_term", "taxonomy")


def twins(terms):
    folded = defaultdict(set)
    for t in terms:
        if t:
            folded[str(t).lower()].add(t)
    return {k: v for k, v in folded.items() if len(v) > 1}


def transform(lines, canon, tax):
    """-> (new rows, stats, change pairs, refusals). Pure: writes nothing."""
    out, st, pairs, refuse = [], Counter(), Counter(), Counter()
    for raw in lines:
        r = orjson.loads(raw)
        t0, x0 = r.get("std_term"), r.get("taxonomy")
        if not t0:
            st["no std_term - left alone"] += 1
            out.append((raw, r))
            continue
        t1 = canon.get(str(t0).strip().lower(), t0)
        if str(t0).strip().lower() not in canon:
            st["std_term not in Tech vocabulary - casing left alone"] += 1
        x1 = resolve_taxonomy(t1, tax)
        if not x1:
            # never blank a taxonomy the row already had
            refuse["std_term resolves to NO taxonomy"] += 1
            x1 = x0
        if t1 != t0:
            st["std_term recased"] += 1
        if x1 != x0:
            st["taxonomy re-derived to a new value"] += 1
            pairs[(t1, x0, x1)] += 1
        n = dict(r)
        n["std_term"], n["taxonomy"] = t1, x1
        out.append((raw, n))
    return out, st, pairs, refuse


def check(out):
    """Gates on the transformed rows. Every value must be 0."""
    bad = Counter()
    for raw, n in out:
        o = orjson.loads(raw)
        if {k: v for k, v in o.items() if k not in FIELDS} != \
           {k: v for k, v in n.items() if k not in FIELDS}:
            bad["a field other than std_term/taxonomy changed"] += 1
        same = all(o.get(f) == n.get(f) for f in FIELDS)
        if same and orjson.dumps(n) + b"\n" != raw:
            bad["unchanged row not byte-identical"] += 1
    bad["std_term casing twins"] = len(twins(n.get("std_term") for _, n in out))
    return bad


def main(args):
    path = Path(args.file).resolve()
    print("=" * 76)
    print(f"  CANONICALISE std_term + RE-DERIVE taxonomy   {path.name}")
    print("=" * 76)
    first = path.read_bytes()[:1]
    if first != b"{":
        raise SystemExit("not a flat JSON Lines stage file - refusing")
    canon = orjson.loads(Path(args.prior).read_bytes())["canonical_casing"]
    tax, _ = load_taxonomy(set())
    lines = [l if l.endswith(b"\n") else l + b"\n"
             for l in path.read_bytes().splitlines(keepends=True) if l.strip()]
    before = twins(orjson.loads(l).get("std_term") for l in lines)
    print(f"  rows {len(lines):,} | Tech vocabulary {len(canon):,} terms")
    print(f"  casing twins BEFORE: {len(before)} "
          f"(rows {sum(1 for l in lines if str(orjson.loads(l).get('std_term')).lower() in before):,})")

    out, st, pairs, refuse = transform(lines, canon, tax)
    for k, v in sorted(st.items()):
        print(f"     {k:52} {v:>7,}")
    print("  taxonomy changes (std_term: before -> after):")
    for (t, a, b), c in pairs.most_common():
        print(f"     {c:>6,}  {t}: {a} -> {b}")
    bad = check(out)
    bad.update(refuse)
    print("  GATES (every one must be 0)")
    for k in ("a field other than std_term/taxonomy changed",
              "unchanged row not byte-identical", "std_term casing twins",
              "std_term resolves to NO taxonomy"):
        print(f"     {'PASS' if not bad[k] else 'FAIL'}  {k:48} {bad[k]:,}")
    if any(bad.values()):
        raise SystemExit("gates failed - nothing written")
    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    bak = free_backup(path, "pre_casingfix")
    shutil.copy2(path, bak)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        for _, n in out:
            f.write(orjson.dumps(n) + b"\n")
    tmp.replace(path)

    # ---- re-read from DISK and prove it against the backup ------------------
    disk = [l if l.endswith(b"\n") else l + b"\n"
            for l in path.read_bytes().splitlines(keepends=True) if l.strip()]
    orig = [l if l.endswith(b"\n") else l + b"\n"
            for l in bak.read_bytes().splitlines(keepends=True) if l.strip()]
    ok = len(disk) == len(orig)
    changed_lines = sum(a != b for a, b in zip(orig, disk))
    expect = sum(1 for (raw, n) in out
                 if any(orjson.loads(raw).get(f) != n.get(f) for f in FIELDS))
    disk_twins = len(twins(orjson.loads(l).get("std_term") for l in disk))
    print(f"\n  ON DISK: rows {len(disk):,} (backup {len(orig):,}) | lines changed "
          f"{changed_lines:,} (expected {expect:,}) | twins {disk_twins}")
    if not ok or changed_lines != expect or disk_twins:
        shutil.copy2(bak, path)
        raise SystemExit("on-disk verification FAILED - original restored from backup")

    audit = path.with_name(path.stem + ".casing_fix_applied.csv")
    agg = Counter()
    for raw, n in out:
        o = orjson.loads(raw)
        if any(o.get(f) != n.get(f) for f in FIELDS):
            agg[(o.get("std_term"), n["std_term"], o.get("taxonomy"), n["taxonomy"])] += 1
    with open(audit, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["std_term_before", "std_term_after", "taxonomy_before",
                    "taxonomy_after", "rows"])
        w.writerows([*k, v] for k, v in agg.most_common())
    print(f"  -> {path.name} rewritten | backup {bak.name} | audit {audit.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True, help="flat JSON Lines menu stage file")
    p.add_argument("--prior", required=True,
                   help="prior_item_labels.json carrying canonical_casing")
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
