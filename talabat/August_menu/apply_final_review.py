"""Stage 6 - apply Sagar's manually-finalised std_terms and ingredients.

Source of truth: AUGUST_menu_items_FINALIZED_with_ingredients_v2.ndjson
    86,997 reviewed item_keys with `final_std_terms`, `final_taxonomy` and
    `final_ingredients`. Per Sagar these are correct and win over anything the
    automated mapping produced.

JOIN KEY IS `item_key`, NOT `review_id`.
418 review_ids came back corrupted: Excel strips leading zeros from an
all-digit hash, so 006136449828 -> 6136449828. The hash existed precisely to
survive a spreadsheet round-trip and the spreadsheet ate it anyway. `item_key`
survived intact and resolves all 86,997 rows. (Next time prefix the id with a
letter so it can never be parsed as a number.)

VOCABULARY IS UNIFIED TO THE NDJSON'S:
    the review file uses 128 Title-Case std_terms; the automated mapping used
    95 lower-case ones. All 95 appear in the 128 (case-insensitively), and each
    of the 128 maps to exactly ONE taxonomy - verified, zero ambiguity. So the
    ndjson's spelling and its std_term->taxonomy map are applied to EVERY row,
    reviewed or not, and the dataset ends up with one vocabulary instead of two.

INGREDIENTS BECOME A LIST EVERYWHERE:
    reviewed rows already carry a list of canonical ingredients; unreviewed
    rows hold free text ("Tortilla wrap, egg, kass, French fries..."), which is
    split on commas. Mixing str and list in one field would break the DB load.

GUARANTEE: no row may end with a null/empty std_term, taxonomy or ingredients.
Asserted before anything is written.

    python apply_final_review.py --dry-run
    python apply_final_review.py
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
REVIEW = HERE / "AUGUST_menu_items_FINALIZED_with_ingredients_v2.ndjson"
SRC = DATA / "menu_items_with_std_terms.jsonl"
OUT = DATA / "menu_items_FINAL_reviewed.jsonl"
REPORT = DATA / "final_review_report.json"
VOCAB = DATA / "std_term_vocabulary.json"

sys.stdout.reconfigure(encoding="utf-8")


def split_ingredients(s):
    """Free-text ingredient string -> de-duplicated list, order preserved."""
    if not s:
        return []
    parts = [p.strip(" .;") for p in re.split(r"[,;]", str(s))]
    out, seen = [], set()
    for p in parts:
        k = p.lower()
        if p and k not in seen:
            seen.add(k)
            out.append(p)
    return out


def main(args):
    rev = [json.loads(l) for l in open(REVIEW, encoding="utf-8") if l.strip()]
    rows = [json.loads(l) for l in open(SRC, encoding="utf-8") if l.strip()]
    print(f"  review rows : {len(rev):,}")
    print(f"  menu rows   : {len(rows):,}")

    # canonical vocabulary straight from the review file
    tax_of, canon = {}, {}
    for r in rev:
        t = str(r["final_std_terms"]).strip()
        tax_of[t] = str(r["final_taxonomy"]).strip()
        canon[t.lower()] = t
    print(f"  vocabulary  : {len(tax_of)} std_terms -> "
          f"{len(set(tax_of.values()))} taxonomies")

    by_key = {}
    for r in rev:
        k = r.get("item_key")
        if k:
            by_key[k] = r
    print(f"  reviewed keys: {len(by_key):,}")

    stats = Counter()
    unmapped_terms = Counter()
    out = []
    for m in rows:
        r = by_key.get(m["item_key"])
        if r:
            std = str(r["final_std_terms"]).strip()
            tax = str(r["final_taxonomy"]).strip()
            ing = [str(x).strip() for x in (r.get("final_ingredients") or [])
                   if str(x).strip()]
            cats = [str(x).strip() for x in (r.get("ingredient_categories") or [])
                    if str(x).strip()]
            src = "manual_review"
            stats["reviewed"] += 1
            if not ing:
                # reviewer marked it not applicable (drinks, service charges)
                ing = split_ingredients(m.get("ingredients"))
                stats["reviewed_ingredients_backfilled"] += 1
        else:
            raw = (m.get("std_term") or "").strip()
            std = canon.get(raw.lower(), raw)
            if raw and raw.lower() not in canon:
                unmapped_terms[raw] += 1
            tax = tax_of.get(std) or (m.get("taxonomy") or "").strip()
            ing = split_ingredients(m.get("ingredients"))
            cats = []
            src = "auto_mapping"
            stats["auto"] += 1
        if not ing:
            ing = ["unspecified"]           # the no-null guarantee
            stats["ingredients_placeholder"] += 1
        out.append({**m, "std_term": std, "taxonomy": tax,
                    "ingredients": ing, "ingredient_categories": cats,
                    "std_term_source": src})

    # ---- the guarantee ----
    bad_std = [o for o in out if not (o["std_term"] or "").strip()]
    bad_tax = [o for o in out if not (o["taxonomy"] or "").strip()]
    bad_ing = [o for o in out if not o["ingredients"]]
    print(f"\n  --- no-null guarantee ---")
    print(f"    empty std_term    : {len(bad_std)}")
    print(f"    empty taxonomy    : {len(bad_tax)}")
    print(f"    empty ingredients : {len(bad_ing)}")
    if unmapped_terms:
        print(f"    std_terms not in the review vocabulary: {len(unmapped_terms)}"
              f"  {list(unmapped_terms)[:6]}")

    print(f"\n  reviewed rows : {stats['reviewed']:,}")
    print(f"  auto rows     : {stats['auto']:,}")
    print(f"  distinct std_term after merge : "
          f"{len({o['std_term'] for o in out}):,}")
    print(f"  distinct taxonomy after merge : "
          f"{len({o['taxonomy'] for o in out}):,}")
    changed = sum(1 for m, o in zip(rows, out)
                  if (m.get("std_term") or "").lower() != o["std_term"].lower())
    print(f"  std_term changed by this merge: {changed:,}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0
    assert not bad_std and not bad_tax and not bad_ing, "null guarantee violated"
    assert len(out) == len(rows), "row count changed"

    with open(OUT, "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    VOCAB.write_text(json.dumps(
        {"std_term_to_taxonomy": dict(sorted(tax_of.items())),
         "source": REVIEW.name}, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT.write_text(json.dumps({
        "rows": len(out), "reviewed": stats["reviewed"], "auto": stats["auto"],
        "std_term_changed": changed,
        "distinct_std_term": len({o["std_term"] for o in out}),
        "distinct_taxonomy": len({o["taxonomy"] for o in out}),
        "ingredients_placeholder": stats["ingredients_placeholder"],
        "reviewed_ingredients_backfilled": stats["reviewed_ingredients_backfilled"],
        "top_std_terms": dict(Counter(o["std_term"] for o in out).most_common(15)),
        "top_taxonomy": dict(Counter(o["taxonomy"] for o in out).most_common(15)),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {OUT.name} ({len(out):,} rows)")
    print(f"  -> {VOCAB.name}, {REPORT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
