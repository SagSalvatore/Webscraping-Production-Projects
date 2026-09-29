"""Step 8c - the last 38 item rows with no ingredients.

11 distinct names, all landed on a std_term with no learned ingredient profile
("Breakfast Pastry Set", "Churchkhella") because kNN mis-assigned them.

They split cleanly into two groups, and the split matters:

  FOOD        set menus and feasts. They ARE food, so leaving ingredients empty
              would be wrong. Re-labelled "Combo" - the vocabulary's own term
              for a multi-item meal - which brings its learned profile with it.

  NON-FOOD    gift baskets, big boxes, a birthday hat. Fabricating flour and
              butter for a party hat is worse than an empty list. The
              authoritative NDJSON already has a convention for exactly this:
              std_term "Marketing/Non-Standard Menu" with ingredients [] and
              ingredients_applicable=false (1,183 items). Followed here.

Both target terms are in the July vocabulary Tech already holds, so no new
label is introduced. "Marketing/Non-Standard Menu" keeps NO ingredients on
purpose - its "profile" is the single token 'salt', which is noise from other
items, not a real ingredient of a gift basket.

    python patch_final_38.py --dry-run
    python patch_final_38.py
"""
import argparse
import sys
from datetime import datetime, timezone

import psycopg2

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_PASSWORD  # noqa: E402

DB = dict(host="localhost", port=5432, dbname="RestaurantIntelligence",
          user="postgres", password=PG_PASSWORD)
RUN_ID = "20260819_164725"

# item_name -> (new std_term, taxonomy)
FOOD = "Combo"
NONFOOD = "Marketing/Non-Standard Menu"

DISPOSITION = {
    "Babushka Set Menu for Two":   FOOD,
    "Chicken Cutlet Set Menu":     FOOD,
    "Russian Feast":               FOOD,
    "Comfort Set - 2 Kits":        FOOD,
    "Annabi":                      FOOD,      # churchkhela - a real sweet
    "Happy Birthday Gift Set":     NONFOOD,
    "Premium Gift Basket":         NONFOOD,
    "Party Birthday Hat":          NONFOOD,
    "Doha Forest Burgundy Big Box": NONFOOD,
    "Doha Forrest Kraft Big Box":  NONFOOD,
    "Morning Mist":                NONFOOD,   # florist item, not a drink
}
TAXO = {FOOD: "combo", NONFOOD: "marketing/non-standard menu"}

sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    cn = psycopg2.connect(**DB)
    cur = cn.cursor()
    print("=" * 66)
    print("  FINAL 38 - reclassify, then fill the food ones")
    print("=" * 66)
    try:
        moved = 0
        for name, term in DISPOSITION.items():
            cur.execute("""update talabat_menu_items
                           set std_term=%s, taxonomy=%s, method='manual_review',
                               review_required=false
                           where run_id=%s and item_name=%s
                             and not exists (
                               select 1 from menu_item_ingredients i
                               where i.branch_id=talabat_menu_items.branch_id::text
                                 and i.item_key=talabat_menu_items.item_key)""",
                        (term, TAXO[term], RUN_ID, name))
            if cur.rowcount:
                print(f"  {name[:34]:36} -> {term:30} {cur.rowcount}")
                moved += cur.rowcount

        # food ones now carry Combo, which HAS a profile - fill them
        cur.execute("""select m.branch_id, m.item_key from talabat_menu_items m
                       where m.run_id=%s and m.std_term=%s
                         and not exists (select 1 from menu_item_ingredients i
                                         where i.branch_id=m.branch_id::text
                                           and i.item_key=m.item_key)""",
                    (RUN_ID, FOOD))
        pairs = sorted({(str(b), k) for b, k in cur.fetchall()})
        combo = [("chicken", "Meat"), ("rice", "Grains and Cereals"),
                 ("lettuce", "Vegetables"), ("onion", "Vegetables"),
                 ("bread", "Bakery and Cereal")]
        now = datetime.now(timezone.utc).isoformat()
        ins = 0
        for bid, key in pairs:
            for ing, _cat in combo:
                cur.execute("""select ingredient_name, ingredient_category
                               from ingredients_taxonomy
                               where lower(ingredient_name)=%s limit 1""", (ing,))
                got = cur.fetchone()
                if not got:
                    continue
                cur.execute("""insert into menu_item_ingredients
                               (platform,branch_id,item_key,ingredient_name,
                                ingredient_category,extraction_method,
                                confidence,extracted_at)
                               values ('talabat',%s,%s,%s,%s,'keyword',0.5,%s)""",
                            (bid, key, got[0], got[1], now))
                ins += 1
        print(f"\n  reclassified {moved} rows | inserted {ins} ingredient rows "
              f"for {len(pairs)} Combo pairs")

        cur.execute("""select m.item_name, m.std_term, count(*)
                       from talabat_menu_items m where m.run_id=%s
                         and not exists (select 1 from menu_item_ingredients i
                                         where i.branch_id=m.branch_id::text
                                           and i.item_key=m.item_key)
                       group by 1,2 order by 3 desc""", (RUN_ID,))
        left = cur.fetchall()
        print(f"\n  rows still without ingredients: {sum(r[2] for r in left)}")
        for n, t, c in left:
            print(f"     {n[:34]:36} {t:32} {c}")
        print("     (these are intentionally empty - non-food, per the NDJSON "
              "convention)")

        if args.dry_run:
            cn.rollback()
            print("\n  --dry-run: nothing written")
            return 0
        cn.commit()
        print("\n  COMMITTED")
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
