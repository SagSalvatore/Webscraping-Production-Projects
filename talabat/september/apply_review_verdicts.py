"""Apply Sagar's manual review, and conform the whole cohort to the TAXONOMY FILE.

Two things happen in one pass over menu_items_with_std_terms.jsonl, because both
are about the same fields and doing them separately would mean conforming twice.

1  INGREDIENT VOCABULARY - THE AUTHORITY IS THE JSON FILE, NOT POSTGRES.
   conform_and_key_review.py read `ingredients_taxonomy` from Postgres: 982 terms
   in Title Case. What Tech is actually built against is
   August_menu/ingredients_export_02.json - 820 terms, all lowercase, and exactly
   what July's 7,810,742 shipped values sit inside. Conforming against the DB gave
   the right vocabulary in the wrong casing: 407,830 values, 0% lowercase, so
   "Chicken" would not join to Tech's "chicken" on a single row. Same defect
   menu_refresh/conform_to_taxonomy_file.py was written to fix for July.

   The matching rule is unchanged and still IMPORTED, never reimplemented:
       exact (case-insensitive) -> plural/split/paren recovery -> DROP.

2  THE MANUAL VERDICTS  std_terms_with_ing_done.xlsx, 11,868 rows, all filled.
   Joined on review_id, never on item_key: item_key contains quotes and colons
   ('"tamriyah" box: dates with rahash...') which do not survive a spreadsheet
   round trip. All 11,868 ids matched, covering 12,106 menu rows.

   Verified before writing anything:
     corrected_std_term        116 distinct, 100% already in what Tech holds,
                               casing identical to Tech's, all 116 resolve to a
                               taxonomy through the existing cascade
     corrected_ingredients     101,178 values, 100% inside the 820-term file
                               apart from one sentinel (below)
     "not applicable - non-menu item"   155 rows -> ingredients emptied. These
                               are flowers, vases and gift sets; Sagar labelled
                               them Marketing/Non-Standard Menu (141) and
                               Service And Packaging (14), both real std_terms.

TAXONOMY IS RE-DERIVED, NOT COPIED FROM THE SHEET. `taxonomy` is a 9-value
contract (core_food, beverage, combo, side, dessert, breakfast, addon,
accompaniment, marketing/non-standard menu). The sheet's "Final Taxonomy" is a
59-value DISH FAMILY (Grill, Manoushe, Fatteh, Sushi & Sashimi) - a different
question, and 7 of its values straddle two taxonomy values, so it is not a
refinement that could be collapsed. Writing it into `taxonomy` would replace the
contract. It is carried as its own field, `dish_family`, and `taxonomy` is
re-derived from the corrected std_term.

    python apply_review_verdicts.py --dry-run
    python apply_review_verdicts.py
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

import openpyxl
import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "menu_refresh"))
sys.path.insert(0, str(ROOT / "August_menu"))
from conform_ingredients import recover                             # noqa: E402
from map_std_terms import MANUAL_TAXONOMY, resolve_taxonomy         # noqa: E402

DATA = HERE / "data" / "menus"
SRC = DATA / "menu_items_with_std_terms.jsonl"
OUT = DATA / "menu_items_final.jsonl"
XLSX = HERE / "std_terms_with_ing_done.xlsx"
REVIEW = DATA / "std_term_needs_review.json"
TAXO = ROOT / "August_menu" / "ingredients_export_02.json"
TAXCSV = ROOT / "menu" / "final_std_terms_taxonomy.csv"
AUDIT = DATA / "review_verdicts_applied.csv"
DROPPED = DATA / "ingredients_dropped_vs_file.csv"

NON_MENU = "not applicable - non-menu item"

# 25 verdicts Sagar reversed after seeing them listed (Sept 2026). A
# flower/bloom/petal keyword sweep over item NAMES pulled real drinks into
# Non-Menu - the menu SECTION they sit in gives them away: "Cold Drinks",
# "Craft Teas", "Mojitos And Mocktails". Mountains Dew Zero is the clearest.
# Rejecting a verdict here does not invent a label: the row falls through to the
# untouched branch and keeps the machine's std_term, taxonomy and ingredients,
# so no term enters the vocabulary that was not already in it.
# The genuinely non-menu ones stay - 200g coffee-bean retail bags, a YoYo,
# steel straws, artificial flowers, gift sets.
REJECTED = {
    "MK-59f0e55e3820",  # Bloom and Brews             | Flowers and Coffee
    "MK-f3220528413d",  # Petal Blooms                | Flowers and Coffee
    "MK-bcc86b1668da",  # Floral And Berry            | Flowers and Coffee
    "MK-359b2e7300f1",  # Minty Blossom               | Flowers and Coffee
    "MK-f57132f37b9e",  # Cream Saffron Shengini      | Speciality Cold Tea
    "MK-b5ea729fa802",  # Fresh Kharkadi 400ml        | Mocktails & Juice
    "MK-08017da9fc60",  # Pour Over - Ethiopia        | Filtered Coffee
    "MK-3b2f173713ad",  # Blossom Hot                 | Classics Coffee
    "MK-5d34a37d1d2d",  # Pour Over - Tadesse Kosha   | Filtered Coffee
    "MK-c693b2e92a70",  # Citrus Floral Mocktail      | Mojitos And Mocktails
    "MK-d5c7da4f360f",  # Jasmine Pearl - Floral      | Craft Teas
    "MK-6c3cb98f65f7",  # Blooming Flower Sip         | Flowers and Coffee
    "MK-96d059cd4958",  # Long Jing - Green           | Craft Teas
    "MK-71c8b8310da8",  # Iced Blue Pea Flower Tea    | Iced coffee
    "MK-b00d23faeccc",  # Rose Petal Laddoo           | Indian Sweets
    "MK-df03c6dbea13",  # Butterfly Breez             | Mocktails
    "MK-c3ac1eb1f686",  # Arabian Reserve V60         | TPT Signature Blend Coffee
    "MK-7829b948d0dc",  # Lebro Rose                  | SIGNATURE DRINK
    "MK-e39873915e18",  # Hibiscus Small              | Drink
    "MK-007e69da2233",  # Lychee Nitro                | Signature Drinks
    "MK-d5b18683356a",  # Berry Hibiscus Large        | Drink
    "MK-d845293ac1de",  # MAcrons Cori                | Sweets
    "MK-fd3cfe1e3f3a",  # Ethiopia V60 (Cold)         | Cold Drinks
    "MK-ce6e458f6d42",  # Mountains Dew Zero          | Beverages and Drinks
    "MK-62725b180c91",  # KARKADE CUP                 | COLD DRINKS
}
sys.stdout.reconfigure(encoding="utf-8")


def load_vocab():
    """820 terms, lowercase - the file Tech is built against."""
    d = json.loads(TAXO.read_text(encoding="utf-8"))
    return {r["ingredient_name"].strip().lower(): r["ingredient_name"].strip()
            for r in d if r.get("ingredient_name")}


def load_tax():
    tax = {}
    with open(TAXCSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            s = (row.get("Std_terms") or "").strip().lower()
            if s:
                tax[s] = (row.get("taxonomy") or "").strip()
    tax.update(MANUAL_TAXONOMY)
    return tax


def split(v):
    """`ingredients` arrives in TWO shapes and must be normalised before use.
    Tier-0 rows (prior_delivered, 44,250) carry a list, because the unified file
    ships arrays; the kNN/exact tiers (23,246) carry one comma-joined string,
    because the Excel reference is free text. Iterating a str yields CHARACTERS -
    a first version without this dropped 693,885 "values", the top ones being
    'e', ',' and 'a'."""
    if v is None:
        return []
    if isinstance(v, list):
        return [str(p).strip() for p in v if str(p).strip()]
    return [p.strip() for p in str(v).split(",")
            if p.strip() and p.strip().lower() != "nan"]


def load_verdicts():
    """review_id -> verdict, then re-keyed onto item_key via the review file."""
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = ws.iter_rows(values_only=True)
    head = [str(c).strip() if c is not None else "" for c in next(rows)]
    ix = {h: i for i, h in enumerate(head)}
    for need in ("review_id", "corrected_std_term", "corrected_ingredients",
                 "Final Taxonomy"):
        if need not in ix:
            raise SystemExit(f"column missing from the sheet: {need!r}")
    out = {}
    for r in rows:
        rid = r[ix["review_id"]]
        if not rid:
            continue
        out[str(rid).strip()] = {
            "std_term": str(r[ix["corrected_std_term"]] or "").strip(),
            "ingredients": split(r[ix["corrected_ingredients"]]),
            "dish_family": str(r[ix["Final Taxonomy"]] or "").strip(),
        }
    wb.close()
    return out


def conform(vals, vocab, drop_counts):
    """exact -> recover -> drop. Output is always the file's own lowercase."""
    keep, seen = [], set()
    for v in split(vals):
        s = str(v).strip()
        if not s:
            continue
        got = vocab.get(s.lower()) or recover(s, vocab)
        if not got:
            drop_counts[s.lower()] += 1
            continue
        if got not in seen:
            seen.add(got)
            keep.append(got)
    return keep


def main(args):
    print("=" * 78)
    print("  APPLY MANUAL VERDICTS + CONFORM TO THE TAXONOMY FILE")
    print("=" * 78)

    vocab, tax = load_vocab(), load_tax()
    print(f"  ingredient vocabulary : {len(vocab):,} terms from {TAXO.name}")
    print(f"  taxonomy lookup       : {len(tax):,} std_terms -> "
          f"{len(set(tax.values()))} taxonomy values")

    verdicts = load_verdicts()
    rev = json.loads(REVIEW.read_text(encoding="utf-8"))
    key_of, matched = {}, 0
    for k, v in rev.items():
        rid = v.get("review_id")
        if rid not in verdicts:
            continue
        matched += 1
        if rid not in REJECTED:
            key_of[k] = verdicts[rid]
    print(f"  verdicts in the sheet : {len(verdicts):,}")
    print(f"  matched to an item_key: {matched:,}"
          f"{'' if matched == len(verdicts) else '   <-- SHORTFALL'}")
    if matched != len(verdicts):
        raise SystemExit("not every verdict found its item_key - refusing to write")
    print(f"  verdicts reversed     : {matched - len(key_of):,}  "
          f"(of {len(REJECTED)} listed; they keep the machine's label)")
    if matched - len(key_of) != len(REJECTED):
        raise SystemExit("a REJECTED review_id is not in the sheet - stale list")

    drop_counts = Counter()
    st = Counter()
    audit = []
    out_rows = []
    for line in open(SRC, "rb"):
        r = orjson.loads(line)
        k = (r.get("item_key") or "").strip().lower()
        v = key_of.get(k)

        if v is None:
            r["ingredients"] = conform(r.get("ingredients") or [],
                                       vocab, drop_counts)
            r["dish_family"] = ""
            r["review_applied"] = False
            st["untouched"] += 1
        else:
            before_t, before_i = r.get("std_term"), split(r.get("ingredients"))
            if v["ingredients"] == [NON_MENU]:
                ings = []
                st["non_menu"] += 1
            else:
                ings = conform(v["ingredients"], vocab, drop_counts)
            r["std_term"] = v["std_term"]
            r["taxonomy"] = resolve_taxonomy(v["std_term"], tax)
            r["ingredients"] = ings
            r["dish_family"] = v["dish_family"]
            r["review_applied"] = True
            r["std_term_method"] = "manual_review"
            r["std_term_confidence"] = 1.0
            r["review_required"] = False
            st["reviewed"] += 1
            if (before_t or "").strip().lower() != v["std_term"].lower():
                st["std_term_changed"] += 1
            if [x.lower() for x in before_i] != [x.lower() for x in ings]:
                st["ingredients_changed"] += 1
            audit.append([k, before_t, v["std_term"], r["taxonomy"],
                          v["dish_family"], "; ".join(before_i),
                          "; ".join(ings)])
        if not r["taxonomy"]:
            st["NO_TAXONOMY"] += 1
        out_rows.append(r)

    n = len(out_rows)
    print(f"\n  menu rows             : {n:,}")
    print(f"    review applied      : {st['reviewed']:,}")
    print(f"      std_term changed  : {st['std_term_changed']:,}")
    print(f"      ingredients changed:{st['ingredients_changed']:,}")
    print(f"      non-menu, emptied : {st['non_menu']:,}")
    print(f"    untouched, reconformed: {st['untouched']:,}")

    vals = sum(len(r["ingredients"]) for r in out_rows)
    low = sum(1 for r in out_rows for x in r["ingredients"] if x == x.lower())
    empty = sum(1 for r in out_rows if not r["ingredients"])
    print(f"\n  ingredient values     : {vals:,}")
    print(f"    lowercase           : {low:,} ({low/vals*100:.1f}%)   "
          f"[was 0.0% against the DB]")
    print(f"    dropped by conform  : {sum(drop_counts.values()):,} "
          f"({len(drop_counts):,} distinct)")
    print(f"  items with no ingredients : {empty:,}")
    print(f"  rows with NO taxonomy : {st['NO_TAXONOMY']:,}   (must be 0)")
    if drop_counts:
        print("\n  most-dropped:")
        for v_, c in drop_counts.most_common(10):
            print(f"     {c:>6}  {v_[:56]}")

    tc = Counter(r["taxonomy"] for r in out_rows)
    print(f"\n  taxonomy ({len(tc)} values, the contract):")
    for t, c in tc.most_common():
        print(f"     {c:>7,}  {t}")
    df = Counter(r["dish_family"] for r in out_rows if r["dish_family"])
    print(f"\n  dish_family: {len(df)} values on {sum(df.values()):,} rows"
          f"   (new field, does not touch `taxonomy`)")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    with open(OUT, "wb") as f:
        for r in out_rows:
            f.write(orjson.dumps(r) + b"\n")
    with open(AUDIT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item_key", "std_term_before", "std_term_after", "taxonomy",
                    "dish_family", "ingredients_before", "ingredients_after"])
        w.writerows(audit)
    with open(DROPPED, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dropped_value", "occurrences"])
        w.writerows(drop_counts.most_common())

    print(f"\n  -> {OUT.name}              ({n:,} rows)")
    print(f"  -> {AUDIT.name}   ({len(audit):,} rows)")
    print(f"  -> {DROPPED.name}  ({len(drop_counts):,} values)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
