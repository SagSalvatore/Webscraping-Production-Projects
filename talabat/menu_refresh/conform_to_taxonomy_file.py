"""Conform ingredients in BOTH deliverables to ingredients_export_02.json.

THE AUTHORITY IS THE JSON FILE, NOT POSTGRES. Measured:

    ingredients_export_02.json      820 terms, ALL lowercase
    July deliverable (shipped)      816 distinct terms, 100% inside it,
                                    all lowercase - 7,810,742 values

So that file is exactly what Tech is built against. The Postgres
`ingredients_taxonomy` table holds 982 terms in Title Case - a superset with a
different casing convention. Conforming against the DB was wrong on both counts.

TWO DEFECTS THIS FIXES:

  August_export.json    never conformed at all. 26,550 distinct values outside
                        the taxonomy across 967,580 occurrences - raw free text
                        from the NDJSON ("zinger chicken", "Chicken/Meat",
                        "touch of spicy honey", '"basmati rice').
  July_menu_update.json conformed against the DB, so every value came out Title
                        Case ("Brioche", "Butter") where July ships lowercase.
                        Right vocabulary, wrong casing.

RULE: exact (case-insensitive) -> plural/split/paren recovery -> DROP.
Output is always the taxonomy's own lowercase spelling. An item left with
nothing is refilled from its std_term class profile, itself filtered to the 820.

--files REPOINTS THIS AT ANY COHORT, which is what the monthly refresh needs:
new menu items come out of map_std_terms.py and must be conformed before they
can ship. Two shapes are handled, sniffed from CONTENT not extension - the
delivered exports are nested arrays (one record per restaurant, items beneath),
the mapper's output is flat JSON Lines where the row IS the item. See
split_ings() for why the ingredient VALUE also has two shapes.

    python conform_to_taxonomy_file.py --dry-run
    python conform_to_taxonomy_file.py
    python conform_to_taxonomy_file.py --files path/to/menu_items_with_std_terms.jsonl
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import ijson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TAXO = ROOT / "August_menu" / "ingredients_export_02.json"
PROFILES = HERE / "data" / "std_term_canonical_ingredients.json"
FILES = [
    ROOT / "August_menu" / "data" / "August_export.json",
    HERE / "data" / "July_menu_update.json",
]
DROPPED = HERE / "data" / "ingredients_dropped_vs_taxonomy_file.csv"

sys.stdout.reconfigure(encoding="utf-8")


def load_taxo():
    d = json.loads(TAXO.read_text(encoding="utf-8"))
    # the file is already lowercase; map lower -> its own spelling so the
    # output matches July byte for byte
    return {r["ingredient_name"].strip().lower(): r["ingredient_name"].strip()
            for r in d if r.get("ingredient_name")}


def recover(s, vocab):
    t = str(s).strip().lower()
    if t in vocab:
        return vocab[t]
    for cut in ("es", "s"):
        if t.endswith(cut) and t[: -len(cut)] in vocab:
            return vocab[t[: -len(cut)]]
    for part in re.split(r"[/&,]", t):
        p = part.strip()
        if p in vocab:
            return vocab[p]
        for cut in ("es", "s"):
            if p.endswith(cut) and p[: -len(cut)] in vocab:
                return vocab[p[: -len(cut)]]
    p = re.sub(r"\(.*?\)", "", t).strip(' "\'')
    return vocab.get(p)


def split_ings(v):
    """`ingredients` arrives in TWO shapes and only one of them is a list.

    The delivered exports store a list. map_std_terms.py does NOT: tier 0 copies
    the delivered list, but the exact and kNN tiers take the value straight from
    the Excel reference, where it is one comma-joined STRING
    ("Chicken, Garlic, Butter"). Iterating a str yields CHARACTERS - the same
    defect that once dropped 693,885 "values" whose commonest entries were
    'e', ',' and 'a'. Normalise the shape before anything iterates it.
    """
    if isinstance(v, list):
        return [str(p).strip() for p in v if str(p).strip()]
    return [p.strip() for p in str(v or "").replace(";", ",").split(",")
            if p.strip() and p.strip().lower() != "nan"]


def sniff(path):
    """Array or JSON Lines - decided by CONTENT, never by the extension.

    A flat mapper output and a nested export can both end in .json/.jsonl, and
    a .bak of a JSONL file parsed as an array is a silent way to read nothing.
    """
    with open(path, "rb") as f:
        return "json" if f.read(2048).lstrip()[:1] == b"[" else "jsonl"


def conform_item(it, vocab, profiles, stats, dropped_all):
    """Rewrite one item's ingredients onto the taxonomy. Shape-agnostic: it is
    given the dict that OWNS `ingredients`, whether that came from a nested
    export record or from a flat row that is itself the item."""
    keep, seen = [], set()
    for i in split_ings(it.get("ingredients")):
        c = recover(i, vocab)
        if c is None:
            stats["dropped"] += 1
            dropped_all[i.lower()] += 1
            continue
        stats["kept"] += 1
        if c not in seen:
            seen.add(c)
            keep.append(c)
    if not keep:
        keep = list(profiles.get(it.get("std_term")) or [])
        stats["refilled" if keep else "empty"] += 1
    it["ingredients"] = keep


def main(args):
    vocab = load_taxo()
    prof_raw = json.loads(PROFILES.read_text(encoding="utf-8"))
    profiles = {t: [vocab[i.lower()] for i in v if i.lower() in vocab]
                for t, v in prof_raw.items()}
    usable = sum(1 for v in profiles.values() if v)
    print("=" * 74)
    print(f"  CONFORM TO {TAXO.name}  ({len(vocab):,} terms, lowercase)")
    print("=" * 74)
    print(f"  std_term profiles usable after filtering: {usable:,}/{len(profiles):,}")

    dropped_all = Counter()
    files = ([Path(p) for p in args.files.split(",")] if args.files else FILES)
    for path in files:
        if not path.exists():
            print(f"\n  {path.name}: NOT FOUND - skipped")
            continue
        stats = Counter()
        shape = sniff(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        n = 0
        with open(path, "rb") as src, open(tmp, "w", encoding="utf-8") as out:
            if shape == "json":
                # NESTED EXPORT: one record per restaurant, items underneath.
                out.write("[\n")
                first = True
                for rec in ijson.items(src, "item", use_float=True):
                    for it in rec.get("menu_items") or []:
                        conform_item(it, vocab, profiles, stats, dropped_all)
                    if not first:
                        out.write(",\n")
                    json.dump(rec, out, ensure_ascii=False, indent=2)
                    first = False
                    n += 1
                    if n % 5000 == 0:
                        print(f"    {path.name}: {n:,} ...", flush=True)
                out.write("\n]\n")
            else:
                # FLAT JSONL: the row IS the item (map_std_terms.py output).
                for line in src:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    conform_item(rec, vocab, profiles, stats, dropped_all)
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
                    if n % 20000 == 0:
                        print(f"    {path.name}: {n:,} ...", flush=True)
        print(f"\n  [{shape}] {path.name}")

        tot = stats["kept"] + stats["dropped"]
        print(f"    records            {n:,}")
        print(f"    values processed   {tot:,}")
        print(f"    kept               {stats['kept']:,} "
              f"({stats['kept']/max(tot,1)*100:.2f}%)")
        print(f"    dropped            {stats['dropped']:,}")
        print(f"    items refilled     {stats['refilled']:,}")
        print(f"    items left empty   {stats['empty']:,}")
        if args.dry_run:
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(path)

    print("\n  top dropped values across both files:")
    for s, c in dropped_all.most_common(12):
        print(f"     {c:>8,}  {s[:56]}")

    if args.dry_run:
        print("\n  --dry-run: originals untouched")
        return 0

    # The audit CSV is named after what was conformed. A fixed name would let a
    # refresh run silently overwrite the delivered exports' own audit trail.
    dropped_path = (files[0].parent / "ingredients_dropped_vs_taxonomy_file.csv"
                    if args.files else DROPPED)
    with open(dropped_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("value,occurrences\n")
        for s, c in dropped_all.most_common():
            f.write('"%s",%d\n' % (s.replace('"', '""'), c))
    print(f"\n  -> {dropped_path}  (audit trail)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--files", help="comma-separated paths to conform instead of "
                                   "the two delivered exports; each file's shape "
                                   "(array vs JSON Lines) is sniffed from its "
                                   "content")
    sys.exit(main(p.parse_args()))
