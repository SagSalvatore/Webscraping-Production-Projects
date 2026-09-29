"""Force every ingredient in July_menu_update.json onto the controlled
vocabulary, matching what July actually shipped.

WHY. Measured on the July deliverable: 7,810,742 ingredient values, 100% of them
inside ingredients_taxonomy, zero free text, zero "discarded". That is not luck -
July read ingredients from menu_item_ingredients, which carries a foreign key to
the taxonomy, so an invalid term could never appear.

Our file builds from std_term_mapping_august.json instead, which has no such
constraint, and 100,625 values (15,709 distinct) fall outside the vocabulary:
"discarded" 12,645 times, plus free text like "eggs", "flour", "tea/coffee".
Shipping that changes the contract Tech is built against.

THREE STEPS, cheapest first:
  1. exact match against the vocabulary (case-insensitive) -> canonical casing
  2. light recovery for near misses: plural -> singular, "a/b" -> first side,
     parenthetical stripped. Only accepted if the result IS in the vocabulary.
  3. anything still unmatched is DROPPED - that is what July's FK did.
If an item empties out, its std_term class profile is used, which is built only
from vocabulary terms.

Streamed - the file is 545 MB.

    python conform_ingredients.py --dry-run
    python conform_ingredients.py
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import ijson
import psycopg2

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_PASSWORD  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FILE = DATA / "July_menu_update.json"
PROFILES = DATA / "std_term_canonical_ingredients.json"
DROPPED = DATA / "ingredients_dropped.csv"

DB = dict(host="localhost", port=5432, dbname="RestaurantIntelligence",
          user="postgres", password=PG_PASSWORD)

sys.stdout.reconfigure(encoding="utf-8")


def load_vocab():
    cn = psycopg2.connect(**DB)
    c = cn.cursor()
    c.execute("select ingredient_name from ingredients_taxonomy")
    v = {r[0].lower(): r[0] for r in c.fetchall()}
    cn.close()
    return v


def recover(s, vocab):
    """Only ever returns a value that IS in the vocabulary, or None."""
    t = s.strip().lower()
    if t in vocab:
        return vocab[t]
    # plural -> singular  ("eggs" -> "Egg")
    for cut in ("es", "s"):
        if t.endswith(cut) and t[: -len(cut)] in vocab:
            return vocab[t[: -len(cut)]]
    # "a/b" or "a & b" -> try each side
    for part in re.split(r"[/&,]", t):
        p = part.strip()
        if p and p in vocab:
            return vocab[p]
        for cut in ("es", "s"):
            if p.endswith(cut) and p[: -len(cut)] in vocab:
                return vocab[p[: -len(cut)]]
    # strip a parenthetical
    p = re.sub(r"\(.*?\)", "", t).strip()
    if p in vocab:
        return vocab[p]
    return None


def main(args):
    vocab = load_vocab()
    profiles = json.loads(PROFILES.read_text(encoding="utf-8"))
    print("=" * 72)
    print(f"  CONFORM INGREDIENTS to {len(vocab):,} controlled terms")
    print("=" * 72)

    stats = Counter()
    dropped = Counter()
    tmp = FILE.with_suffix(".tmp")
    n = 0

    with open(FILE, "rb") as src, open(tmp, "w", encoding="utf-8") as out:
        out.write("[\n")
        first = True
        for rec in ijson.items(src, "item", use_float=True):
            for it in rec.get("menu_items") or []:
                keep = []
                for i in it.get("ingredients") or []:
                    s = str(i).strip()
                    if not s:
                        continue
                    if s.lower() in vocab:
                        canon = vocab[s.lower()]
                        stats["exact"] += 1
                    else:
                        canon = recover(s, vocab)
                        if canon:
                            stats["recovered"] += 1
                        else:
                            stats["dropped"] += 1
                            dropped[s.lower()] += 1
                            continue
                    if canon not in keep:
                        keep.append(canon)
                if not keep:
                    prof = profiles.get(it.get("std_term")) or []
                    keep = [vocab[p.lower()] for p in prof if p.lower() in vocab]
                    if keep:
                        stats["refilled_from_profile"] += 1
                    else:
                        stats["left_empty"] += 1
                it["ingredients"] = keep
            if not first:
                out.write(",\n")
            json.dump(rec, out, ensure_ascii=False, indent=2)
            first = False
            n += 1
            if n % 4000 == 0:
                print(f"    {n:,} restaurants ...", flush=True)
        out.write("\n]\n")

    total = stats["exact"] + stats["recovered"] + stats["dropped"]
    print(f"\n  ingredient values processed : {total:,}")
    print(f"    already in vocabulary     : {stats['exact']:,}")
    print(f"    recovered by normalisation: {stats['recovered']:,}")
    print(f"    dropped (not an ingredient): {stats['dropped']:,} "
          f"({len(dropped):,} distinct)")
    print(f"    items refilled from profile: {stats['refilled_from_profile']:,}")
    print(f"    items left with none       : {stats['left_empty']:,}")
    print("\n  top dropped values:")
    for s, c in dropped.most_common(12):
        print(f"     {c:>7,}  {s[:56]}")

    if args.dry_run:
        tmp.unlink(missing_ok=True)
        print("\n  --dry-run: original untouched")
        return 0

    tmp.replace(FILE)
    with open(DROPPED, "w", encoding="utf-8-sig", newline="") as f:
        f.write("value,occurrences\n")
        for s, c in dropped.most_common():
            f.write(f'"{s}",{c}\n')
    print(f"\n  -> {FILE.name} ({FILE.stat().st_size/1e6:.0f} MB)")
    print(f"  -> {DROPPED.name}  (audit trail of everything removed)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
