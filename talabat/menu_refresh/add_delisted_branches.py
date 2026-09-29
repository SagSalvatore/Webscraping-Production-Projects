"""Add the 80 delisted branches back into July_menu_update.json, carrying their
JULY menus unchanged.

Per Sagar's discussion with Tech: these 80 returned an empty menu page this
month (all re-checked live, all genuinely empty), so instead of omitting them
the file keeps last month's menu for each. 3,294 items across the 80.

Source is export_july_flat.jsonl - the flattened July deliverable - so the menu
content is exactly what Tech already holds. The only thing applied is the same
text scrub the rest of this file went through, so one deliverable is not half
cleaned and half not. That touches presentation, never the menu itself.

Streamed: the existing file is 584 MB and rewriting it via a full load would
need several GB. Records are copied through one at a time and the 80 appended.

    python add_delisted_branches.py --dry-run
    python add_delisted_branches.py
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import ijson

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
sys.path.insert(0, str(HERE))

from build_july_update import scrub                      # noqa: E402

FILE = DATA / "July_menu_update.json"
STATUS = DATA / "scrape" / "restaurant_status.jsonl"
JULY_FLAT = DATA / "export_july_flat.jsonl"

sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    delisted = set()
    with open(STATUS, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                if r.get("status") != "ok":
                    delisted.add(str(r["branch_id"]))
    print("=" * 70)
    print(f"  ADD DELISTED BRANCHES  ({len(delisted)} branches)")
    print("=" * 70)

    # 188 of these July items shipped with an empty ingredient list. The other
    # 15,119 branches in this file are at 100% coverage, so fill them from the
    # same std_term class profiles rather than leave one corner of the
    # deliverable inconsistent. The MENU is untouched - only the derived
    # ingredient field is completed.
    profiles = json.loads(
        (DATA / "std_term_canonical_ingredients.json").read_text(encoding="utf-8"))
    filled = 0

    # their July menus, in this file's menu_items[] shape
    by_branch = defaultdict(list)
    with open(JULY_FLAT, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            b = str(r["branch_id"])
            if b not in delisted:
                continue
            term = scrub(r.get("std_term"))
            ings = r.get("ingredients") or []
            if not ings:
                ings = profiles.get(term) or []
                if ings:
                    filled += 1
            by_branch[b].append({
                "name": scrub(r.get("item_name")),
                "section": scrub(r.get("category")) or "",
                "description": scrub(r.get("description")),
                "std_term": term,
                "price": r.get("price_aed"),
                "ingredients": [x for x in (scrub(i) for i in ings) if x],
                "is_popular": bool(r.get("is_popular")),
            })
    items = sum(len(v) for v in by_branch.values())
    print(f"  branches with a July menu : {len(by_branch)}")
    print(f"  menu items carried over   : {items:,}")

    no_std = sum(1 for v in by_branch.values() for i in v if not i["std_term"])
    no_ing = sum(1 for v in by_branch.values() for i in v if not i["ingredients"])
    print(f"  items without std_term    : {no_std:,}")
    print(f"  items without ingredients : {no_ing:,}  (filled from profile: {filled:,})")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    existing_ids = set()
    tmp = FILE.with_suffix(".tmp")
    n_copied = 0
    with open(FILE, "rb") as src, open(tmp, "w", encoding="utf-8") as out:
        out.write("[\n")
        first = True
        for rec in ijson.items(src, "item", use_float=True):
            existing_ids.add(rec["source_id"])
            if not first:
                out.write(",\n")
            json.dump(rec, out, ensure_ascii=False, indent=2)
            first = False
            n_copied += 1
            if n_copied % 4000 == 0:
                print(f"    copied {n_copied:,} ...", flush=True)

        added = 0
        for b, mi in by_branch.items():
            if b in existing_ids:
                print(f"    {b} already present - skipped")
                continue
            out.write(",\n")
            json.dump({"source_name": "talabat", "source_id": b,
                       "menu_items": mi}, out, ensure_ascii=False, indent=2)
            added += 1
        out.write("\n]\n")

    tmp.replace(FILE)
    print(f"\n  copied {n_copied:,} existing + added {added} delisted")
    print(f"  -> {FILE.name} ({FILE.stat().st_size/1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
