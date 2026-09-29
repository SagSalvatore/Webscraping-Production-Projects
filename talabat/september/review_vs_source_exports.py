"""September new brands vs the SOURCE exports, with EXACT cuisine matching.

WHY THE SOURCE EXPORTS AND NOT unified.jsonl. The unified file has been through
sanitization - "NA" normalisation, coordinate-kNN city fills, Arabic name fixes
and label-conflict resolution. Comparing against it compares against our own
processing. talabat_export.json (June-July) and August_export.json are what was
actually built from the scrape, so they are the honest reference.

WHY ijson HERE AND orjson EARLIER. These two are single large NESTED JSON
documents (a 388 MB and a 258 MB array), so they must be streamed item by item -
that is exactly ijson's job. unified.jsonl is JSON *Lines*, one object per line,
where orjson-per-line is faster and ijson would give nothing. Different shape,
different tool.

CUISINE MUST MATCH EXACTLY - Sagar's rule. Not "shares a cuisine": the FULL SET
must be identical. If the sets differ at all the two are different restaurants.

    Maki Sushi     {japanese, sushi}
    omaki sushi    {japanese, sushi, asian}     -> DIFFERENT, sets are not equal

This is deliberately strict. A shared cuisine is weak evidence - two unrelated
Indian restaurants both say "Indian" - whereas an identical combination across a
near-identical name is strong.

    python review_vs_source_exports.py
    python review_vs_source_exports.py --min 88
"""
import argparse
import csv
import sys
from collections import Counter, defaultdict
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import ijson
import orjson
from rapidfuzz import fuzz, process

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "listing_comparison"))
from common import norm_name                                    # noqa: E402

SEPT = ROOT / "listing_comparison" / "output" / \
    "sept_full_2026-09-08_identity_classification.csv"
CONF = ROOT / "Restaurant Identifier" / "data" / "restaurants_confirmed_sep.jsonl"
EXPORTS = [("july", ROOT / "export" / "talabat_export.json"),
           ("august", ROOT / "August_menu" / "data" / "August_export.json")]
OUT = HERE / "data" / "collisions_vs_source_exports.csv"
sys.stdout.reconfigure(encoding="utf-8")


def km(a, b):
    try:
        la1, lo1, la2, lo2 = map(radians, (float(a[0]), float(a[1]),
                                           float(b[0]), float(b[1])))
    except (TypeError, ValueError):
        return None
    h = sin((la2-la1)/2)**2 + cos(la1)*cos(la2)*sin((lo2-lo1)/2)**2
    return 6371.0 * 2 * asin(sqrt(h))


def cset(*vals):
    out = set()
    for v in vals:
        if not v:
            continue
        if isinstance(v, str):
            out |= {c.strip().lower() for c in v.split(",") if c.strip()}
        elif isinstance(v, (list, tuple)):
            out |= {str(x).strip().lower() for x in v if str(x).strip()}
    return out


def main(args):
    print("=" * 78)
    print(f"  SEPTEMBER vs SOURCE EXPORTS   name >= {args.min} + EXACT cuisine")
    print("=" * 78)

    rows = [r for r in csv.DictReader(open(SEPT, encoding="utf-8-sig"))
            if r["category"] == "genuinely_new_brand"]
    sep_cui = {}
    for line in open(CONF, encoding="utf-8"):
        try:
            rec = orjson.loads(line)
        except Exception:
            continue
        sep_cui[str(rec.get("branch_id"))] = cset(rec.get("serves_cuisine"))
    print(f"  september new brands : {len(rows):,}")

    names, geo, cui, src, sid = [], defaultdict(list), defaultdict(set), {}, {}
    for label, path in EXPORTS:
        n = 0
        with open(path, "rb") as f:
            for rec in ijson.items(f, "item"):
                n += 1
                k = norm_name(rec.get("name"))
                if not k:
                    continue
                if k not in cui:
                    names.append(k)
                    src[k] = label
                    sid[k] = rec.get("source_id")
                g = rec.get("geo") or {}
                geo[k].append((g.get("lat"), g.get("lng")))
                # key_cuisines IS the serve_cuisine column - the export swaps
                # sub_cuisines/key_cuisines by explicit spec. September's side is
                # raw servesCuisine, so this is the only apples-to-apples field.
                # Unioning all three made the export set systematically larger
                # and exact equality almost never held (5 of 102).
                cui[k] |= cset(rec.get("key_cuisines"))
        print(f"  {label:8} export streamed : {n:,} records")
    print(f"  distinct brand names in both exports : {len(names):,}")

    keys = [norm_name(r["name"]) for r in rows]
    res = process.cdist(keys, names, scorer=fuzz.token_sort_ratio,
                        score_cutoff=args.min, workers=-1)

    out = []
    for i, row in enumerate(res):
        bj, bs = -1, 0
        for j, s in enumerate(row):
            if s > bs:
                bs, bj = s, j
        if bj < 0:
            continue
        k2 = names[bj]
        a, b = sep_cui.get(str(rows[i]["branch_id"]), set()), cui[k2]
        d = min((x for x in (km((rows[i].get("lat"), rows[i].get("lon")), p)
                             for p in geo[k2]) if x is not None), default=None)
        identical = bool(a) and a == b
        out.append({
            "branch_id": rows[i]["branch_id"],
            "sept_name": rows[i]["name"],
            "export_name": k2,
            "export": src[k2],
            "export_source_id": sid[k2],
            "name_score": round(bs, 1),
            "km_apart": "" if d is None else round(d, 2),
            "sept_cuisines": ", ".join(sorted(a)),
            "export_cuisines": ", ".join(sorted(b)),
            "cuisine_identical": identical,
            "verdict": ("SAME RESTAURANT - drop" if identical else
                        "different - cuisine sets differ"),
        })

    out.sort(key=lambda r: (not r["cuisine_identical"], -r["name_score"]))
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)

    same = [r for r in out if r["cuisine_identical"]]
    print(f"\n  name matches >= {args.min}          : {len(out):,}")
    print(f"    cuisine sets IDENTICAL -> drop : {len(same)}")
    print(f"    cuisine differs -> keep        : {len(out)-len(same)}")
    if same:
        print(f"\n  DROP THESE ({len(same)}):")
        for r in same:
            print(f"    {r['name_score']:5.1f} {r['sept_name'][:28]:30} ~ "
                  f"{r['export_name'][:24]:26} [{r['export_cuisines'][:34]}] "
                  f"{r['export']}")
    print(f"\n  near-misses kept because ONE cuisine differs:")
    for r in [x for x in out if not x["cuisine_identical"]][:10]:
        print(f"    {r['name_score']:5.1f} {r['sept_name'][:24]:26} ~ "
              f"{r['export_name'][:20]:22}")
        print(f"          sept  [{r['sept_cuisines'][:56]}]")
        print(f"          expt  [{r['export_cuisines'][:56]}]")
    print(f"\n  -> {OUT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--min", type=float, default=88)
    sys.exit(main(p.parse_args()))
