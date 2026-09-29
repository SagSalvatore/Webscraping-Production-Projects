"""Remove restaurants that returned no menu - from EVERY September file.

A restaurant with no menu items is not a deliverable row: Tech's file is
menus, so an entry with an empty menu_items[] carries nothing. Sagar's
instruction is to discard the whole record, not merely its (absent) items.

REMOVED FROM ALL THREE, so no later stage can resurrect them:
    september_new_listings_202609_deduped.csv    the brand list
    sept_menu_targets.jsonl                      the scrape input
    menus/restaurant_status.jsonl                the scrape output

menu_items.jsonl needs no filtering - these restaurants contributed no rows to
it, which is the whole point.

Originals are kept alongside as .with_empty.bak so the drop is reversible.

    python drop_empty_menus.py --dry-run
    python drop_empty_menus.py
"""
import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
STATUS = DATA / "menus" / "restaurant_status.jsonl"
TARGETS = DATA / "sept_menu_targets.jsonl"
LISTINGS = DATA / "september_new_listings_202609_deduped.csv"
DROPPED = DATA / "september_empty_menu_dropped.csv"
sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    print("=" * 74)
    print("  DROP RESTAURANTS WITH NO MENU")
    print("=" * 74)

    rows = [json.loads(l) for l in open(STATUS, encoding="utf-8")]
    from collections import Counter
    print(f"  scraped restaurants : {len(rows):,}")
    print(f"  status              : {dict(Counter(r.get('status') for r in rows))}")

    # anything that produced zero items, whatever the label
    # the field is item_count. An earlier version guessed `items`/`n_items`,
    # which do not exist, so `not (...)` was True for EVERY row and the dry-run
    # proposed discarding all 1,061 - caught only because --dry-run prints them.
    bad = [r for r in rows
           if r.get("status") != "ok" or not (r.get("item_count") or 0)]
    bad_ids = {str(r.get("branch_id")) for r in bad}
    print(f"\n  discarding {len(bad_ids)} restaurant(s) with no menu:")
    for r in bad:
        print(f"     {str(r.get('status')):16}{str(r.get('branch_id')):10}"
              f"{str(r.get('name'))[:44]}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    # audit trail first
    with open(DROPPED, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["branch_id", "name", "status", "url"])
        for r in bad:
            w.writerow([r.get("branch_id"), r.get("name"), r.get("status"),
                        r.get("url", "")])

    # 1 the brand list
    lst = list(csv.DictReader(open(LISTINGS, encoding="utf-8-sig")))
    keep = [x for x in lst if str(x["branch_id"]) not in bad_ids]
    shutil.copy2(LISTINGS, LISTINGS.with_suffix(".csv.with_empty.bak"))
    with open(LISTINGS, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(lst[0].keys()))
        w.writeheader()
        w.writerows(keep)
    print(f"\n  listings CSV : {len(lst):,} -> {len(keep):,}")

    # 2 the scrape input
    tg = [json.loads(l) for l in open(TARGETS, encoding="utf-8")]
    tkeep = [x for x in tg if str(x["branch_id"]) not in bad_ids]
    shutil.copy2(TARGETS, TARGETS.with_suffix(".jsonl.with_empty.bak"))
    with open(TARGETS, "w", encoding="utf-8") as f:
        for x in tkeep:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    print(f"  menu targets : {len(tg):,} -> {len(tkeep):,}")

    # 3 the scrape output
    skeep = [r for r in rows if str(r.get("branch_id")) not in bad_ids]
    shutil.copy2(STATUS, STATUS.with_suffix(".jsonl.with_empty.bak"))
    with open(STATUS, "w", encoding="utf-8") as f:
        for r in skeep:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  scrape status: {len(rows):,} -> {len(skeep):,}")

    items = DATA / "menus" / "menu_items.jsonl"
    n = sum(1 for l in open(items, encoding="utf-8")
            if str(json.loads(l).get("branch_id")) in bad_ids)
    print(f"\n  menu_items rows belonging to the dropped: {n}  (expected 0)")
    print(f"\n  SEPTEMBER FINAL COUNT : {len(keep):,} restaurants")
    print(f"  -> {DROPPED.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
