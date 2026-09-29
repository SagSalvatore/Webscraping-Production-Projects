"""Manual-review export for a refresh cohort's NEW menu items.

STANDING RULE - EVERY REVIEW EXPORT CARRIES THE FULL MENU ROW. Sagar, Sept 2026.
A reviewer cannot judge "is this std_term right" from a normalised item_key and
a label; they need what the restaurant actually wrote. September's worst row was
`Chikuzenni`, described "Simmered Chicken and Root Vegetables", sitting in a
Japanese restaurant's `Tanpin` section - labelled **pizza** at 1/5 agreement.
Obvious with the description, unjudgeable without it. Columns, in order:

    review_id · item_name · description · category · price_aed
    std_term · taxonomy · ingredients
    confidence · vote_agreement · method · auto_accepted
    menu_rows · restaurants · example_restaurant · example_area · example_url
    item_key · corrected_std_term · corrected_ingredients · notes

WHERE EACH FIELD COMES FROM, because no single file holds them all:
    description, price_aed   the SCRAPE (menu_items.jsonl). The delta rows do
                             not carry description at all.
    ingredients, std_term    the CONFORMED mapper output - what actually ships,
                             not the raw pre-conformance value.
    example_restaurant/area  the unified deliverable, keyed source_id=branch_id.
                             restaurant_status.jsonl looks like the obvious
                             source and is NOT: `name` and `area_name` are null
                             on all 22,269 of its rows.
    example_url              scrape_targets.

WHAT NEEDS A HUMAN: EVERY tier-2 (kNN) key, not just the flagged ones.
    tier0 prior_delivered  verbatim from what Tech already holds - not a guess,
                           and re-auditing it would re-open a human's decision
    tier1 exact            exact match to the human-reviewed reference
    tier2 knn              A GUESS, every one. ALL go to review.

Sagar, Sept 2026: "i want accuracy rather than guess work". The accept rule
(>=4/5 votes AND sim >=0.60) keeps 57.1% of keys at 93.8% measured accuracy -
a good bet, not a fact: ~660 auto-accepted keys on this cohort are wrong and
would ship unseen. So the rule is kept as `auto_accepted` and `vote_agreement`
to ORDER the work, never to decide what escapes it.

Sorted WORST-CONFIDENCE FIRST, so an abandoned review still covered the least
reliable calls. Measured on 2,000 held-out rows:
    5/5 -> 97.5%   4/5 -> 84.0%   3/5 -> 64.7%   2/5 -> 38.0%   1/5 -> 33.7%

    python export_refresh_review.py --data data/refresh_202609/stdterm
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UNIFIED = ROOT / "unified" / "data" / "talabat_unified_202608.jsonl"
sys.stdout.reconfigure(encoding="utf-8")

COLUMNS = ["review_id", "item_name", "description", "category", "price_aed",
           "std_term", "taxonomy", "ingredients",
           "confidence", "vote_agreement", "method", "auto_accepted",
           "menu_rows", "restaurants", "example_restaurant", "example_area",
           "example_url", "item_key",
           "corrected_std_term", "corrected_ingredients", "notes"]


def review_id(item_key: str) -> str:
    """Stable, Excel-safe. 'MK-' keeps it textual; sha256 keeps it deterministic
    so a returned verdict joins back even after a re-run."""
    return "MK-" + hashlib.sha256(item_key.encode("utf-8")).hexdigest()[:12]


def main(args):
    D = Path(args.data)
    if not D.is_absolute():
        D = HERE / D
    run_dir = D.parent
    rows_f = D / "menu_items_with_std_terms.jsonl"
    map_f = D / "std_term_mapping.json"
    scrape_items = run_dir / "scrape" / "menu_items.jsonl"
    targets = next(run_dir.glob("scrape_targets_*.jsonl"), None)
    out_csv = D / "new_mappings_review.csv"
    out_nd = D / "new_mappings_review.ndjson"

    print("=" * 74)
    print("  MANUAL-REVIEW EXPORT - new menu items from this refresh")
    print("=" * 74)
    for p in (rows_f, map_f):
        if not p.exists():
            print(f"  missing: {p}")
            return 1

    mapping = json.loads(map_f.read_text(encoding="utf-8"))
    guesses = {k: v for k, v in mapping.items() if v.get("method") == "knn"}
    method = Counter(v.get("method") for v in mapping.values())
    print(f"  item_keys mapped : {len(mapping):,}")
    for m, n in method.most_common():
        print(f"     {m:18} {n:>7,} keys"
              + ("   <- machine guesses, ALL reviewed" if m == "knn"
                 else "   <- not a guess, excluded"))

    # ---- the conformed rows: shipped ingredients, plus reach per key --------
    ship, rows_per_key, branches, key_cat, key_name = {}, Counter(), defaultdict(set), defaultdict(Counter), {}
    for line in open(rows_f, "rb"):
        if not line.strip():
            continue
        r = orjson.loads(line)
        k = r["item_key"]
        if k not in guesses:
            continue
        rows_per_key[k] += 1
        branches[k].add(r.get("branch_id"))
        key_cat[k][r.get("category") or ""] += 1
        key_name.setdefault(k, r.get("item_name") or k)
        ship.setdefault(k, r)
    print(f"  to review        : {len(guesses):,} keys "
          f"({sum(rows_per_key.values()):,} rows)")
    print(f"     of which the accept rule would have let through: "
          f"{sum(1 for v in guesses.values() if not v.get('review_required')):,} keys")

    # ---- description + price from the SCRAPE (the delta has no description) --
    desc, price = {}, {}
    if scrape_items.exists():
        for line in open(scrape_items, "rb"):
            if not line.strip():
                continue
            r = orjson.loads(line)
            k = r.get("item_key")
            if k not in guesses:
                continue
            if k not in desc and (r.get("description") or "").strip():
                desc[k] = r["description"].strip()
            price.setdefault(k, r.get("price_aed"))
        print(f"  descriptions found: {len(desc):,}/{len(guesses):,} keys "
              f"({len(desc)/max(len(guesses),1)*100:.1f}%)")
    else:
        print(f"  WARNING no scrape at {scrape_items} - description will be blank")

    # ---- example restaurant + area, keyed source_id = branch_id -------------
    # BOTH SIDES ARE COERCED TO str. The unified deliverable stores source_id as
    # a STRING; the scrape and delta store branch_id as an INT. Joined raw the
    # intersection is empty and every restaurant/area column ships blank without
    # raising - measured: 0 of 5,565 branches resolved.
    want = {str(b) for s in branches.values() for b in s}
    rest = {}
    if UNIFIED.exists():
        for line in open(UNIFIED, "rb"):
            if not line.strip():
                continue
            r = orjson.loads(line)
            sid = str(r.get("source_id"))
            if sid in want and sid not in rest:
                rest[sid] = (r.get("name") or "",
                             (r.get("location") or {}).get("area") or "")
        print(f"  branches resolved : {len(rest):,}/{len(want):,}")
        if not rest:
            print("     WARNING nothing joined - check the source_id/branch_id types")
    url = {}
    if targets and targets.exists():
        for line in open(targets, "rb"):
            if not line.strip():
                continue
            t = orjson.loads(line)
            b = str(t.get("branch_id"))
            if b in want:
                url.setdefault(b, t.get("url") or "")

    RANK = {"1/5": 0, "2/5": 1, "3/5": 2, "4/5": 3, "5/5": 4}
    recs = []
    for k, v in guesses.items():
        r = ship.get(k, {})
        ex = str(sorted(branches[k])[0]) if branches[k] else ""
        nm, ar = rest.get(ex, ("", ""))
        recs.append({
            "review_id": review_id(k),
            "item_name": key_name.get(k, k),
            "description": desc.get(k, ""),
            "category": key_cat[k].most_common(1)[0][0] if key_cat[k] else "",
            "price_aed": price.get(k, r.get("price_aed")),
            "std_term": v.get("std_term"),
            "taxonomy": v.get("taxonomy"),
            "ingredients": ", ".join(r.get("ingredients") or []),
            "confidence": v.get("confidence"),
            "vote_agreement": v.get("vote_agreement"),
            "method": v.get("method"),
            "auto_accepted": "no" if v.get("review_required") else "yes",
            "menu_rows": rows_per_key[k],
            "restaurants": len(branches[k]),
            "example_restaurant": nm,
            "example_area": ar,
            "example_url": url.get(ex, ""),
            "item_key": k,
            "corrected_std_term": "",     # <- the reviewer fills these three
            "corrected_ingredients": "",
            "notes": "",
        })
    # weakest agreement first, then blast radius: effort tracks risk, and an
    # abandoned review still covered the least reliable calls
    recs.sort(key=lambda x: (RANK.get(x["vote_agreement"], 9), -x["menu_rows"]))

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(recs)
    with open(out_nd, "wb") as f:
        for r in recs:
            f.write(orjson.dumps(r) + b"\n")

    print(f"\n  -> {out_csv.name}  ({len(recs):,} keys, "
          f"{sum(r['menu_rows'] for r in recs):,} rows)")
    print(f"  -> {out_nd.name}")
    filled = {c: sum(1 for r in recs if str(r[c] or "").strip())
              for c in ("description", "category", "price_aed",
                        "ingredients", "example_restaurant", "example_area",
                        "example_url")}
    print("\n  column coverage (blank here means the reviewer flies blind):")
    for c, n in filled.items():
        print(f"     {c:20} {n:>7,}/{len(recs):,}  {n/len(recs)*100:5.1f}%")

    ACC = {"5/5": "97.5%", "4/5": "84.0%", "3/5": "64.7%",
           "2/5": "38.0%", "1/5": "33.7%"}
    va = Counter(r["vote_agreement"] for r in recs)
    print("\n  queue order (weakest agreement first):")
    for k in ("1/5", "2/5", "3/5", "4/5", "5/5"):
        if va[k]:
            n = sum(r["menu_rows"] for r in recs if r["vote_agreement"] == k)
            auto = sum(1 for r in recs
                       if r["vote_agreement"] == k and r["auto_accepted"] == "yes")
            print(f"     {k}  {va[k]:>6,} keys  {n:>7,} rows  "
                  f"holdout {ACC[k]:>6}  ({auto:,} would have shipped unreviewed)")
    print("\n  head of the queue:")
    for r in recs[:6]:
        print(f"     {str(r['item_name'])[:26]:28} | {str(r['description'])[:38]:40} "
              f"| {str(r['category'])[:16]:18} -> {str(r['std_term'])[:16]}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data/refresh_202609/stdterm")
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
