"""Step 7 - std_term + taxonomy + ingredients for this month's rows.

The DELETE in load_to_postgres.py dropped June's std_term/taxonomy along with
June's rows, so every one of the 1,246,162 new rows needs a value. Ingredients
were NOT lost - menu_item_ingredients keys on (platform, branch_id, item_key)
with no FK to the item table, so all 2,176,348 rows survived and re-join.

FOUR TIERS, ordered by how much human judgement is behind each. Measured
coverage of the 1,246,162 rows before writing anything:

  1. NDJSON     AUGUST_menu_items_FINALIZED_with_ingredients_v2.ndjson, 86,996
                keys. Human-reviewed and authoritative - it wins wherever it
                has an opinion, including over the export.
  2. JULY EXPORT what Tech already holds, 344,337 keys with std_term. Covers
                93.0% of rows on its own. Using it keeps this month's labels
                identical to last month's for unchanged items, which is the
                whole point - a menu item that did not change must not silently
                acquire a different std_term. Taxonomy is not in the export, so
                it is looked up from the term (taxonomy is a function of
                std_term, not of the item).
  3. EXCEL EXACT Restaurant_Menu_With_Ingredients (2).xlsx, the same reference
                the June/August mapping used. Carries ingredients across free.
  4. kNN        TF-IDF char(2,3)+word(1,2), cosine, k=5 majority vote - the
                same parameters and accept thresholds as August_menu, imported
                rather than re-tuned. 76.5% top-1 on held-out.

Only ~84,738 rows (51,993 keys) fall past tier 2, so the expensive tier is
small this month.

    python map_std_terms_august.py --dry-run
    python map_std_terms_august.py
"""
import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import psycopg2
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_PASSWORD  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
sys.path.insert(0, str(ROOT / "August_menu"))

from map_std_terms import (          # noqa: E402 - reference loaders, not re-implemented
    ACCEPT_SIM,
    ACCEPT_VOTES,
    CHUNK,
    KNN_K,
    load_reference,
    load_taxonomy,
)

NDJSON = ROOT / "August_menu" / "AUGUST_menu_items_FINALIZED_with_ingredients_v2.ndjson"
EXPORT_FLAT = DATA / "export_july_flat.jsonl"
OUT_MAP = DATA / "std_term_mapping_august.json"
OUT_REPORT = DATA / "std_term_report_august.json"

DB = dict(host="localhost", port=5432, dbname="RestaurantIntelligence",
          user="postgres", password=PG_PASSWORD)
RUN_ID = "20260819_164725"

sys.stdout.reconfigure(encoding="utf-8")


def split_ings(s):
    if not s:
        return []
    if isinstance(s, list):
        return [str(x).strip().lower() for x in s if str(x).strip()]
    return [p.strip().lower() for p in str(s).replace(";", ",").split(",")
            if p.strip()]


def main(args):
    t_all = time.time()
    cn = psycopg2.connect(**DB)
    cur = cn.cursor()

    print("=" * 68)
    print(f"  STD_TERM MAPPING  run_id={RUN_ID}")
    print("=" * 68)

    cur.execute("""select item_key, menu_category, count(*)
                   from talabat_menu_items where run_id=%s
                   group by 1,2""", (RUN_ID,))
    key_rows, key_cat = Counter(), defaultdict(Counter)
    for k, c, n in cur.fetchall():
        key_rows[k] += n
        key_cat[k][c or ""] += n
    keys = list(key_rows)
    total_rows = sum(key_rows.values())
    print(f"  rows {total_rows:,} | distinct item_key {len(keys):,}")

    names, cats, ings, labels = load_reference()
    tax, missing = load_taxonomy(set(labels))
    print(f"  reference: {len(names):,} labelled rows | taxonomy {len(tax):,} terms")
    if missing:
        print(f"  WARNING std_terms with no taxonomy: {missing}")

    if args.reuse_mapping and OUT_MAP.exists():
        # the kNN tier costs ~17 min; do not pay it again to re-run a write
        mapping = json.loads(OUT_MAP.read_text(encoding="utf-8"))
        print(f"\n  --reuse-mapping: loaded {len(mapping):,} keys from "
              f"{OUT_MAP.name}, skipping all four tiers")
        return finish(args, cn, cur, mapping, keys, key_rows, total_rows)

    mapping = {}

    # ---- tier 1: authoritative NDJSON ------------------------------------
    nd = {}
    with open(NDJSON, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                if r.get("final_std_terms"):
                    nd[r["item_key"]] = r
    for k in keys:
        r = nd.get(k)
        if r:
            mapping[k] = {"std_term": r["final_std_terms"],
                          "taxonomy": r.get("final_taxonomy") or
                                      tax.get(str(r["final_std_terms"]).lower(), ""),
                          "ingredients": split_ings(r.get("final_ingredients")),
                          "confidence": 1.0, "method": "ndjson_reviewed",
                          "review_required": False}
    print(f"\n  tier1 NDJSON      {len(mapping):>7,} keys "
          f"({sum(key_rows[k] for k in mapping):>9,} rows)")

    # ---- tier 2: July export (consistency with what Tech holds) ----------
    # The export carries BOTH std_term and the resolved ingredient list per
    # item, so this tier supplies ingredients as well - no separate lookup and
    # no risk of an unchanged item getting a different ingredient list than the
    # one Tech already shipped against.
    exp = {}
    with open(EXPORT_FLAT, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                if r.get("std_term") and r["item_key"] not in exp:
                    exp[r["item_key"]] = (r["std_term"], r.get("ingredients") or [])
    n0 = len(mapping)
    exp_with_ings = 0
    for k in keys:
        if k not in mapping and k in exp:
            term, eings = exp[k]
            if eings:
                exp_with_ings += 1
            mapping[k] = {"std_term": term,
                          "taxonomy": tax.get(str(term).lower(), ""),
                          "ingredients": split_ings(eings), "confidence": 1.0,
                          "method": "july_export", "review_required": False}
    print(f"  tier2 JULY EXPORT {len(mapping)-n0:>7,} keys "
          f"({sum(key_rows[k] for k in list(mapping)[n0:]):>9,} rows)"
          f"  [{exp_with_ings:,} with ingredients]")

    # ---- tier 3: Excel exact --------------------------------------------
    ref = {}
    for n, c, i, l in zip(names, cats, ings, labels):
        if l:
            ref.setdefault(n.strip().lower(), (l, i))
    n1 = len(mapping)
    for k in keys:
        if k not in mapping and k in ref:
            l, i = ref[k]
            mapping[k] = {"std_term": l, "taxonomy": tax.get(l.lower(), ""),
                          "ingredients": split_ings(i), "confidence": 1.0,
                          "method": "excel_exact", "review_required": False}
    print(f"  tier3 EXCEL EXACT {len(mapping)-n1:>7,} keys")

    todo = [k for k in keys if k not in mapping]
    print(f"  tier4 kNN         {len(todo):>7,} keys "
          f"({sum(key_rows[k] for k in todo):>9,} rows)")

    if args.dry_run:
        print("\n  --dry-run: no kNN, nothing written")
        cn.close()
        return 0

    # ---- tier 4: kNN -----------------------------------------------------
    if todo:
        t0 = time.time()
        ref_text = [f"{n} || {c}" for n, c in zip(names, cats)]
        q_text = [f"{k} || {key_cat[k].most_common(1)[0][0]}" for k in todo]
        vc = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3), min_df=3,
                             sublinear_tf=True, dtype=np.float32)
        vw = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=3,
                             sublinear_tf=True, dtype=np.float32)
        Xr = normalize(hstack([vc.fit_transform(ref_text),
                               vw.fit_transform(ref_text)]).tocsr())
        Xq = normalize(hstack([vc.transform(q_text), vw.transform(q_text)]).tocsr())
        print(f"    vectorised {Xr.shape[1]:,} features in {time.time()-t0:.0f}s")
        t0 = time.time()
        for i in range(0, Xq.shape[0], CHUNK):
            S = (Xq[i:i + CHUNK] @ Xr.T).toarray()
            top = np.argpartition(-S, KNN_K, axis=1)[:, :KNN_K]
            for j in range(S.shape[0]):
                order = top[j][np.argsort(-S[j, top[j]])]
                sim = float(S[j, order[0]])
                vote, agree = Counter(labels[o] for o in order).most_common(1)[0]
                ing = next((ings[o] for o in order
                            if labels[o] == vote and ings[o]), "")
                mapping[todo[i + j]] = {
                    "std_term": vote, "taxonomy": tax.get(vote.lower(), ""),
                    "ingredients": split_ings(ing), "confidence": round(sim, 4),
                    "method": "knn",
                    "review_required": not (agree >= ACCEPT_VOTES
                                            and sim >= ACCEPT_SIM)}
            if (i // CHUNK) % 40 == 0 and i:
                print(f"    kNN {i:,}/{len(todo):,}  "
                      f"{time.time()-t0:.0f}s", flush=True)
        print(f"    kNN done in {time.time()-t0:.0f}s")

    return finish(args, cn, cur, mapping, keys, key_rows, total_rows)


def finish(args, cn, cur, mapping, keys, key_rows, total_rows):
    """Canonicalise, persist the mapping, write to Postgres, report.

    Separate from the tier logic so --reuse-mapping can re-run only this half:
    the kNN tier costs ~17 minutes and a failure in the write should never
    force it to be recomputed.
    """
    unmapped = [k for k in keys if k not in mapping]
    if unmapped:
        print(f"\n  WARNING {len(unmapped):,} keys still unmapped")

    # ---- canonicalise std_term CASING to the vocabulary Tech already holds --
    #
    # The Excel reference is lowercase ("appetizer"); the July export is Title
    # Case ("Appetizer"). Left alone this ships BOTH as distinct std_terms -
    # 664 instead of 570 - and Tech's app would treat them as different labels.
    # Measured: all 184 terms from the non-export tiers match an export term
    # case-insensitively and NOT ONE is genuinely new, so this only fixes
    # casing and cannot collapse two real terms into one.
    export_vocab = {}
    with open(EXPORT_FLAT, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                s = json.loads(line).get("std_term")
                if s:
                    export_vocab.setdefault(s.lower(), s)
    before = len({v["std_term"] for v in mapping.values()})
    recased = 0
    for v in mapping.values():
        canon = export_vocab.get(str(v["std_term"]).lower())
        if canon and canon != v["std_term"]:
            v["std_term"] = canon
            recased += 1
    after = len({v["std_term"] for v in mapping.values()})
    print(f"\n  std_term casing canonicalised to the July vocabulary:")
    print(f"     keys recased      {recased:>7,}")
    print(f"     distinct std_term {before} -> {after}")

    OUT_MAP.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")

    # ---- write std_term / taxonomy back ---------------------------------
    # NOTE: no `cn.autocommit = False` here. psycopg2 is transactional by
    # default and the SELECT above already opened a transaction, so setting it
    # raises "set_session cannot be used inside a transaction" - which is
    # exactly what threw away the first run's write after 1009s of kNN.
    print("\n  writing to talabat_menu_items ...")
    try:
        cur.execute("""create temp table stg_map (
                         item_key text, std_term text, taxonomy text,
                         confidence numeric, method text, review_required bool
                       ) on commit drop""")
        import io
        buf = io.StringIO()
        for k, v in mapping.items():
            def c(x):
                return (str(x).replace("\\", "\\\\").replace("\t", " ")
                        .replace("\n", " ").replace("\r", " "))
            buf.write("\t".join([c(k), c(v["std_term"]), c(v["taxonomy"]),
                                 str(v["confidence"]), v["method"],
                                 "t" if v["review_required"] else "f"]) + "\n")
        buf.seek(0)
        cur.copy_from(buf, "stg_map", null="\\N")
        cur.execute("create index on stg_map (item_key)")
        cur.execute("""update talabat_menu_items m
                       set std_term = s.std_term, taxonomy = s.taxonomy,
                           confidence = s.confidence, method = s.method,
                           review_required = s.review_required
                       from stg_map s
                       where m.run_id = %s and m.item_key = s.item_key""",
                    (RUN_ID,))
        updated = cur.rowcount

        cur.execute("""select count(*) from talabat_menu_items
                       where run_id=%s and std_term is null""", (RUN_ID,))
        still_null = cur.fetchone()[0]
        print(f"    updated {updated:,} rows | std_term still NULL: {still_null:,}")
        if still_null:
            raise AssertionError(f"{still_null:,} rows have no std_term - "
                                 "Sagar's requirement is zero")
        cn.commit()
        print("    COMMITTED")
    except Exception as exc:
        cn.rollback()
        print(f"    ERROR {type(exc).__name__}: {exc}  - rolled back")
        cn.close()
        return 1

    by_method = Counter(v["method"] for v in mapping.values())
    rows_by_method = Counter()
    for k, v in mapping.items():
        rows_by_method[v["method"]] += key_rows[k]
    review = sum(key_rows[k] for k, v in mapping.items() if v["review_required"])
    print("\n  --- by method (keys / rows) ---")
    for m, n in by_method.most_common():
        print(f"     {m:18} {n:>7,} / {rows_by_method[m]:>9,}")
    print(f"\n  rows flagged review_required: {review:,} "
          f"({review/total_rows*100:.1f}%)")
    print(f"  distinct std_terms: {len({v['std_term'] for v in mapping.values()}):,}")

    OUT_REPORT.write_text(json.dumps({
        "run_id": RUN_ID, "rows": total_rows, "keys": len(keys),
        "by_method_keys": dict(by_method), "by_method_rows": dict(rows_by_method),
        "review_required_rows": review,
        "distinct_std_terms": len({v["std_term"] for v in mapping.values()}),
    }, indent=2), encoding="utf-8")
    print(f"\n  -> {OUT_MAP.name}\n  -> {OUT_REPORT.name}")
    cn.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--reuse-mapping", action="store_true",
                   help="skip the four tiers and re-run only the DB write "
                        "from std_term_mapping_august.json")
    sys.exit(main(p.parse_args()))
