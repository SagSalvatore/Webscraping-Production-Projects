"""Export the flagged std_term decisions to CSV for manual review, and
reconcile the corrected file back afterwards.

    python export_review_csv.py            write the review CSV
    python export_review_csv.py --reconcile <corrected.csv>

GRAIN: one row per DISTINCT item_key (86,997), not per menu row (104,202).
One decision therefore fixes 1.2 menu rows on average.

WHY A HASH KEY: `item_key` is the real join key and is exported verbatim, but
it is not safe to rely on alone after a trip through a spreadsheet. Real keys in
this data include leading spaces and embedded quotes - e.g.
    '" butter chicken biryani"'
Excel strips leading whitespace, rewrites quoting, and will silently coerce
anything starting with = + - @ into a formula. `review_id` is md5(item_key), so
reconciliation still works even if the visible text is altered. Reconcile
matches on review_id FIRST and only falls back to item_key.

Rows are ordered by rows_affected desc, then worst vote_agreement first, so the
highest-impact and least-reliable decisions are at the top of the sheet.

To correct a row: type the new term into `corrected_std_term`. Leave it blank to
keep the current value. Taxonomy is derived automatically - do not edit it.
Allowed terms are listed in std_term_allowed_values.csv; anything else is
rejected at reconcile time rather than silently accepted.
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

ROWS = DATA / "menu_items_with_std_terms.jsonl"
REVIEW = DATA / "std_term_needs_review.json"
TAXJSON = DATA / "std_term_taxonomy.json"

OUT_CSV = DATA / "std_term_review.csv"
OUT_ALLOWED = DATA / "std_term_allowed_values.csv"
OUT_FIXED = DATA / "menu_items_with_std_terms_reviewed.jsonl"
OUT_APPLIED = DATA / "review_applied_log.csv"

sys.stdout.reconfigure(encoding="utf-8")
csv.field_size_limit(10 ** 7)

VOTE_ORDER = {"1/5": 0, "2/5": 1, "3/5": 2, "4/5": 3, "5/5": 4, "exact": 5}


def h(key: str) -> str:
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def main(args):
    rows = [json.loads(l) for l in open(ROWS, encoding="utf-8") if l.strip()]
    tax = json.loads(TAXJSON.read_text(encoding="utf-8"))

    if args.reconcile:
        return reconcile(args, rows, tax)

    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    n_rows = Counter()
    cats = defaultdict(Counter)
    for r in rows:
        if r["item_key"] in review:
            n_rows[r["item_key"]] += 1
            cats[r["item_key"]][r["category"]] += 1

    out = []
    for k, v in review.items():
        out.append({
            "review_id": h(k),
            "item_key": k,
            "menu_category": cats[k].most_common(1)[0][0] if cats[k] else "",
            "rows_affected": n_rows.get(k, 0),
            "current_std_term": v["std_term"],
            "current_taxonomy": v["taxonomy"],
            "confidence": v["confidence"],
            "vote_agreement": v.get("vote_agreement", ""),
            "corrected_std_term": "",          # <- reviewer fills this only
            "notes": "",
        })
    out.sort(key=lambda r: (-r["rows_affected"],
                            VOTE_ORDER.get(r["vote_agreement"], 9),
                            r["item_key"]))

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]), quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(out)

    allowed = sorted(tax)
    with open(OUT_ALLOWED, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(["std_term", "taxonomy"])
        for t in allowed:
            w.writerow([t, tax[t]])

    print(f"  distinct keys to review : {len(out):,}")
    print(f"  menu rows they cover    : {sum(r['rows_affected'] for r in out):,}")
    print(f"  vote mix: {dict(Counter(r['vote_agreement'] for r in out))}")
    print(f"\n  -> {OUT_CSV.name}  ({len(out):,} rows, QUOTE_ALL, utf-8-sig)")
    print(f"  -> {OUT_ALLOWED.name}  ({len(allowed)} allowed terms)")
    print("\n  top 5 by impact:")
    for r in out[:5]:
        print(f"    {r['rows_affected']:>3} rows  {r['item_key'][:34]:36} "
              f"{r['current_std_term']:18} {r['vote_agreement']}")
    return 0


def reconcile(args, rows, tax):
    src = Path(args.reconcile)
    if not src.is_absolute():
        src = DATA / src.name
    fixed = list(csv.DictReader(open(src, encoding="utf-8-sig")))
    print(f"  corrected file  : {src.name} ({len(fixed):,} rows)")

    by_hash, by_key = {}, {}
    for r in fixed:
        new = (r.get("corrected_std_term") or "").strip()
        if not new:
            continue
        if r.get("review_id"):
            by_hash[r["review_id"].strip()] = new
        if r.get("item_key"):
            by_key[r["item_key"]] = new
    print(f"  corrections given: {len(by_hash) or len(by_key):,}")

    bad = sorted({v for v in list(by_hash.values()) + list(by_key.values())
                  if v.lower() not in tax})
    if bad:
        print(f"\n  REJECTED - not in the allowed 95 terms: {bad[:10]}")
        print("  Fix these or they will be skipped. Nothing written.")
        return 1

    applied, log = 0, []
    matched_h = matched_k = 0
    for r in rows:
        k = r["item_key"]
        new = by_hash.get(h(k))
        if new:
            matched_h += 1
        else:
            new = by_key.get(k)
            if new:
                matched_k += 1
        if not new or new == r["std_term"]:
            continue
        log.append({"item_key": k, "review_id": h(k), "from": r["std_term"],
                    "to": new, "from_taxonomy": r["taxonomy"],
                    "to_taxonomy": tax.get(new.lower(), "")})
        r["std_term"] = new
        r["taxonomy"] = tax.get(new.lower(), "")
        r["std_term_method"] = "manual"
        r["std_term_confidence"] = 1.0
        r["review_required"] = False
        applied += 1

    assert all(r.get("std_term") for r in rows), "a row lost its std_term"
    with open(OUT_FIXED, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if log:
        with open(OUT_APPLIED, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(log[0]), quoting=csv.QUOTE_ALL)
            w.writeheader(); w.writerows(log)

    print(f"  matched by review_id : {matched_h:,}   by item_key fallback: {matched_k:,}")
    print(f"  menu rows updated    : {applied:,}")
    print(f"  still flagged        : {sum(1 for r in rows if r.get('review_required')):,}")
    print(f"\n  -> {OUT_FIXED.name}")
    print(f"  -> {OUT_APPLIED.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--reconcile", help="corrected CSV to apply back")
    sys.exit(main(p.parse_args()))
