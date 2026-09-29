"""Final pass: force every identical menu name onto one std_term and one
ingredient list, directly in the deliverable.

WHY A FILE PASS AND NOT ANOTHER REBUILD. resolve_collapsed_names.py grouped keys
by smart_title(clean_item_key(key)), but the builder writes
scrub(smart_title(...)) - scrub additionally strips quotes, stray separators and
invisible characters. So a handful of names that LOOK identical in the output
were distinct at grouping time and slipped through: `Classic French Fries " "`,
`Penne All'arrabbiata`, `S'mores Cookies`. 118 names, 630 rows - 0.05% of the
file. Rebuilding for that costs 20 minutes; a streamed patch costs one.

THE RULE. For each conflicting name, adopt the (std_term, ingredients) carried
by the MOST menu rows. That is the best-evidenced answer in the data itself, and
needs no external lookup - the alternative, picking first-seen or alphabetical,
would be arbitrary.

Two passes: count, then write. Only std_term and ingredients are touched.

    python enforce_name_consistency.py --dry-run
    python enforce_name_consistency.py
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ijson

HERE = Path(__file__).resolve().parent
FILE = HERE / "data" / "July_menu_update.json"
REPORT = HERE / "data" / "name_consistency_report.json"

sys.stdout.reconfigure(encoding="utf-8")


def sig_of(it):
    return (it.get("std_term"),
            tuple(it.get("ingredients") or []))


def main(args):
    print("=" * 74)
    print("  ENFORCE NAME CONSISTENCY")
    print("=" * 74)

    # pass 1 - which names disagree, and which value carries the most rows
    counts = defaultdict(Counter)
    with open(FILE, "rb") as f:
        for rec in ijson.items(f, "item", use_float=True):
            for it in rec.get("menu_items") or []:
                nm = it.get("name")
                if nm:
                    counts[nm][sig_of(it)] += 1

    conflicted = {n: c for n, c in counts.items() if len(c) > 1}
    winner = {n: c.most_common(1)[0][0] for n, c in conflicted.items()}
    rows_total = sum(sum(c.values()) for c in conflicted.values())
    rows_changed = sum(sum(v for s, v in c.items() if s != winner[n])
                       for n, c in conflicted.items())
    print(f"  distinct names        : {len(counts):,}")
    print(f"  names in conflict     : {len(conflicted):,}")
    print(f"  rows on those names   : {rows_total:,}")
    print(f"  rows that will change : {rows_changed:,}")
    print("\n  examples (winner keeps the most rows):")
    for n, c in sorted(conflicted.items(),
                       key=lambda t: -sum(t[1].values()))[:6]:
        w = winner[n]
        print(f"     {n[:34]:36} -> {str(w[0])[:22]:24} "
              f"({c[w]} rows) beats {len(c)-1} other(s)")
        for s, v in c.most_common()[1:3]:
            print(f"         losing: {str(s[0])[:22]:24} {v} rows")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    # pass 2 - write
    changed = 0
    tmp = FILE.with_suffix(".tmp")
    with open(FILE, "rb") as src, open(tmp, "w", encoding="utf-8") as out:
        out.write("[\n")
        first = True
        n = 0
        for rec in ijson.items(src, "item", use_float=True):
            for it in rec.get("menu_items") or []:
                nm = it.get("name")
                w = winner.get(nm)
                if w and sig_of(it) != w:
                    it["std_term"] = w[0]
                    it["ingredients"] = list(w[1])
                    changed += 1
            if not first:
                out.write(",\n")
            json.dump(rec, out, ensure_ascii=False, indent=2)
            first = False
            n += 1
            if n % 5000 == 0:
                print(f"    {n:,} ...", flush=True)
        out.write("\n]\n")
    tmp.replace(FILE)

    print(f"\n  rows changed: {changed:,}")
    REPORT.write_text(json.dumps({
        "names_in_conflict": len(conflicted),
        "rows_changed": changed,
    }, indent=2), encoding="utf-8")
    print(f"  -> {FILE.name} ({FILE.stat().st_size/1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
