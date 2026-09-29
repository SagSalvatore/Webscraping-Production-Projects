"""Conform September's ingredients to the controlled vocabulary, and give the
review file a stable, spreadsheet-safe key.

PART 1 - INGREDIENT CONFORMANCE
July shipped 7,810,742 ingredient values, 100% inside ingredients_taxonomy, zero
free text. That was structural, not luck: it read from menu_item_ingredients,
which has a FOREIGN KEY to the taxonomy, so an invalid term could not appear.
A pipeline building from the mapping JSON has no such constraint - August found
100,625 values (15,709 distinct) outside the vocabulary, "discarded" 12,645
times, plus free text like "eggs", "flour", "tea/coffee".

The matching is IMPORTED from menu_refresh/conform_ingredients.py rather than
reimplemented, so the two can never drift:
    1 exact match (case-insensitive) -> canonical casing
    2 light recovery: plural -> singular, "a/b" -> first side, parens stripped;
      accepted ONLY if the result is in the vocabulary
    3 anything still unmatched is DROPPED - which is what July's FK did
Everything dropped is written out, never silently discarded.

THE VOCABULARY IS THE FILE, NOT POSTGRES - see load_vocab below. Postgres is not
touched at all, which also keeps the parked loading work out of the way.

PART 2 - THE REVIEW FILE MUST CARRY FULL MENU CONTEXT
A reviewer cannot judge "is this std_term right" from the item_key alone.
Sagar's standing rule: the review export carries EVERY column of the menu row -
item_name, description, category, price, the restaurant it came from - so the
call can be made by reading one line instead of going back to the source data.
The mapper keys on item_key (the normalised name), but the human needs the
original name, what the restaurant says it is, and which section it sits in.

review_id ON THE REVIEW FILE
std_term_needs_review.json is keyed by raw item_key, and the very first key is
'"tamriyah" box: dates with rahash and tahini sauce' - quotes and a colon. Those
do not survive a spreadsheet round trip, so a returned verdict cannot be joined
back. review_id = "MK-" + sha256(item_key)[:12], the same convention August's
manager review used: the "MK-" prefix keeps Excel treating it as text, and the
hash is deterministic so next month's verdicts still land on the right row.

    python conform_and_key_review.py --dry-run
    python conform_and_key_review.py
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "menu_refresh"))
from conform_ingredients import recover                            # noqa: E402

# THE VOCABULARY COMES FROM THE FILE, NOT POSTGRES. conform_ingredients.load_vocab
# selects from `ingredients_taxonomy`: 982 terms in Title Case. What Tech is built
# against is ingredients_export_02.json - 820 terms, all lowercase, and exactly
# what July's 7,810,742 shipped values sit inside. Reading the DB gave the right
# vocabulary in the wrong casing on every row ("Chicken" never joins "chicken"),
# the same defect menu_refresh/conform_to_taxonomy_file.py fixed for July.
# Only `recover` is imported, so the matching rule still cannot drift.
TAXO = ROOT / "August_menu" / "ingredients_export_02.json"


def load_vocab():
    d = json.loads(TAXO.read_text(encoding="utf-8"))
    return {r["ingredient_name"].strip().lower(): r["ingredient_name"].strip()
            for r in d if r.get("ingredient_name")}

DATA = HERE / "data" / "menus"
SRC = DATA / "menu_items_with_std_terms.jsonl"
OUT = DATA / "menu_items_conformed.jsonl"
DROPPED = DATA / "ingredients_dropped.csv"
REVIEW = DATA / "std_term_needs_review.json"
REVIEW_CSV = DATA / "std_term_needs_review.csv"
sys.stdout.reconfigure(encoding="utf-8")


def review_id(item_key: str) -> str:
    """Stable and Excel-safe. 'MK-' keeps it textual; sha256 keeps it
    deterministic so a verdict returned later joins back cleanly."""
    return "MK-" + hashlib.sha256(item_key.encode("utf-8")).hexdigest()[:12]


def as_list(v):
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def main(args):
    print("=" * 76)
    print("  CONFORM INGREDIENTS + KEY THE REVIEW FILE")
    print("=" * 76)

    vocab = load_vocab()
    print(f"  controlled vocabulary : {len(vocab):,} terms  (read-only from PG)")

    rows = [json.loads(l) for l in open(SRC, encoding="utf-8") if l.strip()]
    print(f"  menu rows             : {len(rows):,}")

    kept = dropped = exact = recovered = 0
    drop_counts, emptied = Counter(), 0
    out_rows = []
    for r in rows:
        vals = as_list(r.get("ingredients"))
        new = []
        for v in vals:
            if v.lower() in vocab:
                new.append(vocab[v.lower()])
                exact += 1
            else:
                got = recover(v, vocab)
                if got:
                    new.append(got)
                    recovered += 1
                else:
                    drop_counts[v.lower()] += 1
                    dropped += 1
        # de-duplicate while preserving order
        seen, final = set(), []
        for v in new:
            if v not in seen:
                seen.add(v)
                final.append(v)
        if vals and not final:
            emptied += 1
        kept += len(final)
        r["ingredients"] = final
        out_rows.append(r)

    total = exact + recovered + dropped
    print(f"\n  ingredient values     : {total:,}")
    print(f"    exact match         : {exact:>9,} ({exact/total*100:5.1f}%)")
    print(f"    recovered           : {recovered:>9,} ({recovered/total*100:5.1f}%)")
    print(f"    DROPPED             : {dropped:>9,} ({dropped/total*100:5.1f}%)")
    print(f"  values kept (deduped) : {kept:,}")
    print(f"  items emptied by this : {emptied:,}")
    if drop_counts:
        print(f"\n  most-dropped values ({len(drop_counts):,} distinct):")
        for v, n in drop_counts.most_common(12):
            print(f"     {n:>6}  {v[:52]}")

    rev = json.loads(REVIEW.read_text(encoding="utf-8"))
    print(f"\n  review file entries   : {len(rev):,}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    with open(OUT, "w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(DROPPED, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dropped_value", "occurrences"])
        w.writerows(drop_counts.most_common())

    # review file: keyed, and emitted as CSV so it can actually be worked in
    keyed = {}
    for k, v in rev.items():
        keyed[k] = {"review_id": review_id(k), **v}
    REVIEW.write_text(json.dumps(keyed, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    # full menu context per item_key, so the reviewer never has to look
    # anything up. A key can appear in many restaurants, so the most common
    # surface form wins and the spread is reported alongside.
    ctx = {}
    for r in out_rows:
        k = (r.get("item_key") or "").strip().lower()
        if k not in rev:
            continue
        c = ctx.setdefault(k, {"names": Counter(), "descs": Counter(),
                               "cats": Counter(), "prices": [], "brs": set()})
        if r.get("item_name"):
            c["names"][r["item_name"]] += 1
        if r.get("description"):
            c["descs"][r["description"]] += 1
        if r.get("category"):
            c["cats"][r["category"]] += 1
        if r.get("price_aed") is not None:
            c["prices"].append(r["price_aed"])
        c["brs"].add(r.get("branch_id"))

    rest = {}
    tgt = HERE / "data" / "sept_menu_targets.jsonl"
    if tgt.exists():
        for line in open(tgt, encoding="utf-8"):
            t = json.loads(line)
            rest[t["branch_id"]] = (t.get("name"), t.get("area_name"),
                                    t.get("url"))

    with open(REVIEW_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["review_id", "item_name", "description", "category",
                    "price_aed", "std_term", "taxonomy", "ingredients",
                    "confidence", "vote_agreement", "method",
                    "menu_rows", "restaurants", "example_restaurant",
                    "example_area", "example_url", "item_key",
                    "corrected_std_term", "corrected_ingredients", "notes"])
        for k, v in sorted(keyed.items(),
                           key=lambda kv: kv[1].get("confidence") or 0):
            c = ctx.get(k, {"names": Counter(), "descs": Counter(),
                            "cats": Counter(), "prices": [], "brs": set()})
            top = lambda cc: cc.most_common(1)[0][0] if cc else ""
            br = next(iter(c["brs"]), None)
            rn, ra, ru = rest.get(br, ("", "", ""))
            pr = c["prices"]
            w.writerow([
                v["review_id"], top(c["names"]), top(c["descs"]),
                top(c["cats"]),
                f"{min(pr):g}-{max(pr):g}" if pr and min(pr) != max(pr)
                else (f"{pr[0]:g}" if pr else ""),
                v.get("std_term"), v.get("taxonomy"), v.get("ingredients"),
                v.get("confidence"), v.get("vote_agreement"), v.get("method"),
                sum(c["names"].values()), len(c["brs"]), rn, ra, ru,
                k, "", "", ""])

    print(f"\n  -> {OUT.name}          ({len(out_rows):,} rows)")
    print(f"  -> {DROPPED.name}   ({len(drop_counts):,} distinct values)")
    print(f"  -> {REVIEW.name}  (+review_id)")
    print(f"  -> {REVIEW_CSV.name}  ({len(keyed):,} rows, worst confidence first)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
