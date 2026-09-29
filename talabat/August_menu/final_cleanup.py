"""Stage 4 - drop corrupt prices and non-restaurant businesses.

FILENAME WARNING: output is menu_items_shipped.jsonl, NOT menu_items_FINAL.
Windows filesystems are case-INSENSITIVE, so an earlier "menu_items_FINAL.jsonl"
resolved to the same inode as the stage-3 "menu_items_final.jsonl" and silently
overwrote it. Never distinguish a pipeline stage by letter case alone.

Two removals, each written to its own file so nothing is unrecoverable:

1. CORRUPT PRICES (> 5,000 AED). Field corruption, not expensive food: the worst
   is 20,260,912,000,000 on a "Papaya MilkShake" - the date 2026-09-12 landing in
   a price column - and one branch has 7 juices at exactly 111,417. The cut sits
   at 5,000 because 113 rows between 1,000-5,000 are genuine (catering platters,
   a 501-rose bouquet), so a tighter cap would delete real data.

2. NON-RESTAURANT BUSINESSES. Two different tests, because a cuisine tag alone
   is not evidence - that was measured, not assumed:

   a) RETAIL tags (Grocery, Supermarket, Bookstore, Stationery, Home essentials)
      -> removed outright. These sell goods rather than serving prepared food,
      which is what "not a restaurant" means here.

   b) "Flowers" -> removed ONLY where the menu proves it is a florist. Of 18
      branches carrying the tag, just 6 sell mostly bouquets (DEM ART FLOWERS,
      WHITE ORCHID FLOWERS, Munasabat: 62-78% flower items). The other 12 are
      sweets/chocolate/cafe businesses with 0-3% flower items that merely also
      offer flowers - normal for UAE gift shops - and they are KEPT.
      Judging (b) by the tag alone would have deleted "Shahad Al Jazeera Sweets
      & Pastries": 112 real menu items, zero flowers.

    python final_cleanup.py --dry-run
    python final_cleanup.py
"""
import argparse
import json
import re
import sys
from collections import defaultdict
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

# the cohort's restaurant list. Without an override September would read
# August's brands and write over August's shipped file.
BRANDS = Path(_argv_value("--brands") or
              (ROOT / "listing_comparison" / "output" / "talabat_genuinely_new_brands.jsonl"))
MENU = DATA / "menu_items_final.jsonl"

BRANDS_OUT = Path(_argv_value("--brands-out") or
                  (ROOT / "listing_comparison" / "output" / "talabat_restaurants_shipped.jsonl"))
MENU_OUT = DATA / "menu_items_shipped.jsonl"
REMOVED_BIZ = DATA / "removed_non_restaurants.jsonl"
REMOVED_PRICES = DATA / "removed_corrupt_prices.jsonl"

PRICE_MAX = 5000.0
RETAIL_TAGS = {"Grocery", "Supermarket", "Bookstore", "Stationery", "Home essentials"}
FLOWER_TAG = "Flowers"
FLORIST_THRESHOLD = 20.0        # % of menu items that are bouquets

FLOWER_ITEM_RX = re.compile(
    r"rose|bouquet|flower|blooming|tulip|orchid|lily|carnation|gypso", re.I)

sys.stdout.reconfigure(encoding="utf-8")


def read(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def main(args):
    brands = read(BRANDS)
    menu = read(MENU)
    print("=" * 66)
    print("  FINAL CLEANUP - non-restaurants + corrupt prices")
    print("=" * 66)
    print(f"  brands in : {len(brands):,}")
    print(f"  menu rows : {len(menu):,}")

    by_branch = defaultdict(list)
    for m in menu:
        by_branch[m["branch_id"]].append(m)

    drop, reasons = {}, {}
    for b in brands:
        tags = set(b.get("cuisines") or [])
        bid = b["branch_id"]
        retail = tags & RETAIL_TAGS
        if retail:
            drop[bid] = b
            reasons[bid] = f"retail: {','.join(sorted(retail))}"
            continue
        if FLOWER_TAG in tags:
            items = by_branch.get(bid, [])
            n = sum(1 for i in items if FLOWER_ITEM_RX.search(i["item_name"]))
            pct = n / len(items) * 100 if items else 0.0
            if pct >= FLORIST_THRESHOLD:
                drop[bid] = b
                reasons[bid] = f"florist: {pct:.0f}% flower items ({n}/{len(items)})"

    flower_tagged = [b for b in brands if FLOWER_TAG in set(b.get("cuisines") or [])]
    kept_flower = [b for b in flower_tagged if b["branch_id"] not in drop]

    print(f"\n  --- non-restaurant removals ---")
    print(f"  retail-tagged      : {sum(1 for r in reasons.values() if r.startswith('retail')):>3}")
    print(f"  florists (by menu) : {sum(1 for r in reasons.values() if r.startswith('florist')):>3}"
          f"   of {len(flower_tagged)} Flowers-tagged")
    print(f"  food shops KEPT despite a Flowers tag: {len(kept_flower)}")
    for b in drop.values():
        print(f"     REMOVE  {b['name'][:38]:40} {reasons[b['branch_id']]}")
    for b in kept_flower[:6]:
        items = by_branch.get(b["branch_id"], [])
        n = sum(1 for i in items if FLOWER_ITEM_RX.search(i["item_name"]))
        print(f"     keep    {b['name'][:38]:40} {n}/{len(items)} flower items")

    bad_price = [m for m in menu if (m.get("price_aed") or 0) > PRICE_MAX]
    kept_brands = [b for b in brands if b["branch_id"] not in drop]
    kept_menu = [m for m in menu if m["branch_id"] not in drop
                 and (m.get("price_aed") or 0) <= PRICE_MAX]

    print(f"\n  corrupt prices > {PRICE_MAX:,.0f} : {len(bad_price)}"
          f"   worst {max((m['price_aed'] for m in bad_price), default=0):,.0f}")
    print(f"\n  brands : {len(brands):,} -> {len(kept_brands):,}")
    print(f"  menu   : {len(menu):,} -> {len(kept_menu):,}")

    assert len({b["branch_id"] for b in kept_brands}) == len(kept_brands), "dup branch_id"
    assert len({(m["branch_id"], m["item_id"]) for m in kept_menu}) == len(kept_menu), \
        "duplicate (branch_id,item_id)"
    assert not [m for m in kept_menu if (m.get("price_aed") or 0) > PRICE_MAX], \
        "corrupt price survived"
    assert not ({m["branch_id"] for m in kept_menu} & set(drop)), \
        "removed business still has menu rows"
    orphan = {m["branch_id"] for m in kept_menu} - {b["branch_id"] for b in kept_brands}
    assert not orphan, f"{len(orphan)} menu rows have no parent brand"
    assert MENU_OUT.name.lower() != MENU.name.lower(), \
        "output name collides with input on a case-insensitive filesystem"

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    removed = [{**b, "removal_reason": reasons[b["branch_id"]]} for b in drop.values()]
    for path, rows in ((BRANDS_OUT, kept_brands), (MENU_OUT, kept_menu),
                       (REMOVED_BIZ, removed), (REMOVED_PRICES, bad_price)):
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n  -> {BRANDS_OUT.name}   ({len(kept_brands):,} restaurants)")
    print(f"  -> {MENU_OUT.name}   ({len(kept_menu):,} menu items)")
    print(f"  -> {REMOVED_BIZ.name}  ({len(removed)} businesses, with reason)")
    print(f"  -> {REMOVED_PRICES.name}  ({len(bad_price)})")

    print(f"\n  --- final unique counts ---")
    print(f"  restaurants with menus : {len({m['branch_id'] for m in kept_menu}):,}")
    print(f"  (branch_id,item_id)    : {len({(m['branch_id'],m['item_id']) for m in kept_menu}):,}")
    print(f"  distinct item_key      : {len({m['item_key'] for m in kept_menu}):,}")
    print(f"  distinct category      : {len({m['category'] for m in kept_menu}):,}")
    print(f"  price_aed max          : {max(m['price_aed'] for m in kept_menu):,.2f}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--brands", help="cohort restaurant jsonl")
    p.add_argument("--brands-out", help="where to write the shipped list")
    p.add_argument("--data", help="cohort data dir ""(default: August_menu/data). Repoints every input and output.")
    sys.exit(main(p.parse_args()))
