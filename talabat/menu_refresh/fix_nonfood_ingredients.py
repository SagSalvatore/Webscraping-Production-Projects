"""Non-food menu items must carry NO ingredients, and the class profiles must
stop inventing them.

WHAT WENT WRONG. fill_ingredient_gaps.py learns, per std_term, the ingredients
appearing in >=30% of items carrying it. Applied to a NON-FOOD term that is
meaningless: the profile for "Marketing/Non-Standard Menu" came out as ['salt']
and "Service And Packaging" as ['sugar','salt','butter'] - noise averaged over a
category that has no ingredients by definition. A party candle does not contain
salt.

Measured in the shipped files before this fix:
    August_export.json     2,771 non-food items, ALL with ingredients,
                           'Discarded' x2,102
    July_menu_update.json 14,652 non-food items, ALL with ingredients,
                           'Salt' x11,557

An empty ARRAY is the right representation, not the string "N/A": July's own
export ships [] for the 53,785 items it had no ingredients for, and `ingredients`
is a controlled-vocabulary list - putting "N/A" inside it makes "N/A" an
ingredient.

This does two things, streamed, to both deliverables:
  1. non-food std_terms  -> ingredients []
  2. everything else     -> conformed to ingredients_export_02.json, which
                            August never had applied (that is where 'Discarded'
                            survived)

It also strips the non-food terms out of the profile file so the next build
cannot reintroduce them.

    python fix_nonfood_ingredients.py --dry-run
    python fix_nonfood_ingredients.py
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
DATA = HERE / "data"

TAXO = ROOT / "August_menu" / "ingredients_export_02.json"
PROFILES = DATA / "std_term_canonical_ingredients.json"
FILES = [ROOT / "August_menu" / "data" / "August_export.json",
         DATA / "July_menu_update.json"]

# terms where ingredients do not apply. Same convention as the authoritative
# NDJSON, which marks these ingredients_applicable=false with an empty list.
NON_FOOD = {"Marketing/Non-Standard Menu", "Service And Packaging"}

NEW_INGREDIENTS = {"pasta", "tuna", "sesame oil", "dumpling wrapper",
                   "nigella seed"}

sys.stdout.reconfigure(encoding="utf-8")


def load_vocab():
    d = json.loads(TAXO.read_text(encoding="utf-8"))
    v = {r["ingredient_name"].strip().lower(): r["ingredient_name"].strip()
         for r in d if r.get("ingredient_name")}
    for n in NEW_INGREDIENTS:
        v.setdefault(n, n)
    return v


def conform(s, vocab):
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
    p = re.sub(r"\(.*?\)", "", t).strip(' "\'')
    return vocab.get(p)


def main(args):
    vocab = load_vocab()
    print("=" * 74)
    print(f"  NON-FOOD INGREDIENTS + VOCABULARY CONFORMANCE  "
          f"({len(vocab):,} terms)")
    print("=" * 74)

    prof = json.loads(PROFILES.read_text(encoding="utf-8"))
    stale = {t: prof[t] for t in NON_FOOD if prof.get(t)}
    for t in NON_FOOD:
        prof.pop(t, None)          # strip BEFORE any refill uses them
    print(f"  profiles removed for non-food terms: {len(stale)}  {stale}")

    for path in FILES:
        if not path.exists():
            print(f"\n  {path.name}: not found")
            continue
        st = Counter()
        dropped = Counter()
        tmp = path.with_suffix(".tmp")
        n = 0
        with open(path, "rb") as src, open(tmp, "w", encoding="utf-8") as out:
            out.write("[\n")
            first = True
            for rec in ijson.items(src, "item", use_float=True):
                for it in rec.get("menu_items") or []:
                    term = it.get("std_term")
                    if term in NON_FOOD:
                        if it.get("ingredients"):
                            st["nonfood_cleared"] += 1
                        it["ingredients"] = []
                        continue
                    keep, seen = [], set()
                    for i in it.get("ingredients") or []:
                        s = str(i).strip()
                        if not s:
                            continue
                        c = conform(s, vocab)
                        if c is None:
                            st["dropped"] += 1
                            dropped[s.lower()] += 1
                            continue
                        if c not in seen:
                            seen.add(c)
                            keep.append(c)
                        st["kept"] += 1
                    if keep != (it.get("ingredients") or []):
                        st["items_changed"] += 1
                    # A FOOD item emptied by conformance is a gap, not a
                    # decision - refill it from its std_term profile. The
                    # profiles have had the non-food terms stripped, so this
                    # cannot put 'salt' back into a candle.
                    if not keep:
                        keep = [vocab[p.lower()] for p in (prof.get(term) or [])
                                if p.lower() in vocab]
                        if keep:
                            st["food_refilled_from_profile"] += 1
                        else:
                            st["food_item_left_empty"] += 1
                    it["ingredients"] = keep
                if not first:
                    out.write(",\n")
                json.dump(rec, out, ensure_ascii=False, indent=2)
                first = False
                n += 1
                if n % 5000 == 0:
                    print(f"    {path.name}: {n:,} ...", flush=True)
            out.write("\n]\n")

        print(f"\n  {path.name}   ({n:,} records)")
        for k in ("nonfood_cleared", "items_changed", "kept", "dropped",
                  "food_refilled_from_profile", "food_item_left_empty"):
            if st[k]:
                print(f"    {k:24} {st[k]:>9,}")
        if dropped:
            print(f"    top dropped: "
                  f"{[k for k, _ in dropped.most_common(6)]}")
        if args.dry_run:
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(path)

    if args.dry_run:
        print("\n  --dry-run: originals untouched")
        return 0

    for t in stale:
        prof.pop(t, None)
    PROFILES.write_text(json.dumps(prof, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"\n  -> {PROFILES.name}: {len(stale)} non-food profile(s) removed")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
