"""Stage the refresh's NEW menu items for the cohort text pipeline.

WHAT A NEW ITEM IS. compute_delta.py marks an item ADDED when its item_key was
not on that branch's menu at the baseline. Those items were labelled
(map_std_terms.py -> conform -> Sagar's review -> apply_refresh_review.py) and
must now reach the unified file - which means the SAME text treatment every
shipped menu got first: sanitize_menus.py -> translate_arabic.py -> price rules
-> build_august_export.menu_item().

WHY THE ROWS COME FROM THE RAW SCRAPE, NOT FROM THE LABELLED FILE.
sanitize_menus.py collapses promo copies ("Picks for you", "Offers") keyed on
(branch_id, item_id). The labelled rows descend from delta rows, which carry NO
item_id - fed to sanitize as-is, every item of a branch would share the key
(branch_id, None) and collapse into ONE row. So the scrape rows for each ADDED
(branch_id, item_key) are taken, promo copies included, and the reviewed label
is attached to them; sanitize then keeps the copy in the item's real section.

Labels are joined on the RAW item_key, before sanitize recomputes it. Every
copy of one item shares that key, so every copy carries the same label.

The output directory is the cohort --data dir for the August_menu stages:
    python build_new_items_input.py --run refresh_202609
    cd ../August_menu
    python sanitize_menus.py   --data ../menu_refresh/data/refresh_202609/newitems
    python translate_arabic.py --data ../menu_refresh/data/refresh_202609/newitems --dry-run
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.stdout.reconfigure(encoding="utf-8")

LABEL_FIELDS = ("std_term", "taxonomy", "ingredients", "std_term_confidence",
                "vote_agreement", "std_term_method", "review_required",
                "review_applied", "dish_family")
# Translation caches are keyed by SOURCE STRING, so earlier cohorts' work is
# reusable verbatim - same string, same English, and no second API bill.
SEED_CACHES = [ROOT / "September" / "data" / "menus" / "translation_cache.json",
               ROOT / "August_menu" / "data" / "translation_cache.json"]


def main(args):
    rd = HERE / "data" / args.run
    labelled = rd / "stdterm" / "menu_items_final.jsonl"
    scrape = rd / "scrape" / "menu_items.jsonl"
    out_dir = rd / "newitems"
    out = out_dir / "menu_items.jsonl"
    print("=" * 74)
    print(f"  NEW MENU ITEMS -> cohort text pipeline input   [{args.run}]")
    print("=" * 74)
    for p in (labelled, scrape):
        if not p.exists():
            raise SystemExit(f"missing: {p}")

    label = {}
    for line in open(labelled, "rb"):
        r = orjson.loads(line)
        k = (r["branch_id"], r["item_key"])
        if k in label:
            raise SystemExit(f"labelled file repeats {k} - grain is not (branch, item_key)")
        label[k] = {f: r.get(f) for f in LABEL_FIELDS} | {
            "restaurant_id": r.get("restaurant_id"), "cohort": r.get("cohort")}
    print(f"  labelled (branch, item_key) pairs : {len(label):,}")

    rows, hit, ids_per_pair = [], Counter(), {}
    for line in open(scrape, "rb"):
        s = orjson.loads(line)
        k = (s["branch_id"], s["item_key"])
        lab = label.get(k)
        if lab is None:
            continue
        hit[k] += 1
        ids_per_pair.setdefault(k, set()).add(s.get("item_id"))
        rows.append({**s, **lab})

    missing = [k for k in label if k not in hit]
    multi_id = sum(1 for v in ids_per_pair.values() if len(v) > 1)
    no_id = sum(1 for r in rows if r.get("item_id") is None)
    print(f"  scrape rows taken                 : {len(rows):,}")
    print(f"    pairs found in the scrape       : {len(hit):,}")
    print(f"    pairs NOT found                 : {len(missing):,}   (must be 0)")
    print(f"    pairs with promo/extra copies   : {sum(1 for v in hit.values() if v > 1):,}")
    print(f"    pairs spanning >1 item_id       : {multi_id:,}   (distinct items sharing a name - kept)")
    print(f"    rows with no item_id            : {no_id:,}   (must be 0)")
    if missing or no_id:
        raise SystemExit("coverage gate failed - nothing written")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        for r in rows:
            f.write(orjson.dumps(r) + b"\n")
    cache = out_dir / "translation_cache.json"
    if not cache.exists():
        seed = {}
        for p in SEED_CACHES:
            if p.exists():
                d = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    for k, v in d.items():
                        seed.setdefault(k, v)
        cache.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
        print(f"  translation cache seeded from earlier cohorts: {len(seed):,} strings")
    print(f"\n  -> {out.relative_to(ROOT)}  ({len(rows):,} rows)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--run", default="refresh_202609")
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
