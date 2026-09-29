"""Make every identical item name carry one std_term and one ingredient list
across BOTH cohorts, arbitrated by the TF-IDF kNN.

THE PROBLEM. 17,403 item names carry a different std_term in the July half of
the unified file than in the August half, plus 245 that disagree within August:

    Double Quarter Pounder With Cheese   july=Cheese Tart  august=Burger
    Happy Meal 4pcs Chicken McNuggets    july=Pineapple    august=Grill
    Tumbler 16 Oz Stainless Steel        july=Gum          august=Marketing/...

They were labelled by different pipelines and never reconciled: July's are 93%
inherited verbatim from the July export, August's came from exact-match or kNN
against the human-reviewed Excel. Separately the two were merely inconsistent;
in ONE file a category filter on "Burgers" now returns August's Quarter Pounders
and not July's.

WHY NOT WINNER-TAKES-MOST. That is what enforce_name_consistency.py does, and it
is actively wrong here: July carries 2.5x August's rows, so July wins nearly
every tie and pushes `Cheese Tart` INTO August, degrading the better labels.
The rule that was right within one cohort inverts across two.

THE RULE HERE. The kNN arbitrates between the two labels that already exist - it
never invents a third:

    kNN agrees with July    -> July's label wins
    kNN agrees with August  -> August's label wins
    kNN agrees with neither -> August wins (better provenance), counted apart

ONE ANSWER PER NAME, BY CONSTRUCTION. The kNN keys on `name || category`, so the
same name under two categories could otherwise resolve two ways. Each distinct
name is therefore queried ONCE, using the category carrying the most rows, and
that single answer applies to every instance of the name.

Ingredients follow the winning side, never the kNN's neighbour - the winning
cohort's ingredient list was built with its label and stays consistent with it.

    python resolve_label_conflicts.py --dry-run    # measure, write nothing
    python resolve_label_conflicts.py
"""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import ijson
import numpy as np
import orjson
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
sys.path.insert(0, str(ROOT / "August_menu"))

from map_std_terms import load_reference, KNN_K, CHUNK   # noqa: E402

UNIFIED = DATA / "talabat_unified_202608.jsonl"
JULY_EXPORT = ROOT / "export" / "talabat_export.json"
OUT = DATA / "label_conflict_fixes.json"
REPORT = DATA / "label_conflict_report.json"
KNN_CACHE = DATA / "label_conflict_knn_cache.json"

sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    t_all = time.time()
    print("=" * 78)
    print("  RESOLVE LABEL CONFLICTS  (kNN arbitrates between July and August)")
    print("=" * 78)

    print("\n  1/5  cohort membership ...", flush=True)
    july_sids = set()
    for r in ijson.items(open(JULY_EXPORT, "rb"), "item", use_float=True):
        july_sids.add(str(r["source_id"]))
    print(f"       july source_ids {len(july_sids):,}")

    # name -> cohort -> {(std_term, ingredients_tuple): rows}
    print("  2/5  scanning the unified file ...", flush=True)
    per_name = defaultdict(lambda: defaultdict(Counter))
    cat_of = defaultdict(Counter)
    n = 0
    for line in open(UNIFIED, "rb"):
        rec = orjson.loads(line)
        coh = "july" if str(rec["source_id"]) in july_sids else "august"
        for it in rec["menu_items"]:
            nm = it.get("name")
            if not nm:
                continue
            sig = (it.get("std_term"), tuple(it.get("ingredients") or []))
            per_name[nm][coh][sig] += 1
            cat_of[nm][it.get("section") or ""] += 1
        n += 1
        if n % 5000 == 0:
            print(f"       {n:,} records ...", flush=True)

    # a name is in conflict when more than one (term, ingredients) exists for it
    conflicted = {}
    for nm, by_coh in per_name.items():
        sigs = set()
        for c in by_coh.values():
            sigs |= set(c)
        if len(sigs) > 1:
            conflicted[nm] = by_coh
    term_conflict = sum(
        1 for nm, d in conflicted.items()
        if len({s[0] for c in d.values() for s in c}) > 1)
    print(f"\n       distinct names        : {len(per_name):,}")
    print(f"       names in conflict     : {len(conflicted):,}")
    print(f"         differing std_term  : {term_conflict:,}")
    print(f"         ingredients only    : {len(conflicted)-term_conflict:,}")

    # ---- 3. kNN, once per conflicted name ------------------------------
    # The kNN is ~16 min for 44,630 names. Cached so a failure in stage 4 or a
    # re-run to adjust the arbitration rule costs seconds instead of repeating
    # it - the same lesson as --reuse-mapping in map_std_terms_august.py, where
    # a transaction error threw away 1,009s of completed work.
    todo = sorted(conflicted)
    knn_term, knn_votes = {}, {}
    if KNN_CACHE.exists() and not args.refresh_knn:
        cached = json.loads(KNN_CACHE.read_text(encoding="utf-8"))
        if set(cached.get("names", [])) == set(todo):
            knn_term = cached["term"]
            knn_votes = cached["votes"]
            print(f"\n  3/5  kNN: reusing {len(knn_term):,} cached predictions "
                  f"({KNN_CACHE.name})")
        else:
            print("\n  3/5  kNN cache is for a different name set - recomputing")

    if not knn_term:
        print("\n  3/5  kNN over the reviewed reference ...", flush=True)
        names, cats, ings, labels = load_reference()
        print(f"       reference {len(names):,} labelled rows")
        ref_text = [f"{a} || {b}" for a, b in zip(names, cats)]
        q_text = [f"{nm} || {cat_of[nm].most_common(1)[0][0]}" for nm in todo]

        t0 = time.time()
        vc = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3), min_df=3,
                             sublinear_tf=True, dtype=np.float32)
        vw = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=3,
                             sublinear_tf=True, dtype=np.float32)
        Xr = normalize(hstack([vc.fit_transform(ref_text),
                               vw.fit_transform(ref_text)]).tocsr())
        Xq = normalize(hstack([vc.transform(q_text),
                               vw.transform(q_text)]).tocsr())
        print(f"       vectorised {Xr.shape[1]:,} features in "
              f"{time.time()-t0:.0f}s")

        t0 = time.time()
        for i in range(0, Xq.shape[0], CHUNK):
            S = (Xq[i:i + CHUNK] @ Xr.T).toarray()
            top = np.argpartition(-S, KNN_K, axis=1)[:, :KNN_K]
            for j in range(S.shape[0]):
                order = top[j][np.argsort(-S[j, top[j]])]
                vote, agree = Counter(labels[o] for o in order).most_common(1)[0]
                nm = todo[i + j]
                knn_term[nm] = vote
                knn_votes[nm] = int(agree)
            if (i // CHUNK) % 20 == 0 and i:
                el = time.time() - t0
                print(f"       {i:,}/{len(todo):,} | ETA "
                      f"{(len(todo)-i)/(i/el)/60:.1f} min", flush=True)
        print(f"       kNN done in {(time.time()-t0)/60:.1f} min")
        KNN_CACHE.write_text(json.dumps(
            {"names": todo, "term": knn_term, "votes": knn_votes},
            ensure_ascii=False), encoding="utf-8")
        print(f"       cached -> {KNN_CACHE.name}")

    # ---- 4. arbitrate ---------------------------------------------------
    print("\n  4/5  arbitrating ...")
    st = Counter()
    fixes = {}
    examples = []

    def dominant(counter):
        """The (term, ingredients) carrying the most rows in that cohort."""
        return counter.most_common(1)[0][0] if counter else None

    def norm(s):
        """Compare terms case-insensitively.

        The kNN returns labels verbatim from the Excel reference, which is
        LOWERCASE ('sandwich', 'hot & cold beverages'), while the deliverable
        carries Title Case ('Sandwich', 'Hot & Cold Beverages') because
        smart_title runs at export. Comparing raw made every arbitration fall
        through to the default: 44,585 of 44,630 'backs neither'. The shipped
        casing is always the winning cohort's, never the kNN's.
        """
        return (s or "").strip().casefold()

    for nm, by_coh in conflicted.items():
        j = dominant(by_coh.get("july"))
        a = dominant(by_coh.get("august"))
        k = norm(knn_term.get(nm))
        if j and a:
            if k == norm(j[0]) and k != norm(a[0]):
                win, why = j, "knn_backs_july"
            elif k == norm(a[0]) and k != norm(j[0]):
                win, why = a, "knn_backs_august"
            elif k == norm(j[0]) == norm(a[0]):
                # same term, ingredient lists differ - take the larger list,
                # an empty or shorter list is a gap not a decision
                win = j if len(j[1]) >= len(a[1]) else a
                why = "same_term_richer_ingredients"
            else:
                win, why = a, "knn_backs_neither_august_default"
        else:
            # single-cohort conflict (the 245 inside August): the kNN picks
            # among that cohort's own competing labels
            only = by_coh.get("august") or by_coh.get("july")
            cands = list(only)
            match = [c for c in cands if norm(c[0]) == k]
            win = match[0] if match else only.most_common(1)[0][0]
            why = "within_cohort_knn" if match else "within_cohort_most_rows"
        st[why] += 1
        fixes[nm] = {"std_term": win[0], "ingredients": list(win[1])}
        if why in ("knn_backs_july", "knn_backs_august") and len(examples) < 14:
            examples.append((nm, j[0], a[0], k, why))

    print(f"\n       {'outcome':38}{'names':>9}")
    for k_, v in st.most_common():
        print(f"       {k_:38}{v:>9,}")
    tot_jа = st["knn_backs_july"] + st["knn_backs_august"]
    if tot_jа:
        print(f"\n       of the {tot_jа:,} the kNN decided: "
              f"July {st['knn_backs_july']/tot_jа*100:.1f}% | "
              f"August {st['knn_backs_august']/tot_jа*100:.1f}%")
    print("\n  examples:")
    for nm, j, a, k, why in examples:
        mark = "J" if why == "knn_backs_july" else "A"
        print(f"     {nm[:34]:36} july={str(j)[:16]:18} aug={str(a)[:16]:18} "
              f"knn={str(k)[:16]:18} -> {mark}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    print("\n  5/5  writing sidecar ...")
    OUT.write_text(json.dumps({
        "generated_for": UNIFIED.name,
        "rule": "kNN arbitrates between the two existing labels; never invents "
                "a third. Ties and no-match default to August (better "
                "provenance). One query per name using its dominant category, "
                "so exactly one answer per name.",
        "names_in_conflict": len(conflicted),
        "outcomes": dict(st),
        "fixes": fixes,
    }, ensure_ascii=False), encoding="utf-8")
    REPORT.write_text(json.dumps({
        "distinct_names": len(per_name),
        "names_in_conflict": len(conflicted),
        "std_term_conflicts": term_conflict,
        "outcomes": dict(st),
    }, indent=2), encoding="utf-8")
    print(f"  -> {OUT.name}  ({len(fixes):,} names)")
    print(f"  -> {REPORT.name}")
    print(f"  elapsed {(time.time()-t_all)/60:.1f} min")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--refresh-knn", action="store_true",
                   help="ignore the cached kNN predictions and recompute")
    sys.exit(main(p.parse_args()))
