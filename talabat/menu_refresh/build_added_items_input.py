"""Turn this month's ADDED delta rows into std_term mapper input.

Every refresh surfaces menu items that did not exist at the baseline. Sagar's
rule: a new item gets the SAME std_term + ingredients treatment the cohort
itself got - it does not ship unlabelled.

WHY A SEPARATE COHORT DIR. map_std_terms.py resolves DATA (and every input and
output under it) at import time from --data. Pointing it at an existing cohort
would read that cohort's menu_items_shipped.jsonl and OVERWRITE its mapping,
review file and report. Each refresh therefore gets its own directory.

WHY ALL ADDED ROWS, NOT JUST THE UNSEEN KEYS. 46.5% of September's ADDED keys
are names we have already mapped and delivered. Feeding only the unseen ones
would be faster but would assume the known ones are still consistent; feeding
everything makes tier 0 re-assert it from prior_item_labels.json and report the
split, so consistency with Tech is measured rather than hoped for.

KEY RENAME. The delta calls the section `menu_category`; the mapper reads
`category`, and category is the single biggest accuracy lever in the kNN
(56.0% -> 76.4%). A missing `category` does not raise - it would silently
degrade every kNN match - so it is asserted here.

DESCRIPTION COMES FROM THE SCRAPE, NOT THE DELTA. Delta rows carry no
description at all, and every review export must ship one: a reviewer judging
"Tropical Fruit Pot -> Chocolate" needs what the restaurant actually wrote.
It is joined back from the run's own menu_items.jsonl on item_key.

    python build_added_items_input.py --run refresh_202609
    python build_added_items_input.py --run refresh_202609 --dry-run
"""
import argparse
import shutil
import sys
from collections import Counter
from pathlib import Path

import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
PRIOR_SRC = ROOT / "september" / "data" / "prior_item_labels.json"
sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    rd = DATA / args.run
    month = args.run.split("_")[-1]
    deltas = rd / f"menu_deltas_{month}.jsonl"
    outdir = rd / "stdterm"
    out = outdir / "menu_items_shipped.jsonl"
    prior_dst = outdir / "prior_item_labels.json"

    print("=" * 70)
    print(f"  ADDED-ITEM INPUT for the std_term mapper   [{args.run}]")
    print("=" * 70)
    if not deltas.exists():
        print(f"  missing: {deltas} - run compute_delta.py first")
        return 1

    rows, keys, no_cat = [], Counter(), 0
    for line in open(deltas, "rb"):
        if not line.strip():
            continue
        r = orjson.loads(line)
        if r.get("change_type") != "ADDED":
            continue
        cat = (r.get("menu_category") or "").strip()
        if not cat:
            no_cat += 1
        rows.append({
            "branch_id": r["branch_id"],
            "restaurant_id": r.get("restaurant_id"),
            "item_key": r["item_key"],
            "item_name": r.get("item_name", ""),
            "category": cat,               # renamed from menu_category
            "price_aed": r.get("new_price_aed"),
            "description": "",             # filled from the scrape, below
            "source_run": r.get("run_id"),
            "cohort": r.get("cohort"),
        })
        keys[r["item_key"]] += 1

    # join the description back from this run's own scrape, per (branch, item)
    scrape = rd / "scrape" / "menu_items.jsonl"
    got = 0
    if scrape.exists():
        want = {(r["branch_id"], r["item_key"]) for r in rows}
        d = {}
        for line in open(scrape, "rb"):
            if not line.strip():
                continue
            s = orjson.loads(line)
            k = (s.get("branch_id"), s.get("item_key"))
            if k in want and (s.get("description") or "").strip():
                d.setdefault(k, s["description"].strip())
        for r in rows:
            v = d.get((r["branch_id"], r["item_key"]))
            if v:
                r["description"] = v
                got += 1
        print(f"  descriptions joined  : {got:,}/{len(rows):,} rows "
              f"({got/max(len(rows),1)*100:.1f}%)")
    else:
        print(f"  WARNING no scrape at {scrape} - descriptions will be blank")

    print(f"  ADDED rows        : {len(rows):>8,}")
    print(f"  distinct item_key : {len(keys):>8,}")
    print(f"  rows with no category: {no_cat:,}"
          f"  ({no_cat/max(len(rows),1)*100:.1f}%)  <- kNN accuracy lever")

    # how much tier 0 will absorb, measured before the mapper runs
    if PRIOR_SRC.exists():
        prior = orjson.loads(PRIOR_SRC.read_bytes())["labels"]
        hit = sum(1 for k in keys if k in prior)
        print(f"  prior labels      : {len(prior):>8,}")
        print(f"    tier0 will cover: {hit:>8,} keys "
              f"({hit/max(len(keys),1)*100:.1f}%)  "
              f"{sum(keys[k] for k in keys if k in prior):,} rows")
    else:
        print(f"  WARNING prior labels not found at {PRIOR_SRC}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    outdir.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        for r in rows:
            f.write(orjson.dumps(r) + b"\n")
    if PRIOR_SRC.exists() and not prior_dst.exists():
        shutil.copy2(PRIOR_SRC, prior_dst)

    print(f"\n  -> {out.relative_to(ROOT)}  ({len(rows):,} rows)")
    print(f"  -> {prior_dst.relative_to(ROOT)}")
    print("\n  next:")
    print(f"    cd ../August_menu && python map_std_terms.py \\")
    print(f"        --data ../menu_refresh/data/{args.run}/stdterm \\")
    print(f"        --prior ../menu_refresh/data/{args.run}/stdterm/"
          f"prior_item_labels.json --dry-run")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--run", default="refresh_202609",
                   help="refresh directory under data/")
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
