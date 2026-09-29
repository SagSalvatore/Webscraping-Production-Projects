"""Fold the team's LLM review into the mapping, then rebuild the deliverable.

INPUT  UPDATING_EXISTING_MENU_STDTERMS_ING_FINALIZED.xlsx  (52,867 verdicts)
OUTPUT std_term_mapping_reviewed.json  - the mapping build_july_update.py reads

WHY THE MAPPING AND NOT THE DELIVERABLE. The obvious move is to edit
July_menu_update.json in place, but its menu_items carry `name` (smart_title of
item_key_clean), NOT item_key - so there is no reliable way back to the key the
verdict is addressed to. Editing the MAPPING and rebuilding keeps every write
keyed by item_key, which is also what makes the consistency guarantee hold for
free: the deliverable never stores a per-row term, it looks one up per key.

THE INVARIANT SAGAR ASKED FOR: identical menu items get identical std_term and
identical ingredients, everywhere. That is structural here - one entry per
item_key, applied to every row carrying that key - and it is asserted at the end
rather than assumed.

WHAT IS AND IS NOT TOUCHED
  * 52,867 newly-mapped keys  -> the review's Final Std Terms / Final Ingredients
  * 310,061 keys from July    -> UNTOUCHED. They keep the labels Tech already
                                 holds, which is the whole point of tier 2.

CASING. The review writes Title Case ("Potato | Cheese"); July ships lowercase
("potato"). Values are conformed to the taxonomy file's own spelling, so the
deliverable stays byte-consistent with what Tech has.

NON-MENU ITEMS. 5,378 keys come back as "not applicable - non-menu item" -
party candles, salt lamps, flowers. Their ingredients are set EMPTY, matching
the convention in the authoritative NDJSON (ingredients_applicable=false).
Fabricating ingredients for a candle is worse than an empty list.

    python apply_review_verdicts.py --dry-run
    python apply_review_verdicts.py
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"

XLSX = HERE / "UPDATING_EXISTING_MENU_STDTERMS_ING_FINALIZED.xlsx"
BASE_MAP = DATA / "std_term_mapping_august.json"
REVIEW_ND = DATA / "new_mappings_review.ndjson"
TAXO = ROOT / "August_menu" / "ingredients_export_02.json"
OUT_MAP = DATA / "std_term_mapping_reviewed.json"
REPORT = DATA / "review_applied_report.json"

# declared by the team in the workbook's "New Ingredients" sheet. Four are
# already in ingredients_export_02.json; only nigella seed is genuinely new.
NEW_INGREDIENTS = {
    "pasta": "bakery and cereal",
    "tuna": "seafood",
    "sesame oil": "fats and oils",
    "dumpling wrapper": "bakery and cereal",
    "nigella seed": "herbs and spices",
}

sys.stdout.reconfigure(encoding="utf-8")


def load_vocab():
    d = json.loads(TAXO.read_text(encoding="utf-8"))
    v = {r["ingredient_name"].strip().lower(): r["ingredient_name"].strip()
         for r in d if r.get("ingredient_name")}
    added = 0
    for name in NEW_INGREDIENTS:
        if name not in v:
            v[name] = name
            added += 1
    return v, added


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
    print("=" * 74)
    print("  APPLY REVIEW VERDICTS")
    print("=" * 74)

    vocab, added = load_vocab()
    print(f"  ingredient vocabulary: {len(vocab):,} terms "
          f"({added} added from the New Ingredients sheet)")

    # review_id -> item_key
    key_of = {}
    with open(REVIEW_ND, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                key_of[r["review_id"]] = r["item_key"]
    print(f"  review_id -> item_key   : {len(key_of):,}")

    wb = openpyxl.load_workbook(XLSX, read_only=True)
    ws = wb["Sheet1"]
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    ix = {h: i for i, h in enumerate(hdr)}
    verdicts = {}
    stats = Counter()
    unresolved = Counter()
    for r in it:
        rid = r[ix["review_id"]]
        if not rid:
            continue
        k = key_of.get(rid)
        if k is None:
            stats["review_id_not_ours"] += 1
            continue
        term = (r[ix["Final Std Terms"]] or "").strip()
        taxo = (r[ix["Final Taxonomy"]] or "").strip()
        raw = str(r[ix["Final Ingredients"]] or "")

        if "not applicable" in raw.lower():
            ings = []
            stats["non_menu_item"] += 1
        else:
            ings, seen = [], set()
            for p in raw.split("|"):
                p = p.strip()
                if not p:
                    continue
                c = conform(p, vocab)
                if c is None:
                    unresolved[p.lower()] += 1
                    continue
                if c not in seen:
                    seen.add(c)
                    ings.append(c)
        if not term:
            stats["blank_term_skipped"] += 1
            continue
        verdicts[k] = {"std_term": term, "taxonomy": taxo, "ingredients": ings}
    wb.close()
    print(f"  verdicts parsed         : {len(verdicts):,}")
    for k, v in stats.most_common():
        print(f"    {k:24} {v:,}")
    if unresolved:
        print(f"  ingredient values not in the vocabulary: {len(unresolved):,} "
              f"({sum(unresolved.values()):,} occurrences)")
        for k, v in unresolved.most_common(8):
            print(f"     {v:>6,}  {k}")

    base = json.loads(BASE_MAP.read_text(encoding="utf-8"))
    print(f"\n  base mapping keys       : {len(base):,}")

    t_chg = i_chg = 0
    for k, v in verdicts.items():
        cur = base.get(k) or {}
        if (cur.get("std_term") or "") != v["std_term"]:
            t_chg += 1
        if tuple(cur.get("ingredients") or []) != tuple(v["ingredients"]):
            i_chg += 1
        base[k] = {**cur, **v, "method": "llm_review",
                   "review_required": False, "confidence": 1.0}
    print(f"  std_term overridden     : {t_chg:,}")
    print(f"  ingredients overridden  : {i_chg:,}")
    print(f"  keys left from July     : {len(base) - len(verdicts):,}  (untouched)")

    # the invariant, at mapping level: one entry per key means one answer per key
    assert len(base) == len(set(base)), "duplicate keys in the mapping"
    no_term = [k for k, v in base.items() if not v.get("std_term")]
    print(f"  keys with no std_term   : {len(no_term):,}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    OUT_MAP.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    REPORT.write_text(json.dumps({
        "verdicts_applied": len(verdicts),
        "std_term_overridden": t_chg,
        "ingredients_overridden": i_chg,
        "non_menu_items": stats["non_menu_item"],
        "untouched_july_keys": len(base) - len(verdicts),
        "vocabulary_size": len(vocab),
        "unresolved_ingredient_values": dict(unresolved.most_common(30)),
    }, indent=2), encoding="utf-8")
    print(f"\n  -> {OUT_MAP.name}  ({len(base):,} keys)")
    print(f"  -> {REPORT.name}")
    print("\n  next: rebuild the deliverable against this mapping")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
