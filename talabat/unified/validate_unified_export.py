"""Gate for talabat_unified_*.jsonl - run before anything leaves the building.

It reads the OUTPUT, never the inputs. A check that consults the source data
would pass on an intention rather than on what was actually written; the point
here is to look at the bytes Tech will receive.

WHAT IT LEARNED FROM THE LAST GATE. validate_july_update.py let 140 null item
names through because its text loop only inspected values that were ALREADY
str - a null was skipped, not checked. Every field check below therefore starts
from "is it present and of the right type", and only then looks at content.

IDENTITY IS NOT source_id. Location records legitimately reuse their parent's
source_id, and 23 July records share (source_id, location.raw) as well - a
chain's main entry sitting at one of its own branch addresses. The record key
is (source_id, location.raw, is_verified_location).

    python validate_unified_export.py
    python validate_unified_export.py --file data/talabat_unified_202608.jsonl
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import orjson

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DEFAULT = DATA / "talabat_unified_202608.jsonl"
REPORT = DATA / "unified_validation.json"

sys.stdout.reconfigure(encoding="utf-8")

try:
    from ftfy import fix_text
    HAVE_FTFY = True
except ImportError:
    HAVE_FTFY = False

EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF\U0000FE00-\U0000FE0F‍⃣]")
ARABIC = re.compile("[؀-ۿﭐ-﷿ﹰ-﻿]")
CJK = re.compile("[一-鿿぀-ゟ゠-ヿ가-힯]")
CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
INVIS = re.compile("[­​-‏‪-‮⁠-⁤﻿]")
MOJI = re.compile("Ã[\x80-\xbf]|â€|Â[\xa0-\xbf]|ï»¿")

REQUIRED = ("source_name", "source_id", "name", "cuisine", "sub_cuisines",
            "key_cuisines", "restaurant_type", "outlet_type", "chain_type",
            "chain_id", "chain_locations_count", "currency", "location",
            "geo", "contact_phone", "website", "maps_url", "menu_items")
OPTIONAL = ("is_verified_location",)
ITEM_REQUIRED = ("name", "section", "description", "std_term", "price",
                 "ingredients", "is_popular")
SOFT_FIELDS = ("contact_phone", "website", "maps_url")
NON_FOOD = {"Marketing/Non-Standard Menu", "Service And Packaging"}


def main(args):
    path = Path(args.file) if args.file else DEFAULT
    print("=" * 78)
    print(f"  VALIDATE {path.name}  ({path.stat().st_size/1e6:.0f} MB)")
    print("=" * 78)

    fails, warns = {}, {}
    n_rec = n_item = 0
    bad_json = 0
    keys = Counter()
    sids = Counter()
    missing_field = Counter()
    wrong_type = Counter()
    item_missing = Counter()
    soft_null = Counter()
    text_hits = Counter()
    text_where = defaultdict(Counter)
    samples = defaultdict(list)
    no_price = neg_price = no_std = 0
    name_null = section_null = 0
    empty_menu = 0
    no_ing_food = no_ing_nonfood = 0
    term_of_name = {}
    ings_of_name = {}
    label_conflict_term = set()
    label_conflict_ing = set()
    chain_of_brand = defaultdict(set)
    prices = []

    with open(path, "rb") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rec = orjson.loads(line)
            except Exception:
                bad_json += 1
                if len(samples["bad_json"]) < 3:
                    samples["bad_json"].append(lineno)
                continue
            if not isinstance(rec, dict):
                bad_json += 1
                continue
            n_rec += 1

            for fld in REQUIRED:
                if fld not in rec:
                    missing_field[fld] += 1
            extra = set(rec) - set(REQUIRED) - set(OPTIONAL)
            if extra:
                wrong_type[f"extra:{sorted(extra)[0]}"] += 1

            sid = rec.get("source_id")
            sids[str(sid)] += 1
            if not isinstance(sid, str):
                wrong_type["source_id_not_str"] += 1
            if not isinstance(rec.get("chain_id"), int):
                wrong_type["chain_id_not_int"] += 1
            loc = rec.get("location")
            geo = rec.get("geo")
            if not isinstance(loc, dict):
                wrong_type["location_not_dict"] += 1
                loc = {}
            if not isinstance(geo, dict):
                wrong_type["geo_not_dict"] += 1
            keys[f"{sid}|{loc.get('raw') or ''}"
                 f"|{int(bool(rec.get('is_verified_location')))}"] += 1

            for fld in SOFT_FIELDS:
                if rec.get(fld) is None:
                    soft_null[fld] += 1

            nm = rec.get("name")
            if isinstance(nm, str) and nm and isinstance(rec.get("chain_id"), int):
                chain_of_brand[nm].add(rec["chain_id"])

            items = rec.get("menu_items")
            if not isinstance(items, list):
                wrong_type["menu_items_not_list"] += 1
                items = []
            if not items:
                empty_menu += 1

            strs = [("record.name", nm)]
            for k in ("cuisine", "restaurant_type", "outlet_type"):
                strs.append((f"record.{k}", rec.get(k)))
            for k in ("raw", "city", "area", "sublocality"):
                strs.append((f"location.{k}", loc.get(k)))

            for it in items:
                n_item += 1
                if not isinstance(it, dict):
                    wrong_type["item_not_dict"] += 1
                    continue
                for fld in ITEM_REQUIRED:
                    if fld not in it:
                        item_missing[fld] += 1
                inm = it.get("name")
                isec = it.get("section")
                if inm is None:
                    name_null += 1
                    if len(samples["item_name_null"]) < 4:
                        samples["item_name_null"].append((sid, it.get("price")))
                if isec is None:
                    section_null += 1
                p = it.get("price")
                if p is None:
                    no_price += 1
                elif isinstance(p, (int, float)):
                    prices.append(float(p))
                    if p < 0:
                        neg_price += 1
                term = it.get("std_term")
                if not term:
                    no_std += 1
                ings = it.get("ingredients")
                if not isinstance(ings, list):
                    wrong_type["ingredients_not_list"] += 1
                    ings = []
                if not ings:
                    if term in NON_FOOD:
                        no_ing_nonfood += 1
                    else:
                        no_ing_food += 1
                if isinstance(inm, str) and inm:
                    prev = term_of_name.setdefault(inm, term)
                    if prev != term:
                        label_conflict_term.add(inm)
                    sig = tuple(ings)
                    prevs = ings_of_name.setdefault(inm, sig)
                    if prevs != sig:
                        label_conflict_ing.add(inm)
                strs += [("item.name", inm), ("item.section", isec),
                         ("item.description", it.get("description")),
                         ("item.std_term", term)]
                strs += [("item.ingredients", g) for g in ings]

            for field, s in strs:
                if not isinstance(s, str) or not s:
                    continue
                for label, hit in (("emoji", EMOJI.search(s)),
                                   ("arabic", ARABIC.search(s)),
                                   ("cjk", CJK.search(s)),
                                   ("control_chars", CTRL.search(s)),
                                   ("zero_width", INVIS.search(s)),
                                   ("mojibake_regex", MOJI.search(s))):
                    if hit:
                        text_hits[label] += 1
                        text_where[label][field] += 1
                        if len(samples[label]) < 3:
                            samples[label].append((sid, field, s[:60]))
                if HAVE_FTFY and fix_text(s) != s:
                    text_hits["mojibake_ftfy"] += 1
                    text_where["mojibake_ftfy"][field] += 1
                    if len(samples["mojibake_ftfy"]) < 3:
                        samples["mojibake_ftfy"].append((sid, field, s[:60]))

            if n_rec % 5000 == 0:
                print(f"    {n_rec:,} records | {n_item:,} items", flush=True)

    print(f"\n  records {n_rec:,} | menu items {n_item:,}")
    print(f"  distinct source_id {len(sids):,} | distinct record keys {len(keys):,}")

    print("\n  --- STRUCTURAL ---")
    dup_keys = sum(v - 1 for v in keys.values() if v > 1)
    for label, v in (("unparseable lines", bad_json),
                     ("duplicate record keys", dup_keys),
                     ("records with empty menu", empty_menu)):
        print(f"    {label:34}{v:>10,}" + ("" if not v else "   <-- FAIL"))
        if v:
            fails[label] = v

    print("\n  --- SCHEMA ---")
    if missing_field:
        fails["missing_record_fields"] = dict(missing_field)
        print(f"    missing record fields: {dict(missing_field)}   <-- FAIL")
    else:
        print("    every record carries all 18 required fields")
    if item_missing:
        fails["missing_item_fields"] = dict(item_missing)
        print(f"    missing item fields  : {dict(item_missing)}   <-- FAIL")
    else:
        print("    every item carries all 7 required fields")
    if wrong_type:
        fails["type_errors"] = dict(wrong_type)
        print(f"    type errors          : {dict(wrong_type)}   <-- FAIL")
    else:
        print("    types conform")

    print("\n  --- D1 CONVENTION ---")
    for fld in SOFT_FIELDS:
        v = soft_null[fld]
        print(f"    {fld:16} still null {v:>8,}" + ("" if not v else "  <-- FAIL"))
        if v:
            fails[f"null_{fld}"] = v

    print("\n  --- MENU INTEGRITY ---")
    for label, v in (("items with null name", name_null),
                     ("items with null section", section_null),
                     ("items without price", no_price),
                     ("negative prices", neg_price),
                     ("items without std_term", no_std),
                     ("FOOD items without ingredients", no_ing_food)):
        print(f"    {label:34}{v:>10,}" + ("" if not v else "   <-- FAIL"))
        if v:
            fails[label] = v
    print(f"    {'non-food w/o ingredients (ok)':34}{no_ing_nonfood:>10,}")
    if prices:
        prices.sort()
        print(f"    price: median {prices[len(prices)//2]:.2f} | "
              f"max {prices[-1]:,.0f} | zero {sum(1 for p in prices if p==0):,}")

    print("\n  --- LABEL CONSISTENCY (the cross-cohort risk) ---")
    print(f"    distinct item names               {len(term_of_name):>10,}")
    for label, s in (("names with >1 std_term", label_conflict_term),
                     ("names with >1 ingredient set", label_conflict_ing)):
        print(f"    {label:34}{len(s):>10,}" + ("" if not s else "   <-- FAIL"))
        if s:
            fails[label] = len(s)
            print(f"        e.g. {sorted(s)[:3]}")
    split = {b: sorted(c) for b, c in chain_of_brand.items() if len(c) > 1}
    print(f"    brands split across chain_ids     {len(split):>10,}"
          + ("" if not split else "   <-- FAIL"))
    if split:
        fails["brands_split_across_chain_ids"] = len(split)
        print(f"        e.g. {list(split.items())[:2]}")

    print("\n  --- TEXT ---")
    for k in ("emoji", "mojibake_ftfy", "mojibake_regex", "arabic", "cjk",
              "control_chars", "zero_width"):
        v = text_hits[k]
        print(f"    {k:20}{v:>10,}" + ("" if not v else "   <-- FAIL"))
        if v:
            fails[k] = v
            print(f"        fields: {dict(text_where[k].most_common(3))}")
            for s in samples[k][:2]:
                print(f"        e.g. {s}")

    print("\n" + "=" * 78)
    if fails:
        print(f"  {len(fails)} CHECK(S) FAILED")
        for k, v in fails.items():
            print(f"     {k}: {v}")
    else:
        print("  ALL CHECKS PASSED")
    print("=" * 78)

    REPORT.write_text(json.dumps({
        "file": path.name, "records": n_rec, "menu_items": n_item,
        "distinct_source_id": len(sids), "distinct_record_keys": len(keys),
        "text": {k: text_hits[k] for k in
                 ("emoji", "mojibake_ftfy", "mojibake_regex", "arabic", "cjk",
                  "control_chars", "zero_width")},
        "nonfood_without_ingredients": no_ing_nonfood,
        "failed": fails,
    }, indent=2), encoding="utf-8")
    print(f"  -> {REPORT.name}")
    return 1 if fails else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--file")
    sys.exit(main(p.parse_args()))
