"""Stage 2 - sanitize the raw menu capture. Never mutates the raw file.

Reads  data/menu_items.jsonl        (raw, exactly as Talabat served it)
Writes data/menu_items_clean.jsonl  (cleaned)
       data/sanitize_mapping.csv    every value change, from -> to, reversible
       data/sanitize_exceptions.csv rows a human should look at
       data/sanitize_report.json    before/after metrics

Rules are deterministic and ordered; each was written against measured counts
from the raw capture, not guessed:

 1. NFKC unicode normalize      fullwidth/ligature forms folded to canonical
 2. strip control + zero-width  invisible chars that break grouping silently
 3. category: strip emoji       Talabat decorates sections ("Picks for you (fire)")
 4. name/description: keep emoji - brands use them deliberately in item names
 5. ARABIC IS PRESERVED         bilingual EN+AR categories are valid for UAE
 6. collapse whitespace         "Egyptian  Pizza" copy-paste artifacts
 7. RECOMPUTE item_key          it DOES matter: 0 changes on a 1.8k sample but
                                195 on the full 555k, where NFKC folds ligatures
                                and fullwidth forms that lowercase+strip alone
                                leaves alone. Never conclude "no-op" from a small
                                sample. The assert at the end enforces that the
                                key always matches the cleaned name.
 8. promo de-duplication        see below

PROMO DUPLICATES - the one destructive step, and why:
Talabat repeats an item inside promotional sections ("Offers", "Picks for you"),
so the SAME (branch_id, item_id) appears 2+ times with different `category`.
Measured at ~12% of rows. That breaks any (branch_id, item_id) primary key on
load. We keep the row carrying the item's REAL menu section and drop the promo
copy, recording `was_also_in` so the promo placement is not lost.
Disable with --keep-promo-dupes. Every drop is listed in sanitize_mapping.csv.

    python sanitize_menus.py --dry-run
    python sanitize_menus.py
"""
import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
def _argv_value(flag, default=None):
    """Read --flag VALUE (or --flag=VALUE) straight from argv.

    Needed BEFORE argparse runs because DATA and everything derived from it are
    module-level constants. A cohort that cannot repoint DATA would read
    August's menu_items.jsonl and overwrite August's outputs - the same hazard
    run2_collector's --out and restaurant_identifier's --cycle already fix.
    """
    import sys as _s
    if flag in _s.argv:
        i = _s.argv.index(flag)
        if i + 1 < len(_s.argv):
            return _s.argv[i + 1]
    for a in _s.argv:
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return default

DATA = Path(_argv_value("--data") or (HERE / "data"))

sys.path.insert(0, str(ROOT / "sanitization"))
from text_cleaner import clean_category, clean_item_name, clean_description

sys.stdout.reconfigure(encoding="utf-8")

RAW = DATA / "menu_items.jsonl"
CLEAN = DATA / "menu_items_clean.jsonl"
MAPPING = DATA / "sanitize_mapping.csv"
EXCEPT = DATA / "sanitize_exceptions.csv"
REPORT = DATA / "sanitize_report.json"

# Sections that re-advertise items already listed elsewhere on the same menu.
# Deliberately conservative: matching too broadly would delete real categories
# (a shop genuinely selling "Combos" is not a promo section).
PROMO_RX = re.compile(
    r"^\s*(offers?|special offers?|picks for you|recommended|popular"
    r"|most popular|best ?sell(er|ing)?s?|top rated|trending|deals?"
    r"|hot deals?|new arrivals?|featured)\s*$", re.I)

ZW_RX = re.compile(r"[​-‏­﻿⁠]")
CTRL_RX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
AR_RX = re.compile(r"[؀-ۿ]")

PRICE_MAX = 5000.0        # above this on a food menu is almost certainly wrong


def make_item_key(name: str) -> str:
    """Must stay identical to make_item_key() in scrape_menus.py / the tracker."""
    return re.sub(r"\s+", " ", str(name).lower().strip())


def base_clean(s: str) -> str:
    """NFKC + remove invisible characters. Applied before field-specific rules."""
    if not s:
        return s
    s = unicodedata.normalize("NFKC", s)
    s = ZW_RX.sub("", s)
    return CTRL_RX.sub("", s)


def main(args):
    if not RAW.exists():
        print(f"missing {RAW} - run scrape_menus.py first")
        return 1
    rows = [json.loads(l) for l in open(RAW, encoding="utf-8") if l.strip()]
    n_in = len(rows)
    print("=" * 68)
    print("  MENU SANITIZATION")
    print("=" * 68)
    print(f"  input rows: {n_in:,}")

    changes, exceptions = [], []
    stats = Counter()

    # ---------- pass 1: field cleaning ----------
    for r in rows:
        raw_cat, raw_name, raw_desc = r["category"], r["item_name"], r["description"]
        cat = clean_category(base_clean(raw_cat))
        name = clean_item_name(base_clean(raw_name))
        desc = clean_description(base_clean(raw_desc))

        # An item name that was ONLY decoration would clean to nothing - keep the
        # raw rather than emit a blank name.
        if not name.strip():
            name = raw_name.strip()
            stats["name_would_be_empty"] += 1

        for field, before, after in (("category", raw_cat, cat),
                                     ("item_name", raw_name, name),
                                     ("description", raw_desc, desc)):
            if before != after:
                stats[f"changed_{field}"] += 1
                changes.append({"branch_id": r["branch_id"], "item_id": r["item_id"],
                                "field": field, "from": before, "to": after})
        r["category"], r["item_name"], r["description"] = cat, name, desc

        # key MUST be recomputed from the cleaned name
        new_key = make_item_key(name)
        if new_key != r["item_key"]:
            stats["changed_item_key"] += 1
            changes.append({"branch_id": r["branch_id"], "item_id": r["item_id"],
                            "field": "item_key", "from": r["item_key"], "to": new_key})
        r["item_key"] = new_key

        r["is_promo_section"] = bool(PROMO_RX.match(cat))
        if r["is_promo_section"]:
            stats["in_promo_section"] += 1
        if AR_RX.search(cat) or AR_RX.search(name):
            stats["has_arabic_preserved"] += 1

        # price sanity - flagged, never silently altered
        p = r.get("price_aed")
        if p is None or p < 0:
            stats["price_invalid"] += 1
            exceptions.append({**{k: r[k] for k in ("branch_id", "item_id", "item_name")},
                               "issue": "price null/negative", "value": p})
        elif p == 0:
            stats["price_zero"] += 1
        elif p > PRICE_MAX:
            stats["price_outlier"] += 1
            exceptions.append({**{k: r[k] for k in ("branch_id", "item_id", "item_name")},
                               "issue": f"price > {PRICE_MAX}", "value": p})
        if not r["item_name"].strip():
            exceptions.append({"branch_id": r["branch_id"], "item_id": r["item_id"],
                               "item_name": r["item_name"], "issue": "empty name", "value": ""})

    # ---------- pass 2: promo de-duplication ----------
    by_key = defaultdict(list)
    for r in rows:
        by_key[(r["branch_id"], r["item_id"])].append(r)
    dups = {k: v for k, v in by_key.items() if len(v) > 1}
    print(f"\n  duplicate (branch_id,item_id) groups : {len(dups):,}")

    kept = rows
    if not args.keep_promo_dupes and dups:
        drop_ids = set()
        for (bid, iid), group in dups.items():
            real = [g for g in group if not g["is_promo_section"]]
            winner = real[0] if real else group[0]
            others = [g for g in group if g is not winner]
            also = sorted({g["category"] for g in others if g["category"]})
            winner["was_also_in"] = "; ".join(also)
            for g in others:
                drop_ids.add(id(g))
                changes.append({"branch_id": bid, "item_id": iid,
                                "field": "ROW_DROPPED", "from": g["category"],
                                "to": f"merged into '{winner['category']}'"})
            stats["promo_rows_dropped"] += len(others)
        kept = [r for r in rows if id(r) not in drop_ids]

    for r in kept:
        r.setdefault("was_also_in", "")

    # ---------- invariants ----------
    seen = Counter((r["branch_id"], r["item_id"]) for r in kept)
    still_dup = sum(1 for v in seen.values() if v > 1)
    assert all(r["item_key"] == make_item_key(r["item_name"]) for r in kept), \
        "item_key out of sync with item_name"
    assert len({r["branch_id"] for r in kept}) == len({r["branch_id"] for r in rows}), \
        "a restaurant lost every row"
    if not args.keep_promo_dupes:
        assert still_dup == 0, f"{still_dup} duplicate keys survived"

    print(f"\n  --- changes ---")
    for k in ("changed_category", "changed_item_name", "changed_description",
              "changed_item_key", "in_promo_section", "has_arabic_preserved",
              "promo_rows_dropped", "price_zero", "price_invalid",
              "price_outlier", "name_would_be_empty"):
        if stats[k]:
            print(f"    {k:24} {stats[k]:>6,}")
    print(f"\n  rows in  : {n_in:,}")
    print(f"  rows out : {len(kept):,}   ({n_in-len(kept):,} promo duplicates collapsed)")
    print(f"  (branch_id,item_id) unique: {len(seen):,}/{len(kept):,}"
          f"{'  OK' if still_dup == 0 else f'  <-- {still_dup} DUPES'}")
    print(f"  distinct categories: {len({r['category'] for r in rows}):,} raw"
          f" -> {len({r['category'] for r in kept}):,} clean")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        if changes:
            print("\n  sample changes:")
            for c in changes[:8]:
                print(f"    {c['field']:12} {str(c['from'])[:34]!r} -> {str(c['to'])[:34]!r}")
        return 0

    with open(CLEAN, "w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if changes:
        with open(MAPPING, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["branch_id", "item_id", "field", "from", "to"])
            w.writeheader(); w.writerows(changes)
    if exceptions:
        with open(EXCEPT, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["branch_id", "item_id", "item_name",
                                              "issue", "value"])
            w.writeheader(); w.writerows(exceptions)
    REPORT.write_text(json.dumps({
        "rows_in": n_in, "rows_out": len(kept),
        "duplicate_groups": len(dups),
        "unique_branch_item_pairs": len(seen),
        "distinct_categories_raw": len({r["category"] for r in rows}),
        "distinct_categories_clean": len({r["category"] for r in kept}),
        "changes_logged": len(changes), "exceptions": len(exceptions),
        **dict(stats),
    }, indent=2), encoding="utf-8")

    print(f"\n  -> {CLEAN.name}   ({len(kept):,} rows)")
    if changes:
        print(f"  -> {MAPPING.name}  ({len(changes):,} changes, reversible)")
    if exceptions:
        print(f"  -> {EXCEPT.name} ({len(exceptions):,} to review)")
    print(f"  -> {REPORT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--data", help="cohort data dir ""(default: August_menu/data). Repoints every input and output.")
    p.add_argument("--keep-promo-dupes", action="store_true",
                   help="do NOT collapse repeated promo rows")
    sys.exit(main(p.parse_args()))
