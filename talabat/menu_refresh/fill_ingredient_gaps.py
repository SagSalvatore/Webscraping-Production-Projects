"""Step 8b - ingredients for the items the direct tiers could not resolve.

THE PROBLEM. The kNN and Excel tiers carry FREE-TEXT ingredient strings, not
taxonomy terms: "discarded", "whole spices", "flour", "fresh fruit". 14,963
distinct strings, and only 242 were recoverable by singularising or splitting -
so this is not a casing issue like std_term was. They are correctly refused by
the ingredient_name FK, which is why those items end with no ingredients at all.
"discarded" must never reach Tech, and neither must an empty list.

THE FIX, per Sagar: learn what each std_term is actually made of from the two
sources that ARE clean - AUGUST_menu_items_FINALIZED_with_ingredients_v2.ndjson
and last month's talabat_export.json - and apply that to items whose own
ingredients could not be resolved.

    profile = ingredients appearing in >=30% of the items carrying that
              std_term, capped at 8, learned over 421,380 labelled items

That is defensible because std_term IS the dish class: every item labelled
"Burger" shares a core (lettuce, tomato, onion, burger bun, cheese, sauce).
It is a class-level default, not a claim about the specific dish, so it is
written with confidence = the measured share and NOT as 'manual'.

Only items with ZERO ingredient rows are touched. An item that resolved even
one real ingredient keeps exactly what it had - a measured ingredient always
beats a class default.

    python fill_ingredient_gaps.py --dry-run
    python fill_ingredient_gaps.py
"""
import argparse
import io
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_PASSWORD  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ROOT = HERE.parent
NDJSON = ROOT / "August_menu" / "AUGUST_menu_items_FINALIZED_with_ingredients_v2.ndjson"
EXPORT_FLAT = DATA / "export_july_flat.jsonl"
PROFILE_OUT = DATA / "std_term_canonical_ingredients.json"

DB = dict(host="localhost", port=5432, dbname="RestaurantIntelligence",
          user="postgres", password=PG_PASSWORD)
RUN_ID = "20260819_164725"

SHARE_MIN = 0.30
CAP = 8

sys.stdout.reconfigure(encoding="utf-8")


def build_profiles(taxo):
    """std_term -> [(ingredient, share)], learned from the two clean sources."""
    freq = defaultdict(Counter)
    items = Counter()

    with open(NDJSON, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            t, ings = r.get("final_std_terms"), r.get("final_ingredients")
            if t and ings:
                items[t] += 1
                for i in ings:
                    s = str(i).strip().lower()
                    if s in taxo:
                        freq[t][s] += 1

    seen = set()
    with open(EXPORT_FLAT, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            k = r["item_key"]
            if k in seen:
                continue
            seen.add(k)
            t, ings = r.get("std_term"), r.get("ingredients")
            if t and ings:
                items[t] += 1
                for i in ings:
                    s = str(i).strip().lower()
                    if s in taxo:
                        freq[t][s] += 1

    prof = {}
    for t, cnt in freq.items():
        n = items[t]
        keep = [(i, v / n) for i, v in cnt.most_common() if v / n >= SHARE_MIN][:CAP]
        if keep:
            prof[t] = keep
    return prof, sum(items.values())


def main(args):
    cn = psycopg2.connect(**DB)
    cur = cn.cursor()
    print("=" * 70)
    print("  INGREDIENT GAP FILL  (std_term class profiles)")
    print("=" * 70)

    cur.execute("select ingredient_name, ingredient_category from ingredients_taxonomy")
    taxo, cats = {}, {}
    for n, c in cur.fetchall():
        taxo[n.lower()] = n
        cats[n.lower()] = c

    prof, contributing = build_profiles(taxo)
    print(f"  profiles learned: {len(prof):,} std_terms "
          f"from {contributing:,} labelled items")
    PROFILE_OUT.write_text(json.dumps(
        {t: [i for i, _ in v] for t, v in prof.items()},
        ensure_ascii=False, indent=1), encoding="utf-8")

    # items with NO ingredient row at all
    cur.execute("""select m.branch_id, m.item_key, m.std_term
                   from talabat_menu_items m
                   where m.run_id=%s
                     and not exists (select 1 from menu_item_ingredients i
                                     where i.branch_id = m.branch_id::text
                                       and i.item_key  = m.item_key)""",
                (RUN_ID,))
    gaps = cur.fetchall()
    pairs = {(str(b), k): t for b, k, t in gaps}
    print(f"  item rows with zero ingredients : {len(gaps):,}")
    print(f"  distinct (branch,item) pairs    : {len(pairs):,}")

    rows = []
    no_profile = Counter()
    now = datetime.now(timezone.utc).isoformat()
    for (bid, key), term in pairs.items():
        p = prof.get(term)
        if not p:
            no_profile[term] += 1
            continue
        for ing, share in p:
            canon = taxo[ing]
            rows.append((bid, key, canon, cats[ing], "keyword",
                         round(min(max(share, 0.0), 1.0), 3), now))

    print(f"  rows to insert                  : {len(rows):,}")
    if no_profile:
        print(f"  pairs whose std_term has NO profile: {sum(no_profile.values()):,}"
              f"  ({len(no_profile)} terms)")
        for t, n in no_profile.most_common(5):
            print(f"     {t[:34]:36} {n:,}")

    if args.dry_run:
        cn.rollback()
        print("\n  --dry-run: nothing written")
        cn.close()
        return 0

    buf = io.StringIO()
    for r in rows:
        buf.write("\t".join(["talabat"] + [
            str(x).replace("\\", "\\\\").replace("\t", " ")
            .replace("\n", " ").replace("\r", " ") for x in r]) + "\n")
    buf.seek(0)
    try:
        cur.copy_from(buf, "menu_item_ingredients", null="\\N",
                      columns=("platform", "branch_id", "item_key",
                               "ingredient_name", "ingredient_category",
                               "extraction_method", "confidence", "extracted_at"))
        cur.execute("""select count(*) from talabat_menu_items m
                       where m.run_id=%s
                         and not exists (select 1 from menu_item_ingredients i
                                         where i.branch_id = m.branch_id::text
                                           and i.item_key  = m.item_key)""",
                    (RUN_ID,))
        left = cur.fetchone()[0]
        cur.execute("select count(*) from talabat_menu_items where run_id=%s",
                    (RUN_ID,))
        tot = cur.fetchone()[0]
        print(f"\n  inserted {len(rows):,}")
        print(f"  item rows STILL without ingredients: {left:,} / {tot:,} "
              f"({left/tot*100:.2f}%)")
        cn.commit()
        print("  COMMITTED")
        return 0
    except Exception as exc:
        cn.rollback()
        print(f"  ERROR {type(exc).__name__}: {exc} - rolled back")
        return 1
    finally:
        cn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
