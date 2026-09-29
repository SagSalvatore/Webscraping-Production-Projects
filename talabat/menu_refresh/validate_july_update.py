"""Validation gate for July_menu_update.json - the same bar the August
deliverable had to clear.

Streamed with ijson: the file is 584 MB and loading it whole would take several
GB of RAM on a box that has under one to spare.

Checks:
  DUPLICATES   source_id must be unique (unlike the full export, this file has
               one record per restaurant and no location records, so source_id
               IS the key here); and within a restaurant, no menu item may
               repeat the same (name, section, price).
  TEXT         emoji, mojibake, Arabic, CJK, control and zero-width characters
               in every shipped string.
  RULES        std_term / ingredients / price present on every item.

    python validate_july_update.py
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ijson

HERE = Path(__file__).resolve().parent
FILE = HERE / "data" / "July_menu_update.json"
REPORT = HERE / "data" / "July_menu_update_validation.json"

sys.stdout.reconfigure(encoding="utf-8")

try:
    from ftfy import fix_text
    HAVE_FTFY = True
except ImportError:
    HAVE_FTFY = False

EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF\U0000FE00-\U0000FE0F‍⃣]")
ARABIC = re.compile("[؀-ۿﭐ-﷽ﹰ-﻿]")
CJK = re.compile("[一-鿿぀-ゟ゠-ヿ가-힯]")
CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
INVIS = re.compile("[­​-‏‪-‮⁠-⁤﻿]")
MOJI = re.compile("Ã[\x80-\xbf]|â€|Â[\xa0-\xbf]|ï»¿")

# Ingredients do not apply to these - the authoritative NDJSON marks them
# ingredients_applicable=false with an empty list, and fabricating ingredients
# for a party candle is worse than an empty array. An empty list here is a
# DECISION, so the rule is "no FOOD item lacks ingredients", not "no item".
NON_FOOD = {"Marketing/Non-Standard Menu", "Service And Packaging"}


def main():
    counts = Counter()
    where = defaultdict(Counter)
    samples = defaultdict(list)
    ids = Counter()
    n_rest = n_item = 0
    dup_items = 0
    no_std = no_ing = no_price = 0
    no_ing_nonfood = 0
    prices = []

    print("=" * 72)
    print(f"  VALIDATE {FILE.name} ({FILE.stat().st_size/1e6:.0f} MB)")
    print("=" * 72)

    with open(FILE, "rb") as f:
        for rec in ijson.items(f, "item", use_float=True):
            n_rest += 1
            sid = rec.get("source_id")
            ids[sid] += 1
            seen = set()
            for it in rec.get("menu_items") or []:
                n_item += 1
                sig = (it.get("name"), it.get("section"), it.get("price"))
                if sig in seen:
                    dup_items += 1
                seen.add(sig)
                if not it.get("std_term"):
                    no_std += 1
                if not it.get("ingredients"):
                    if it.get("std_term") in NON_FOOD:
                        no_ing_nonfood += 1
                    else:
                        no_ing += 1
                if it.get("price") is None:
                    no_price += 1
                else:
                    prices.append(float(it["price"]))
                strs = [("name", it.get("name")), ("section", it.get("section")),
                        ("description", it.get("description")),
                        ("std_term", it.get("std_term"))]
                strs += [("ingredients", g) for g in it.get("ingredients") or []]
                for field, s in strs:
                    if not isinstance(s, str) or not s:
                        continue
                    for nm, hit in (("emoji", EMOJI.search(s)),
                                    ("arabic", ARABIC.search(s)),
                                    ("cjk", CJK.search(s)),
                                    ("control_chars", CTRL.search(s)),
                                    ("zero_width", INVIS.search(s)),
                                    ("mojibake_regex", MOJI.search(s))):
                        if hit:
                            counts[nm] += 1
                            where[nm][field] += 1
                            if len(samples[nm]) < 5:
                                samples[nm].append((sid, field, s[:60]))
                    if HAVE_FTFY and fix_text(s) != s:
                        counts["mojibake_ftfy"] += 1
                        where["mojibake_ftfy"][field] += 1
                        if len(samples["mojibake_ftfy"]) < 5:
                            samples["mojibake_ftfy"].append((sid, field, s[:60]))
            if n_rest % 4000 == 0:
                print(f"    {n_rest:,} restaurants ...", flush=True)

    dup_ids = sum(v - 1 for v in ids.values() if v > 1)
    print(f"\n  restaurants {n_rest:,} | menu entries {n_item:,}")

    fails = {}
    print("\n  --- DUPLICATES ---")
    print(f"    repeated source_id                 : {dup_ids:,}")
    print(f"    repeated (name,section,price) in one restaurant: {dup_items:,}")
    if dup_ids:
        fails["duplicate_source_id"] = dup_ids

    print("\n  --- TEXT INTEGRITY ---")
    for k in ("emoji", "mojibake_ftfy", "mojibake_regex", "arabic", "cjk",
              "control_chars", "zero_width"):
        v = counts[k]
        print(f"    {k:16} {v:>8,}" + ("" if v == 0 else "   <-- FAIL"))
        if v:
            fails[k] = v
            print(f"        fields: {dict(where[k].most_common(3))}")
            for sid, fl, s in samples[k][:2]:
                print(f"        e.g. [{sid}] {fl}: {s}")

    print("\n  --- RULES ---")
    print(f"    non-food items with no ingredients (expected): "
          f"{no_ing_nonfood:>8,}")
    for label, v in (("items without std_term", no_std),
                     ("FOOD items without ingredients", no_ing),
                     ("items without price", no_price)):
        print(f"    {label:30} {v:>8,}" + ("" if v == 0 else "   <-- FAIL"))
        if v:
            fails[label] = v
    if prices:
        prices.sort()
        neg = sum(1 for p in prices if p < 0)
        zero = sum(1 for p in prices if p == 0)
        print(f"    price: median {prices[len(prices)//2]:.2f} AED | "
              f"max {prices[-1]:,.0f} | zero {zero:,} | negative {neg:,}")
        if neg:
            fails["negative_price"] = neg

    print("\n" + "=" * 72)
    if fails:
        print(f"  {len(fails)} CHECK(S) FAILED: {fails}")
    else:
        print("  ALL CHECKS PASSED")
    print("=" * 72)

    REPORT.write_text(json.dumps({
        "restaurants": n_rest, "menu_entries": n_item,
        "duplicate_source_id": dup_ids,
        "duplicate_items_within_restaurant": dup_items,
        "text": {k: counts[k] for k in
                 ("emoji", "mojibake_ftfy", "mojibake_regex", "arabic", "cjk",
                  "control_chars", "zero_width")},
        "items_without_std_term": no_std,
        "food_items_without_ingredients": no_ing,
        "nonfood_items_without_ingredients": no_ing_nonfood,
        "items_without_price": no_price,
        "failed": fails,
    }, indent=2), encoding="utf-8")
    print(f"  -> {REPORT.name}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
