"""Step 7b - taxonomy for the 14 std_terms that final_std_terms_taxonomy.csv
does not cover.

They came in through tier 2 (the July export's 572-term vocabulary) and are
absent from the 582-term CSV, leaving taxonomy empty on 35,167 rows. Same
situation as the 8 terms in August_menu/map_std_terms.py MANUAL_TAXONOMY, which
Sagar confirmed on 2026-08-12.

Every assignment below follows a precedent already in the CSV; the precedent is
named on each line so it can be argued with rather than taken on trust. The
taxonomy vocabulary is closed - core_food, beverage, breakfast, dessert, combo,
side, addon, accompaniment, marketing/non-standard menu - so nothing new is
invented here.

THREE ARE JUDGEMENT CALLS, flagged below: Sugar, Seasonings, Gum. Changing one
later is a single UPDATE, so they are applied rather than left blocking.

    python patch_taxonomy_gaps.py --dry-run
    python patch_taxonomy_gaps.py
"""
import argparse
import sys
from pathlib import Path

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

sys.stdout.reconfigure(encoding="utf-8")

#  std_term                          taxonomy       precedent in the CSV
GAPS = {
    "Makhaniya Biscuit":              ("core_food",  "peanut biscuit -> core_food"),
    "Bean Starters":                  ("core_food",  "bean dish / non veg starter -> core_food"),
    "Kerala Vattichathu Curry":       ("core_food",  "curry (malay style) -> core_food"),
    "Spicy Dry":                      ("core_food",  "black pepper stir fry -> core_food"),
    "Filipino Mung Bean Shrimp Curry": ("core_food", "curry or special roast -> core_food"),
    "Meal/Juice":                     ("beverage",   "'meal + juice' -> beverage (exact analogue)"),
    "Pasta Or Continental Dish":      ("core_food",  "pasta -> core_food"),
    "Dry Fry":                        ("core_food",  "batter fry -> core_food"),
    "Garnishes":                      ("accompaniment", "garnish accompanies a dish"),
    "Retail & Misc":                  ("marketing/non-standard menu", "non-food retail catch-all"),
    "South Indian/Sri Lankan":        ("core_food",  "cuisine-style section, like other regional terms"),
    # ---- judgement calls ----
    "Sugar":                          ("addon",      "JUDGEMENT: condiment added to a drink; 'add on' -> addon"),
    "Seasonings":                     ("accompaniment", "JUDGEMENT: condiment, aligned with Garnishes"),
    "Gum":                            ("dessert",    "JUDGEMENT: 'candy' -> dessert in MANUAL_TAXONOMY"),
}


def main(args):
    cn = psycopg2.connect(**DB)
    cur = cn.cursor()
    print("=" * 72)
    print("  TAXONOMY GAP PATCH")
    print("=" * 72)
    total = 0
    try:
        for term, (taxo, why) in GAPS.items():
            cur.execute("""select count(*) from talabat_menu_items
                           where run_id=%s and std_term=%s
                             and (taxonomy is null or taxonomy='')""",
                        (RUN_ID, term))
            n = cur.fetchone()[0]
            total += n
            flag = "  <-- JUDGEMENT" if why.startswith("JUDGEMENT") else ""
            print(f"  {term[:32]:34} -> {taxo:28} {n:>7,}{flag}")
            print(f"      {why}")
            if not args.dry_run and n:
                cur.execute("""update talabat_menu_items set taxonomy=%s
                               where run_id=%s and std_term=%s
                                 and (taxonomy is null or taxonomy='')""",
                            (taxo, RUN_ID, term))
        print(f"\n  rows affected: {total:,}")

        if args.dry_run:
            cn.rollback()
            print("  --dry-run: nothing written")
            return 0

        cur.execute("""select count(*) from talabat_menu_items
                       where run_id=%s and (taxonomy is null or taxonomy='')""",
                    (RUN_ID,))
        left = cur.fetchone()[0]
        print(f"  rows still without taxonomy: {left:,}")
        if left:
            cn.rollback()
            print("  NOT zero - rolled back so the gap stays visible")
            return 1
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
