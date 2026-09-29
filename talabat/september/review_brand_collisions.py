"""Name + CUISINE collision review: September new brands vs the unified file.

WHY CUISINE. A fuzzy name match alone cannot separate a brand we already hold
from an unrelated business that shares words. Cuisine breaks the tie:

    Maki Sushi ~ omaki sushi   95.2   both Japanese/Sushi   -> same brand
    Princes Palace ~ pies palace 88.0  Indian vs Bakery     -> coincidence

Memory records the cost of trusting names alone: a sample of platform matches
was 55% false positives because one shared generic word was enough to pass.

WHERE EACH SIDE'S CUISINE COMES FROM
    september : restaurants_confirmed_sep.jsonl -> serves_cuisine (raw, comma
                combination straight off the page) joined on branch_id
    unified   : cuisine + sub_cuisines + key_cuisines, unioned

`key_cuisines` and `sub_cuisines` are SWAPPED relative to their source columns
by explicit spec, which does not matter here - we union all three.

OUTPUT is a review CSV, not a decision. Distance separates same-VENUE from
same-BRAND, and neither is auto-dropped: a chain legitimately has branches
146 km apart, and two unrelated cafes can share a name.

    python review_brand_collisions.py --min 88
"""
import argparse
import csv
import sys
from collections import defaultdict
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import orjson
from rapidfuzz import fuzz, process

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "listing_comparison"))
from common import norm_name                                    # noqa: E402

SEPT = ROOT / "listing_comparison" / "output" / \
    "sept_full_2026-09-08_identity_classification.csv"
CONF = ROOT / "Restaurant Identifier" / "data" / "restaurants_confirmed_sep.jsonl"
UNIFIED = ROOT / "unified" / "data" / "talabat_unified_202608.jsonl"
OUT = HERE / "data" / "brand_collisions_review.csv"
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
    """Flatten cuisine values (string combination or list) to a lowercase set."""
    out = set()
    for v in vals:
        if not v:
            continue
        if isinstance(v, str):
            out |= {c.strip().lower() for c in v.split(",") if c.strip()}
        elif isinstance(v, (list, tuple)):
            for x in v:
                if isinstance(x, str) and x.strip():
                    out.add(x.strip().lower())
    return out


def main(args):
    print("=" * 78)
    print(f"  BRAND COLLISION REVIEW  (name >= {args.min} + cuisine)")
    print("=" * 78)

    rows = [r for r in csv.DictReader(open(SEPT, encoding="utf-8-sig"))
            if r["category"] == "genuinely_new_brand"]

    # september cuisine, joined on branch_id
    sep_cui = {}
    for line in open(CONF, encoding="utf-8"):
        try:
            rec = orjson.loads(line)
        except Exception:
            continue
        sep_cui[str(rec.get("branch_id"))] = cset(rec.get("serves_cuisine"),
                                                  rec.get("matched_cuisines"))
    print(f"  september new brands : {len(rows):,}  "
          f"(cuisine found for {sum(1 for r in rows if sep_cui.get(str(r['branch_id']))):,})")

    names, geo, cui = [], defaultdict(list), defaultdict(set)
    for line in open(UNIFIED, "rb"):
        rec = orjson.loads(line)
        k = norm_name(rec.get("name"))
        if not k:
            continue
        if k not in cui:
            names.append(k)
        g = rec.get("geo") or {}
        geo[k].append((g.get("lat"), g.get("lng")))
        cui[k] |= cset(rec.get("cuisine"), rec.get("sub_cuisines"),
                       rec.get("key_cuisines"))
    print(f"  unified brand names  : {len(names):,}")

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
        shared = a & b
        d = min((x for x in (km((rows[i].get("lat"), rows[i].get("lon")), p)
                             for p in geo[k2]) if x is not None), default=None)
        out.append({
            "branch_id": rows[i]["branch_id"],
            "sept_name": rows[i]["name"],
            "unified_name": k2,
            "name_score": round(bs, 1),
            "km_apart": "" if d is None else round(d, 2),
            "sept_cuisines": ", ".join(sorted(a)),
            "unified_cuisines": ", ".join(sorted(b)),
            "shared_cuisines": ", ".join(sorted(shared)),
            "n_shared": len(shared),
            "verdict": (
                "SAME VENUE - drop" if (d is not None and d <= 0.5) else
                "SAME BRAND - likely a new branch, not a new brand"
                if (bs >= 95 and shared) else
                "probably coincidence" if not shared else "review"),
        })

    out.sort(key=lambda r: (-r["n_shared"], -r["name_score"]))
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)

    from collections import Counter
    print(f"\n  {'VERDICT':52}{'ROWS':>6}")
    for k, v in Counter(r["verdict"] for r in out).most_common():
        print(f"    {k:52}{v:>6}")
    print(f"\n  cuisine AGREES on {sum(1 for r in out if r['n_shared']):,} of "
          f"{len(out):,} name matches")
    print(f"\n  strongest evidence (name >= 95 AND shared cuisine):")
    for r in [r for r in out if r["name_score"] >= 95 and r["n_shared"]][:16]:
        print(f"    {r['name_score']:5.1f} {r['sept_name'][:26]:28} ~ "
              f"{r['unified_name'][:24]:26} [{r['shared_cuisines'][:26]}]")
    print(f"\n  -> {OUT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--min", type=float, default=88)
    sys.exit(main(p.parse_args()))
