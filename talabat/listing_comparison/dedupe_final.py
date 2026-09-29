"""Final uniqueness gate over every genuinely_new_brand record.

Sagar's question: "7,528 - make sure they are all unique, not duplicates."

Answer depends on the GRAIN, so all three are reported rather than one number:

  branch_id      a physical location. 7,528 rows / 7,528 ids - unique by
                 construction (the collector excludes known branch_ids), so
                 this is an integrity assertion, not evidence of cleanliness.
  restaurant_id  the chain.
  name_key       the brand. LOWER than the row count on purpose: a new brand
                 that opened 8 branches is 8 legitimate rows, not 8 duplicates.

The real duplicates are RE-LISTINGS: Talabat re-registers a venue under a new
branch_id (often a new restaurant_id too), so the same physical restaurant
appears twice. Those are found by blocking on the brand key and measuring
distance - never by name similarity alone, which would merge genuinely
different outlets of the same brand.

Distance tiers, because confidence is not uniform:
    <=10m   near-certain same venue           -> collapsed
    10-50m  very likely same venue            -> collapsed
    50-100m uncertain (mall/food-court units) -> FLAGGED, kept, for review

    python dedupe_final.py --dry-run
    python dedupe_final.py
"""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, haversine_m, norm_name, write_csv, write_json

sys.stdout.reconfigure(encoding="utf-8")
csv.field_size_limit(10 ** 7)

SOURCES = [OUT / "run2_identity_classification.csv",
           OUT / "batch_2026-08-11_identity_classification.csv"]
AREA_CACHE = OUT / "areaname_cache.json"

COLLAPSE_M = 50.0        # at or under this, treat as the same venue
REVIEW_M = 100.0         # between COLLAPSE_M and this, flag but keep


def load_new_brands():
    rows = []
    for p in SOURCES:
        if not p.exists():
            print(f"  WARNING: missing {p.name}")
            continue
        with open(p, encoding="utf-8-sig") as f:
            n = 0
            for r in csv.DictReader(f):
                if r.get("category") == "genuinely_new_brand":
                    r["_src"] = p.name
                    r["_k"] = r.get("name_key") or norm_name(r.get("name"))
                    rows.append(r)
                    n += 1
        print(f"  {p.name}: {n:,} genuinely_new_brand")
    return rows


def coords(r):
    try:
        return float(r["lat"]), float(r["lon"])
    except (TypeError, ValueError, KeyError):
        return None


def find_pairs(rows):
    """Block on brand key so this stays O(sum k^2) instead of O(n^2)."""
    by_brand = defaultdict(list)
    for r in rows:
        if r["_k"]:
            by_brand[r["_k"]].append(r)
    pairs = []
    for group in by_brand.values():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = coords(group[i]), coords(group[j])
                if not a or not b:
                    continue
                d = haversine_m(a[0], a[1], b[0], b[1])
                if d <= REVIEW_M:
                    pairs.append((d, group[i], group[j]))
    pairs.sort(key=lambda x: x[0])
    return pairs


def cluster(rows, pairs):
    """Union-find, so A~B and B~C collapse to one venue, not two pairs."""
    parent = {r["branch_id"]: r["branch_id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for d, a, b in pairs:
        if d > COLLAPSE_M:
            continue                       # review tier is NOT collapsed
        ra, rb = find(a["branch_id"]), find(b["branch_id"])
        if ra != rb:
            parent[ra] = rb
    groups = defaultdict(list)
    for r in rows:
        groups[find(r["branch_id"])].append(r)
    return groups


def elect(group, area_ok):
    """Survivor: a resolved area beats none; then the newer listing."""
    return sorted(group, key=lambda r: (
        0 if area_ok.get(r["branch_id"]) else 1,
        -int(r["branch_id"]) if str(r["branch_id"]).isdigit() else 0,
    ))[0]


def main(args):
    print("=" * 68)
    print("  UNIQUENESS GATE - genuinely_new_brand")
    print("=" * 68)
    rows = load_new_brands()
    total = len(rows)
    print(f"\n  TOTAL rows: {total:,}")

    ids = [r["branch_id"] for r in rows]
    dup_ids = [k for k, v in Counter(ids).items() if v > 1]
    print(f"\n  --- grain ---")
    print(f"  distinct branch_id    : {len(set(ids)):,}"
          f"{'  OK' if not dup_ids else f'  <-- {len(dup_ids)} DUPLICATED'}")
    print(f"  distinct restaurant_id: {len({r['restaurant_id'] for r in rows}):,}  (chains)")
    print(f"  distinct name_key     : {len({r['_k'] for r in rows}):,}  (brands)")
    print("  a brand with N branches is N legitimate rows - not N duplicates")

    area_ok = {}
    if AREA_CACHE.exists():
        c = json.loads(AREA_CACHE.read_text(encoding="utf-8"))
        area_ok = {k: bool((v or {}).get("area_name_real"))
                   for k, v in c.items()}
        print(f"\n  areaName resolved for {sum(area_ok.values()):,} of {total:,}")

    pairs = find_pairs(rows)
    tiers = Counter("<=10m" if d <= 10 else "10-50m" if d <= COLLAPSE_M
                    else "50-100m(review)" for d, _, _ in pairs)
    print(f"\n  --- re-listing detection (same brand, blocked) ---")
    print(f"  candidate pairs within {REVIEW_M:.0f}m: {len(pairs)}")
    for t in ("<=10m", "10-50m", "50-100m(review)"):
        print(f"     {t:18} {tiers.get(t, 0)}")

    groups = cluster(rows, pairs)
    collapsed = {k: g for k, g in groups.items() if len(g) > 1}
    removed = sum(len(g) - 1 for g in collapsed.values())
    print(f"\n  clusters collapsed : {len(collapsed)}")
    print(f"  rows removed       : {removed}")
    print(f"  FINAL UNIQUE       : {total - removed:,}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    keep, audit = [], []
    for g in groups.values():
        winner = elect(g, area_ok) if len(g) > 1 else g[0]
        keep.append(winner)
        for r in g:
            if r["branch_id"] != winner["branch_id"]:
                audit.append({
                    "removed_branch_id": r["branch_id"],
                    "kept_branch_id": winner["branch_id"],
                    "name": r.get("name"),
                    "removed_restaurant_id": r.get("restaurant_id"),
                    "kept_restaurant_id": winner.get("restaurant_id"),
                    "removed_url": r.get("url"), "kept_url": winner.get("url"),
                    "lat": r.get("lat"), "lon": r.get("lon"),
                    "reason": "same brand within 50m - re-listing",
                })

    review = [{
        "distance_m": round(d, 1), "name": a.get("name"),
        "branch_id_a": a["branch_id"], "branch_id_b": b["branch_id"],
        "restaurant_id_a": a.get("restaurant_id"),
        "restaurant_id_b": b.get("restaurant_id"),
        "url_a": a.get("url"), "url_b": b.get("url"),
    } for d, a, b in pairs if d > COLLAPSE_M]

    # invariants - assert AFTER the transform, before writing
    assert len(keep) == total - removed, "row accounting mismatch"
    assert len({r["branch_id"] for r in keep}) == len(keep), "duplicate ids survived"

    for r in keep:
        r.pop("_k", None)
    write_csv(OUT / "genuinely_new_UNIQUE.csv", keep)
    with open(OUT / "genuinely_new_UNIQUE.jsonl", "w", encoding="utf-8") as f:
        for r in keep:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if audit:
        write_csv(OUT / "dedupe_removed_audit.csv", audit)
    if review:
        write_csv(OUT / "dedupe_needs_review.csv", review)
    write_json(OUT / "dedupe_summary.json", {
        "input_rows": total, "final_unique": len(keep),
        "rows_removed": removed, "clusters_collapsed": len(collapsed),
        "distinct_chains": len({r["restaurant_id"] for r in keep}),
        "distinct_brands": len({norm_name(r.get("name")) for r in keep}),
        "pairs_flagged_for_review": len(review),
        "collapse_threshold_m": COLLAPSE_M, "review_threshold_m": REVIEW_M,
    })
    print(f"\n  -> output/genuinely_new_UNIQUE.csv   ({len(keep):,} rows)")
    if audit:
        print(f"  -> output/dedupe_removed_audit.csv  ({len(audit)} removed, reversible)")
    if review:
        print(f"  -> output/dedupe_needs_review.csv   ({len(review)} to eyeball)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
