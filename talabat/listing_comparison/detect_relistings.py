"""Step 2 - find run-2 listings that are the SAME PHYSICAL VENUE as an existing
one, re-listed by Talabat under a new branch_id.

Why this is the comparison that matters: branch_id novelty is guaranteed by the
collector (it excluded known ids at collection time), so "N new branch_ids" is
not "N new businesses". Talabat re-lists venues on re-onboarding or ownership
change with a fresh id.

Matching uses the cascading ladder in match_strategies.py - strongest rule
first, the winning rule recorded per match. Candidates are found by geo
blocking plus a slug index and a chain index, so rows with missing or bad
coordinates are still reachable.

    python detect_relistings.py
"""
import sys
from collections import Counter, defaultdict

from common import (OUT, geo_key, load_universe, read_jsonl, write_csv,
                    write_json, NEW_RAW, EXISTING_RAW)
from match_strategies import ACCEPT_THRESHOLD, match, prepare

sys.stdout.reconfigure(encoding="utf-8")

NEIGHBOURS = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
STEP = 0.001


def main():
    old, new = load_universe()
    if not new:
        print("run-2 confirmed file is empty - has the classifier finished?")
        return 1

    # branch_slug lives in the RAW crawl files, not the classified ones;
    # merge it back so the strongest strategy (slug_exact) is available.
    for src, target in ((EXISTING_RAW, old), (NEW_RAW, new)):
        slugs = {r["branch_id"]: r.get("branch_slug")
                 for r in read_jsonl(src) if r.get("branch_slug")}
        for r in target:
            if not r.get("branch_slug"):
                r["branch_slug"] = slugs.get(r["branch_id"])

    old = [prepare(r) for r in old]
    new = [prepare(r) for r in new]

    print("=" * 70)
    print("RE-LISTING DETECTION - cascading strategy ladder")
    print("=" * 70)
    print(f"  existing : {len(old):,}   run2 : {len(new):,}")
    print(f"  existing with coords : {sum(1 for r in old if r['_coords']):,}")
    print(f"  existing with slug   : {sum(1 for r in old if r['_slug']):,}")

    # ---- indexes: geo grid, slug, chain ----
    grid, by_slug, by_chain = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in old:
        if r["_coords"]:
            k = geo_key(r["_coords"][0], r["_coords"][1])
            if k:
                grid[k].append(r)
        if r["_slug"]:
            by_slug[r["_slug"]].append(r)
        if r.get("restaurant_id"):
            by_chain[r["restaurant_id"]].append(r)

    matches, unmatched = [], 0
    for r in new:
        # candidate pool from three independent indexes, so a row with bad
        # coordinates can still be caught by slug or chain
        cands = {}
        if r["_coords"]:
            k = geo_key(r["_coords"][0], r["_coords"][1])
            if k:
                for dx, dy in NEIGHBOURS:
                    for c in grid.get((round(k[0] + dx * STEP, 3),
                                       round(k[1] + dy * STEP, 3)), []):
                        cands[c["branch_id"]] = c
        for c in by_slug.get(r["_slug"], []):
            cands[c["branch_id"]] = c
        for c in by_chain.get(r.get("restaurant_id"), []):
            cands[c["branch_id"]] = c

        best = None
        for c in cands.values():
            hit = match(r, c)
            if hit and (best is None or hit[1] > best[0][1]):
                best = (hit, c)
        if best:
            (strategy, conf, dist), c = best
            matches.append({
                "strategy": strategy, "confidence": conf, "distance_m": dist,
                "new_branch_id": r["branch_id"], "new_name": r.get("name"),
                "new_restaurant_id": r.get("restaurant_id"),
                "new_area": r.get("area_name"), "new_url": r.get("url"),
                "existing_branch_id": c["branch_id"], "existing_name": c.get("name"),
                "existing_restaurant_id": c.get("restaurant_id"),
                "existing_area": c.get("area_name"),
                "same_chain": r.get("restaurant_id") == c.get("restaurant_id"),
            })
        else:
            unmatched += 1

    # A low-confidence hit is NOT a re-listing. An identical slug 30 km away
    # means "same brand, different branch" - it must still count as a new
    # venue, and is only surfaced so a human can eyeball it.
    accepted = [m for m in matches if m["confidence"] >= ACCEPT_THRESHOLD]
    review = [m for m in matches if m["confidence"] < ACCEPT_THRESHOLD]
    genuinely_new = unmatched + len(review)
    by_strategy = Counter(m["strategy"].split("+")[0] for m in accepted)

    print(f"\n  RE-LISTINGS (confidence >= {ACCEPT_THRESHOLD}) : {len(accepted):,}"
          f" / {len(new):,}  ({len(accepted)/len(new)*100:.2f}%)")
    print(f"  same brand but far apart -> counted as NEW : {len(review):,}")
    print(f"  GENUINELY NEW VENUES : {genuinely_new:,} "
          f"({genuinely_new/len(new)*100:.2f}%)")
    print(f"\n  which strategy caught the accepted ones:")
    for s, c in by_strategy.most_common():
        print(f"    {s:34} {c:>6,}")

    hi = [m for m in accepted if m["confidence"] >= 0.93]
    print(f"\n  accepted: {len(hi):,} at >=0.93 | {len(accepted)-len(hi):,} at 0.80-0.92")
    if review:
        print("\n  flagged (same brand, almost certainly a DIFFERENT branch):")
        for m in sorted(review, key=lambda m: -(m["distance_m"] or 0))[:6]:
            print(f"    {str(m['new_name'])[:28]:30} vs {str(m['existing_name'])[:24]:26}"
                  f" {m['distance_m']:>10}m")

    write_csv(OUT / "suspected_relistings.csv",
              sorted(accepted, key=lambda m: -m["confidence"]))
    write_csv(OUT / "same_brand_different_branch.csv",
              sorted(review, key=lambda m: -(m["distance_m"] or 0)))
    write_json(OUT / "relisting_summary.json", {
        "run2_confirmed": len(new),
        "accepted_relistings": len(accepted),
        "flagged_same_brand_far": len(review),
        "genuinely_new": genuinely_new,
        "by_strategy": dict(by_strategy),
        "high_confidence": len(hi),
    })

    print("\n  accepted samples:")
    for m in sorted(accepted, key=lambda m: -m["confidence"])[:10]:
        print(f"    [{m['strategy'][:30]:32}] {str(m['new_name'])[:24]:26} "
              f"== {str(m['existing_name'])[:24]:26} {m['distance_m']}m")

    print(f"\n  -> output/suspected_relistings.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
