"""Give the 80 delisted branches the CORRECTED std_term / ingredients, while
leaving their July menu exactly as it was.

THE SPLIT, per Sagar. item_key, source_id, description, menu category and price
are real Talabat data - and Tech's price-velocity module compares them month to
month from July onward, so they must not move. std_term and ingredients are OUR
derived enrichment; freezing those at July's values preserves nothing real, it
just keeps labels we have since proven wrong.

WHY IT MATTERS HERE. These 80 copy their menus verbatim out of the July export
and never touch the mapping, so they still carry July's originals - e.g.
`pepsi` -> "Fries + Pepsi" - sitting beside 3,485 identical rows correctly
labelled "Soft Drink". 561 of the 579 remaining name conflicts trace to exactly
this, over 1,101 rows.

COVERAGE. 2,465 of their 3,294 items (74.8%) have an item_key in this month's
mapping. The other 829 were never re-scraped, so nothing better exists and they
KEEP their July labels - stated rather than silently half-applied.

Only std_term and ingredients are touched. name, section, description, price
and is_popular are left byte-identical.

    python fix_delisted_labels.py --dry-run
    python fix_delisted_labels.py
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import ijson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
sys.path.insert(0, str(ROOT / "export"))
sys.path.insert(0, str(ROOT / "menu"))

from export_to_json import smart_title            # noqa: E402
from clean_menu_data import clean_item_key        # noqa: E402

FILE = DATA / "July_menu_update.json"
MAPPING = DATA / "std_term_mapping_consistent.json"
JULY_FLAT = DATA / "export_july_flat.jsonl"
STATUS = DATA / "scrape" / "restaurant_status.jsonl"
PROFILES = DATA / "std_term_canonical_ingredients.json"

NON_FOOD = {"Marketing/Non-Standard Menu", "Service And Packaging"}

sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    delisted = {str(json.loads(l)["branch_id"])
                for l in open(STATUS, encoding="utf-8")
                if l.strip() and json.loads(l).get("status") != "ok"}
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    profiles = json.loads(PROFILES.read_text(encoding="utf-8"))
    print("=" * 74)
    print(f"  RELABEL THE {len(delisted)} DELISTED BRANCHES")
    print("=" * 74)

    # (source_id, displayed name) -> the July item_key behind it. The file has
    # no item_key, so the name is the only way back - and it is reliable here
    # because it is built from the key by the same function.
    key_of = {}
    for line in open(JULY_FLAT, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        sid = str(r["branch_id"])
        if sid not in delisted:
            continue
        nm = smart_title(str(r.get("item_name") or ""))
        key_of.setdefault((sid, nm), r.get("item_key"))
    print(f"  (source_id, name) -> item_key entries: {len(key_of):,}")

    st = Counter()
    tmp = FILE.with_suffix(".tmp")
    with open(FILE, "rb") as src, open(tmp, "w", encoding="utf-8") as out:
        out.write("[\n")
        first = True
        n = 0
        for rec in ijson.items(src, "item", use_float=True):
            sid = rec.get("source_id")
            if sid in delisted:
                for it in rec.get("menu_items") or []:
                    k = key_of.get((sid, it.get("name")))
                    m = mapping.get(k) if k else None
                    if not m:
                        st["kept_july_label"] += 1
                        continue
                    new_term = m.get("std_term")
                    ings = list(m.get("ingredients") or [])
                    if not ings and new_term not in NON_FOOD:
                        ings = list(profiles.get(new_term) or [])
                    if it.get("std_term") != new_term:
                        st["std_term_corrected"] += 1
                    if [x.lower() for x in (it.get("ingredients") or [])] != \
                       [x.lower() for x in ings]:
                        st["ingredients_updated"] += 1
                    it["std_term"] = new_term
                    it["ingredients"] = ings
                    st["relabelled"] += 1
            if not first:
                out.write(",\n")
            json.dump(rec, out, ensure_ascii=False, indent=2)
            first = False
            n += 1
            if n % 5000 == 0:
                print(f"    {n:,} ...", flush=True)
        out.write("\n]\n")

    print(f"\n  records streamed      : {n:,}")
    for k, v in st.most_common():
        print(f"    {k:22} {v:>7,}")

    if args.dry_run:
        tmp.unlink(missing_ok=True)
        print("\n  --dry-run: original untouched")
        return 0

    tmp.replace(FILE)
    print(f"\n  -> {FILE.name} ({FILE.stat().st_size/1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
