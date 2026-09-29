"""Step 8 - ingredients for this month's items.

menu_item_ingredients survived the reload (it keys on (platform, branch_id,
item_key) with no FK to talabat_menu_items), so 2,176,348 rows are still there
and re-join. This only fills the gaps.

SOURCE is std_term_mapping_august.json, written by map_std_terms_august.py, so
ingredients come from exactly the same tier that produced the std_term:

    ndjson_reviewed -> manual    human-reviewed, authoritative
    july_export     -> keyword   what Tech already shipped against
    excel_exact     -> keyword   the labelled reference
    knn             -> ai        nearest neighbour carrying the winning term

Taking both fields from one tier matters: pulling std_term from one source and
ingredients from another would let an unchanged item acquire an ingredient list
that never went with its label.

TWO THINGS THAT WILL BITE OTHERWISE:

1. ingredient_name is an FK to ingredients_taxonomy (978 terms, Title Case).
   Our sources are lowercase, so every name is mapped back to the taxonomy's
   canonical casing. Anything still unmatched is REPORTED, never silently
   dropped - a dropped ingredient is invisible data loss.
2. Four real ingredients are missing from the taxonomy entirely
   (dumpling wrapper, pasta, sesame oil, tuna). They are added, with categories
   following existing precedent (Ramen/Udon Noodles -> Bakery and Cereal,
   Salmon -> Seafood, Canola/Coconut Oil -> Fats and Oils).

    python load_ingredients_august.py --dry-run
    python load_ingredients_august.py
"""
import argparse
import io
import json
import sys
from collections import Counter
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
MAPPING = DATA / "std_term_mapping_august.json"

DB = dict(host="localhost", port=5432, dbname="RestaurantIntelligence",
          user="postgres", password=PG_PASSWORD)
RUN_ID = "20260819_164725"

METHOD_MAP = {"ndjson_reviewed": "manual", "july_export": "keyword",
              "excel_exact": "keyword", "knn": "ai"}

NEW_TAXONOMY = [
    ("Dumpling Wrapper", "Bakery and Cereal"),
    ("Pasta", "Bakery and Cereal"),
    ("Sesame Oil", "Fats and Oils"),
    ("Tuna", "Seafood"),
]

sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    print("=" * 68)
    print(f"  INGREDIENTS  run_id={RUN_ID}")
    print("=" * 68)
    print(f"  mapping keys: {len(mapping):,}")

    cn = psycopg2.connect(**DB)
    cn.autocommit = False
    cur = cn.cursor()
    try:
        cur.execute("select ingredient_name, ingredient_category "
                    "from ingredients_taxonomy")
        taxo = {}
        cats = {}
        for name, cat in cur.fetchall():
            taxo[name.lower()] = name
            cats[name.lower()] = cat

        missing = [(n, c) for n, c in NEW_TAXONOMY if n.lower() not in taxo]
        if missing:
            print(f"\n  adding {len(missing)} terms to ingredients_taxonomy:")
            for n, c in missing:
                print(f"     {n:20} -> {c}")
                # normalized_name is a GENERATED column - inserting into it
                # raises GeneratedAlways. Postgres derives it from the name.
                cur.execute("""insert into ingredients_taxonomy
                                 (ingredient_name, ingredient_category, source)
                               values (%s,%s,'AUGUST_REFRESH')
                               on conflict do nothing""", (n, c))
                taxo[n.lower()] = n
                cats[n.lower()] = c

        # which (branch, item) already carry ingredients
        cur.execute("""select m.branch_id, m.item_key
                       from talabat_menu_items m
                       where m.run_id=%s
                         and not exists (select 1 from menu_item_ingredients i
                                         where i.branch_id = m.branch_id::text
                                           and i.item_key  = m.item_key)""",
                    (RUN_ID,))
        gaps = cur.fetchall()
        print(f"\n  (branch,item) pairs lacking ingredients: {len(gaps):,}")

        rows = []
        unresolved = Counter()
        no_ings = Counter()
        seen = set()
        now = datetime.now(timezone.utc).isoformat()
        for bid, key in gaps:
            m = mapping.get(key)
            if not m or not m.get("ingredients"):
                no_ings[key] += 1
                continue
            method = METHOD_MAP.get(m.get("method"), "keyword")
            conf = m.get("confidence") or 1.0
            conf = max(0.0, min(1.0, float(conf)))
            for ing in m["ingredients"]:
                canon = taxo.get(str(ing).strip().lower())
                if not canon:
                    unresolved[str(ing).strip().lower()] += 1
                    continue
                sig = (str(bid), key, canon)
                if sig in seen:
                    continue
                seen.add(sig)
                rows.append((str(bid), key, canon, cats[canon.lower()],
                             method, conf, now))

        print(f"  ingredient rows to insert : {len(rows):,}")
        print(f"  pairs with no ingredients in any tier: {sum(no_ings.values()):,}"
              f"  ({len(no_ings):,} distinct keys)")

        # Projected end state. The Excel tiers carry FREE-TEXT ingredients
        # ("whole spices", "discarded") that no taxonomy entry matches, so a
        # pair can have ingredients in the mapping and still end with none
        # after FK filtering. That gap is invisible unless measured here.
        covered = {(b, k) for b, k, *_ in rows}
        gap_set = {(str(b), k) for b, k in gaps}
        empty_after = len(gap_set - covered)
        cur.execute("select count(*) from talabat_menu_items where run_id=%s",
                    (RUN_ID,))
        tot_rows = cur.fetchone()[0]
        had = tot_rows - len(gaps)
        print(f"\n  PROJECTED COVERAGE of {tot_rows:,} item rows:")
        print(f"     already had ingredients      {had:>9,}  "
              f"({had/tot_rows*100:5.1f}%)")
        print(f"     gain from this load          {len(covered):>9,}  "
              f"({len(covered)/tot_rows*100:5.1f}%)")
        print(f"     STILL with none              {empty_after:>9,}  "
              f"({empty_after/tot_rows*100:5.1f}%)")
        print(f"     -> final coverage            "
              f"{(had+len(covered))/tot_rows*100:5.1f}%")
        if unresolved:
            print(f"\n  UNRESOLVED ingredient names (NOT inserted): "
                  f"{len(unresolved):,} distinct")
            for k, v in unresolved.most_common(10):
                print(f"     {v:>7,}  {k}")

        if args.dry_run:
            cn.rollback()
            print("\n  --dry-run: nothing written")
            return 0

        buf = io.StringIO()
        for r in rows:
            buf.write("\t".join(
                ["talabat"] + [str(x).replace("\\", "\\\\").replace("\t", " ")
                               .replace("\n", " ").replace("\r", " ")
                               for x in r]) + "\n")
        buf.seek(0)
        cur.copy_from(buf, "menu_item_ingredients", null="\\N",
                      columns=("platform", "branch_id", "item_key",
                               "ingredient_name", "ingredient_category",
                               "extraction_method", "confidence", "extracted_at"))
        print(f"\n  inserted {len(rows):,} rows")

        cur.execute("""select count(*) from talabat_menu_items m
                       where m.run_id=%s
                         and not exists (select 1 from menu_item_ingredients i
                                         where i.branch_id = m.branch_id::text
                                           and i.item_key  = m.item_key)""",
                    (RUN_ID,))
        still = cur.fetchone()[0]
        cur.execute("select count(*) from talabat_menu_items where run_id=%s",
                    (RUN_ID,))
        tot = cur.fetchone()[0]
        print(f"  rows still without any ingredient: {still:,} / {tot:,} "
              f"({still/tot*100:.1f}%)")

        cn.commit()
        print("\n  COMMITTED")
        return 0
    except Exception as exc:
        cn.rollback()
        print(f"\n  ERROR {type(exc).__name__}: {exc}\n  rolled back")
        return 1
    finally:
        cn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
