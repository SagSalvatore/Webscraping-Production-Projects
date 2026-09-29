"""Step 4 - reconcile the three copies of "last month's menu".

There are THREE, and they are not the same data:

  1. talabat_menu_items (Postgres)   - cleaned IN PLACE after scraping:
                                       emoji stripped from menu_category,
                                       whitespace collapsed in description
  2. export/talabat_export.json      - what Tech ACTUALLY received: further
                                       sanitised, Title Cased, item_id and
                                       item_key dropped entirely
  3. menu_refresh/data/scrape/       - this month, raw off the wire

compute_delta.py diffs 3 against 1. That is right for the INTERNAL report -
Postgres is the system of record and keeps item_key, which is the only stable
join key.

But Tech holds 2. If they diff next month's export against last month's, every
difference between 1 and 2 lands on their side as phantom churn. This script
measures that gap so we know what they will see before they see it.

The export is 388 MB and this box has under 1 GB free, so it is streamed with
ijson and flattened to JSONL on disk - never loaded whole.

    python compare_export.py --extract     # export -> flat JSONL (one pass)
    python compare_export.py               # the reconciliation
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import ijson

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
EXPORT = HERE.parent / "export" / "talabat_export.json"
FLAT = DATA / "export_july_flat.jsonl"
NEW_ITEMS = DATA / "scrape" / "menu_items.jsonl"

sys.stdout.reconfigure(encoding="utf-8")

EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF\U0000FE00-\U0000FE0F\U0001F1E6-\U0001F1FF‍⃣]")


def make_item_key(name: str) -> str:
    """Same rule as the tracker and August_menu: lowercase + collapse space."""
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def norm_text(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"\s+", " ", EMOJI.sub("", s)).strip()


def extract():
    """Stream the export down to one JSONL row per menu item."""
    n_rest = n_item = 0
    with open(EXPORT, "rb") as f, open(FLAT, "w", encoding="utf-8") as out:
        # use_float: ijson yields Decimal by default, which json cannot encode
        for rest in ijson.items(f, "item", use_float=True):
            bid = rest.get("source_id")
            n_rest += 1
            for it in rest.get("menu_items") or []:
                n_item += 1
                out.write(json.dumps({
                    "branch_id": int(bid) if str(bid).isdigit() else bid,
                    "item_name": it.get("name"),
                    "item_key": make_item_key(it.get("name")),
                    "category": it.get("section"),
                    "description": it.get("description"),
                    "price_aed": it.get("price"),
                    "std_term": it.get("std_term"),
                    # the export carries the resolved ingredient list per item -
                    # the same list Tech already holds, so reusing it keeps
                    # unchanged items byte-identical month to month
                    "ingredients": it.get("ingredients") or [],
                    "is_popular": it.get("is_popular"),
                }, ensure_ascii=False) + "\n")
            if n_rest % 2000 == 0:
                print(f"    {n_rest:,} restaurants, {n_item:,} items", flush=True)
    print(f"  -> {FLAT.name}: {n_rest:,} restaurants, {n_item:,} items")


BUCKETS = 12   # 1.3M + 1.25M items will not fit in <1 GB at once


def bucket_of(bid):
    return hash(str(bid)) % BUCKETS


def load_flat(p, key_fields, bucket=None):
    """Load one bucket of branches. Holding both files whole needs ~1 GB;
    this box has less, so the comparison runs BUCKETS times over a slice."""
    d = defaultdict(dict)
    with open(p, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            b = str(r["branch_id"])
            if bucket is not None and bucket_of(b) != bucket:
                continue
            k = r.get("item_key")
            if k:
                d[b][k] = tuple(r.get(x) for x in key_fields)
    return d


def branch_ids(p):
    s = set()
    with open(p, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                s.add(str(json.loads(line)["branch_id"]))
    return s


def main(args):
    if args.extract or not FLAT.exists():
        if not EXPORT.exists():
            print(f"  {EXPORT} not found")
            return 1
        print(f"  streaming {EXPORT.name} ({EXPORT.stat().st_size/1e6:.0f} MB) ...")
        extract()
        if args.extract:
            return 0

    print("=" * 70)
    print("  RECONCILE: what Tech holds  vs  what Postgres holds  vs  now")
    print("=" * 70)

    eb, nb = branch_ids(FLAT), branch_ids(NEW_ITEMS)
    both = eb & nb
    print(f"  July export  : {len(eb):,} restaurants")
    print(f"  August scrape: {len(nb):,} restaurants")
    print(f"\n  branches in both        : {len(both):,}")
    print(f"  only in July export     : {len(eb-nb):,}"
          "   <- Tech has these; this month did not re-scrape them")
    print(f"  only in August scrape   : {len(nb-eb):,}")
    del eb, nb

    stats = Counter()
    price_moves = []
    for bk in range(BUCKETS):
        exp = load_flat(FLAT, ("category", "description", "price_aed"), bk)
        new = load_flat(NEW_ITEMS, ("category", "description", "price_aed"), bk)
        for b in set(exp) & set(new):
            e, n = exp[b], new[b]
            for k in set(e) & set(n):
                ec, ed, ep = e[k]
                nc, nd, np_ = n[k]
                if norm_text(ec) != norm_text(nc):
                    if norm_text(ec).lower() == norm_text(nc).lower():
                        stats["section_case_only"] += 1
                    else:
                        stats["section_real"] += 1
                elif (ec or "") != (nc or ""):
                    stats["section_cosmetic"] += 1
                if norm_text(ed) != norm_text(nd):
                    if norm_text(ed).lower() == norm_text(nd).lower():
                        stats["description_case_only"] += 1
                    else:
                        stats["description_real"] += 1
                elif (ed or "") != (nd or ""):
                    stats["description_cosmetic"] += 1
                if ep is not None and np_ is not None and abs(float(ep) - float(np_)) > 0.01:
                    stats["price_changed"] += 1
                    if ep:
                        price_moves.append((float(np_) - float(ep)) / float(ep) * 100)
            stats["items_matched"] += len(set(e) & set(n))
            stats["items_only_july"] += len(set(e) - set(n))
            stats["items_only_august"] += len(set(n) - set(e))
        del exp, new
        print(f"    bucket {bk+1}/{BUCKETS} done", flush=True)

    print(f"\n  --- item-level, {len(both):,} shared branches ---")
    for k in ("items_matched", "items_only_july", "items_only_august"):
        print(f"     {k:24} {stats[k]:>9,}")
    m = stats["items_matched"] or 1
    print(f"\n  --- of the {m:,} items present in BOTH ---")
    for k in ("section_case_only", "section_cosmetic", "section_real",
              "description_case_only", "description_cosmetic",
              "description_real", "price_changed"):
        print(f"     {k:24} {stats[k]:>9,}  ({stats[k]/m*100:5.2f}%)")
    if price_moves:
        price_moves.sort()
        keep = [p for p in price_moves if abs(p) <= 300]
        print(f"\n  price moves vs the export: {len(price_moves):,}"
              f"   median {keep[len(keep)//2]:+.1f}%" if keep else "")
    print("\n  case-only differences are TITLE CASING applied at export time -")
    print("  they are not restaurant edits and Tech will see them as churn")
    print("  unless the same casing is applied to the new export.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--extract", action="store_true")
    sys.exit(main(p.parse_args()))
