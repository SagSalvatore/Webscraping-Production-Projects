"""Stage 5 - assign std_term + taxonomy (+ ingredients) to every menu item.

Reference: menu/Restaurant_Menu_With_Ingredients (2).xlsx
  355,726 labelled rows: Item Name | Category | Description | Ingredients |
  Std_Term (Reference).  `Item Name` uses the same normalisation as our
  `item_key` (lowercase, single-spaced), so it joins directly.

VOCABULARY: the Excel's 95 std_terms, chosen over the 572 in Postgres.
They agree on 94.1% of shared keys; where they differ the Excel is the
human-reviewed consolidation and the DB is machine-generated noise
("Spicy Loaded Fries" -> "spicy dry", "sakura bouquet" -> "shakarpare").

TWO TIERS, measured on a 2,000-row held-out sample before being chosen:

 1. EXACT  item_key == Item Name.  46,034 of our keys / 304,707 rows (60.2%).
           confidence 1.0. Carries Ingredients across for free - that was
           Phase 2 of the old Plan.md and was never run.

 2. kNN    TF-IDF char(2,3)+word(1,2), cosine, k=5 majority vote.
           76.5% top-1 accuracy on held-out.

Two approaches were measured and REJECTED, both worse than they look:

  * rapidfuzz token_set_ratio: 22 h AND wrong. It scores
    "butter chicken biryani" -> "butter" at 100, because a token subset is
    treated as a perfect match. That is precisely the flaw that put
    "spicy dry" in the existing DB.
  * gpt-4o-mini over the 95 terms: 42.5% - WORSE than kNN's 76.5%, and not
    from stupidity. Its answers are often defensible ("/hummus dish" ->
    appetizer) but the reference has its own conventions (-> hummus). kNN
    learns those conventions from the labelled data; world knowledge cannot.

The single biggest accuracy lever was including the menu CATEGORY in the
matching text: 56.0% -> 76.4%. Category is on every row we hold.

    python map_std_terms.py --dry-run
    python map_std_terms.py --limit 5000    quick pass
    python map_std_terms.py                 full run (~65 min)
"""
import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import openpyxl
from loguru import logger
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
def _argv_value(flag, default=None):
    """Read --flag VALUE (or --flag=VALUE) straight from argv.

    Needed BEFORE argparse runs because DATA and everything derived from it are
    module-level constants. A cohort that cannot repoint DATA would read
    August's menu_items.jsonl and overwrite August's outputs - the same hazard
    run2_collector's --out and restaurant_identifier's --cycle already fix.
    """
    import sys as _s
    if flag in _s.argv:
        i = _s.argv.index(flag)
        if i + 1 < len(_s.argv):
            return _s.argv[i + 1]
    for a in _s.argv:
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return default

DATA = Path(_argv_value("--data") or (HERE / "data"))
LOGS = HERE / "logs"
LOGS.mkdir(exist_ok=True)

XLSX = ROOT / "menu" / "Restaurant_Menu_With_Ingredients (2).xlsx"
TAXCSV = ROOT / "menu" / "final_std_terms_taxonomy.csv"
SRC = DATA / "menu_items_shipped.jsonl"
# optional tier-0 source: labels already delivered to Tech
PRIOR = Path(_argv_value("--prior") or (DATA / "prior_item_labels.json"))

OUT_ROWS = DATA / "menu_items_with_std_terms.jsonl"
OUT_MAP = DATA / "std_term_mapping.json"
OUT_REPORT = DATA / "mapping_report.json"
OUT_TAX = DATA / "std_term_taxonomy.json"
OUT_REVIEW = DATA / "std_term_needs_review.json"

# 8 Excel std_terms absent from final_std_terms_taxonomy.csv. Confirmed by
# Sagar 2026-08-12; they cover 49,222 of our rows (16.2%), so leaving them
# blank was not an option.
MANUAL_TAXONOMY = {
    "hot & cold beverages": "beverage",
    "curry": "core_food",
    "snack": "side",
    "cake": "dessert",
    "lassi/ butter milk": "beverage",
    "candy": "dessert",
    "add on": "addon",
    "others": "core_food",
}

KNN_K = 5
CHUNK = 250

# Accept rule, measured on 2,000 held-out rows. VOTE AGREEMENT (how many of the
# 5 neighbours picked the same term) predicts correctness far better than
# similarity does, and was not in the original Plan.md:
#     5/5 agree -> 97.5% accurate      2/5 agree -> 38.0%
#     4/5 agree -> 84.0%               1/5 agree -> 33.7%
#     3/5 agree -> 64.7%
# votes>=4 AND sim>=0.6 keeps 57.1% of items at 93.8% accuracy; everything else
# is flagged rather than shipped as fact. The flagged 42.9% carries ~85% of all
# the errors, which is what makes a targeted second pass affordable.
ACCEPT_VOTES = 4
ACCEPT_SIM = 0.60


# Reviewed taxonomy for std_terms the CSV does not cover, lifted VERBATIM from
# menu_refresh/patch_taxonomy_gaps.py (Sagar-confirmed 2026-08-12) rather than
# re-derived. Needed because tier 0 inherits the delivered 566-term vocabulary,
# of which 22 sit outside the 582-term CSV - and one of them,
# "Hot & Cold Beverages", is the single highest-volume term in the file.
# MANUAL_TAXONOMY above covers the other 8. Together: 22 of 22, none invented.
PRIOR_TAXONOMY = {
    "makhaniya biscuit": "core_food",
    "bean starters": "core_food",
    "kerala vattichathu curry": "core_food",
    "spicy dry": "core_food",
    "filipino mung bean shrimp curry": "core_food",
    "meal/juice": "beverage",
    "pasta or continental dish": "core_food",
    "dry fry": "core_food",
    "garnishes": "accompaniment",
    "retail & misc": "marketing/non-standard menu",
    "south indian/sri lankan": "core_food",
    "sugar": "addon",                 # JUDGEMENT: condiment added to a drink
    "seasonings": "accompaniment",    # JUDGEMENT: aligned with Garnishes
    "gum": "dessert",                 # JUDGEMENT: 'candy' -> dessert
}


def resolve_taxonomy(term, tax):
    """CSV first, then MANUAL_TAXONOMY, then the reviewed prior-vocabulary map."""
    t = (term or "").strip().lower()
    return (tax.get(t)
            or {k.lower(): v for k, v in MANUAL_TAXONOMY.items()}.get(t)
            or PRIOR_TAXONOMY.get(t, ""))


def load_reference():
    wb = openpyxl.load_workbook(XLSX, read_only=True)
    names, labels, cats, ings = [], [], [], []
    for r in wb["Menu Ingredients"].iter_rows(min_row=2, values_only=True):
        if not r[0]:
            continue
        names.append(str(r[0]).strip())
        cats.append(str(r[1] or "").strip())
        ings.append(str(r[3] or "").strip())
        labels.append(str(r[4] or "").strip())
    wb.close()
    return names, cats, ings, labels


def load_taxonomy(terms):
    tax = {}
    with open(TAXCSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            s = (row.get("Std_terms") or "").strip().lower()
            if s:
                tax[s] = (row.get("taxonomy") or "").strip()
    tax.update(MANUAL_TAXONOMY)
    missing = sorted({t for t in terms if t and t.lower() not in tax})
    return tax, missing


def main(args):
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
    logger.add(LOGS / "map_std_terms.log", level="DEBUG", encoding="utf-8", rotation="10 MB")

    rows = [json.loads(l) for l in open(SRC, encoding="utf-8") if l.strip()]
    logger.info(f"menu rows: {len(rows):,}")

    names, cats, ings, labels = load_reference()
    logger.info(f"reference: {len(names):,} labelled names | "
                f"{len({l for l in labels if l})} std_terms")

    tax, missing = load_taxonomy(set(labels))
    if missing:
        logger.error(f"std_terms with NO taxonomy: {missing}")
        return 1
    logger.info(f"taxonomy lookup: {len(tax):,} terms | all reference terms covered")

    ref = {}
    for n, c, ing, lab in zip(names, cats, ings, labels):
        if n not in ref:                       # first wins; file is key-unique
            ref[n] = (lab, ing)

    # ---- distinct keys, each with its most common category ----
    key_cat = defaultdict(Counter)
    key_rows = Counter()
    for r in rows:
        key_rows[r["item_key"]] += 1
        key_cat[r["item_key"]][r["category"]] += 1
    keys = sorted(key_rows)
    logger.info(f"distinct item_key: {len(keys):,}")

    # ---- TIER 0: labels Tech already holds -------------------------------
    # Reuse verbatim from the delivered unified file so an item name Tech has
    # already seen keeps EXACTLY the std_term and ingredients it was given.
    # This is what held July -> August at 310,061 shared keys, 100% identical.
    # Its taxonomy comes from the SAME row, not from `tax`: the unified
    # vocabulary (566 terms) is not the Excel one (95), so a `tax` lookup would
    # return "" for most of them.
    prior, canon = {}, {}
    if PRIOR.exists():
        import orjson as _oj
        _p = _oj.loads(PRIOR.read_bytes())
        prior = _p["labels"]
        canon = _p.get("canonical_casing") or {}
        logger.info(f"prior labels (from {PRIOR.name}): {len(prior):,} names "
                    f"| canonical casing: {len(canon):,} terms")

    mapping = {}
    tier0 = [k for k in keys if k in prior]
    for k in tier0:
        p = prior[k]
        mapping[k] = {"std_term": p["std_term"],
                      "taxonomy": resolve_taxonomy(p["std_term"], tax),
                      "ingredients": p.get("ingredients") or [],
                      "confidence": 1.0, "vote_agreement": "prior",
                      "method": "prior_delivered", "review_required": False}

    exact = [k for k in keys if k not in prior and k in ref]
    for k in exact:
        lab, ing = ref[k]
        mapping[k] = {"std_term": lab, "taxonomy": tax.get(lab.lower(), ""),
                      "ingredients": ing, "confidence": 1.0,
                      "vote_agreement": "exact",
                      "method": "exact", "review_required": False}
    todo = [k for k in keys if k not in prior and k not in ref]
    logger.info(f"  tier0 PRIOR : {len(tier0):,} keys "
                f"({sum(key_rows[k] for k in tier0):,} rows)  <- Tech already has these")
    logger.info(f"  tier1 EXACT : {len(exact):,} keys "
                f"({sum(key_rows[k] for k in exact):,} rows)")
    logger.info(f"  tier2 kNN   : {len(todo):,} keys "
                f"({sum(key_rows[k] for k in todo):,} rows)")

    if args.limit:
        todo = todo[: args.limit]
        logger.warning(f"--limit: kNN on {len(todo):,} keys only")
    if args.dry_run:
        logger.warning("--dry-run: no matching performed")
        return 0

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
        logger.info(f"  vectorised {Xr.shape[1]:,} features in {time.time()-t0:.0f}s")

        t0 = time.time()
        for i in range(0, Xq.shape[0], CHUNK):
            S = (Xq[i:i + CHUNK] @ Xr.T).toarray()
            top = np.argpartition(-S, KNN_K, axis=1)[:, :KNN_K]
            for j in range(S.shape[0]):
                order = top[j][np.argsort(-S[j, top[j]])]
                sim = float(S[j, order[0]])
                vote, agree = Counter(labels[o] for o in order).most_common(1)[0]
                # ingredients inherited from the nearest neighbour that actually
                # carries the winning term, so kNN rows get ingredients too -
                # not just the exact-match tier.
                ing = next((ings[o] for o in order if labels[o] == vote and ings[o]), "")
                k = todo[i + j]
                mapping[k] = {
                    "std_term": vote, "taxonomy": tax.get(vote.lower(), ""),
                    "ingredients": ing, "confidence": round(sim, 4),
                    "vote_agreement": f"{agree}/{KNN_K}",
                    "method": "knn",
                    "review_required": not (agree >= ACCEPT_VOTES and sim >= ACCEPT_SIM),
                }
            if (i // CHUNK) % 40 == 0 and i:
                el = time.time() - t0
                logger.info(f"    {i:,}/{len(todo):,} | {i/el:.0f} keys/s | "
                            f"ETA {(len(todo)-i)/(i/el)/60:.0f} min")
        logger.success(f"  kNN done in {(time.time()-t0)/60:.1f} min")

    # ---- canonicalise std_term CASING to the delivered vocabulary ----------
    #
    # The tiers disagree about case and both write into the same file. The Excel
    # reference is lowercase ("grill"); tier 0 copies the delivered vocabulary,
    # which is Title Case ("Grill"). Left alone this ships ONE term as TWO
    # labels and Tech's app treats them as different categories.
    #
    # Measured on the September refresh before this existed: 557 distinct
    # std_terms that are only 463 after case-folding - 94 terms present in both
    # casings, the biggest being Grill (3,379 keys), Shawarma (1,216) and Pizza
    # (960). It also broke the ingredient refill downstream, because
    # std_term_canonical_ingredients.json is keyed in the delivered casing:
    # 3,685 items were left with no ingredients that a profile could have filled.
    #
    # canonical_casing comes from talabat_unified_202608.jsonl - what Tech
    # already holds - so this can only ALIGN a spelling, never invent one. Terms
    # absent from it are left exactly as they are.
    if canon:
        before = len({v["std_term"] for v in mapping.values()})
        recased = 0
        for v in mapping.values():
            c = canon.get(str(v["std_term"]).strip().lower())
            if c and c != v["std_term"]:
                v["std_term"] = c
                recased += 1
        after = len({v["std_term"] for v in mapping.values()})
        logger.info(f"  std_term casing -> delivered vocabulary: "
                    f"{recased:,} keys recased | distinct {before} -> {after}")

    # ---- apply to rows ----
    unmapped = 0
    for r in rows:
        m = mapping.get(r["item_key"])
        if not m:
            r.update(std_term=None, taxonomy=None, std_term_confidence=None,
                     std_term_method="unknown", review_required=True)
            unmapped += 1
            continue
        r.update(std_term=m["std_term"], taxonomy=m["taxonomy"],
                 ingredients=m["ingredients"],
                 std_term_confidence=m["confidence"],
                 vote_agreement=m.get("vote_agreement",""),
                 std_term_method=m["method"], review_required=m["review_required"])

    assert all("std_term" in r for r in rows), "a row escaped mapping"

    with open(OUT_ROWS, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    OUT_MAP.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_TAX.write_text(json.dumps(
        {t: tax.get(t.lower(), "") for t in sorted({l for l in labels if l})},
        ensure_ascii=False, indent=2), encoding="utf-8")
    review = {k: v for k, v in mapping.items() if v["review_required"]}
    OUT_REVIEW.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    meth = Counter(r["std_term_method"] for r in rows)
    taxc = Counter(r["taxonomy"] for r in rows)
    stdc = Counter(r["std_term"] for r in rows)
    rep = {
        "rows": len(rows), "distinct_item_key": len(keys),
        "keys_exact": len(exact), "keys_knn": len(mapping) - len(exact),
        "keys_unmapped": len(keys) - len(mapping),
        "rows_unmapped": unmapped,
        "rows_by_method": dict(meth),
        "rows_needing_review": sum(1 for r in rows if r["review_required"]),
        "distinct_std_term": len([s for s in stdc if s]),
        "rows_by_taxonomy": dict(taxc),
        "rows_with_ingredients": sum(1 for r in rows if r.get("ingredients")),
        "knn_accuracy_holdout": 0.765, "vocabulary": "excel_95_terms",
    }
    OUT_REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.success(f"-> {OUT_ROWS.name} ({len(rows):,} rows)")
    logger.info(f"   method mix   : {dict(meth)}")
    logger.info(f"   std_terms    : {rep['distinct_std_term']}")
    logger.info(f"   with ingreds : {rep['rows_with_ingredients']:,}")
    logger.info(f"   need review  : {rep['rows_needing_review']:,}")
    logger.info(f"   taxonomy     : {dict(taxc.most_common())}")
    logger.info(f"-> {OUT_MAP.name}, {OUT_TAX.name}, {OUT_REVIEW.name}, {OUT_REPORT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--prior", help="prior_item_labels.json - labels already delivered to Tech, reused verbatim as tier 0")
    p.add_argument("--data", help="cohort data dir ""(default: August_menu/data). Repoints every input and output.")
    p.add_argument("--limit", type=int)
    sys.exit(main(p.parse_args()))
