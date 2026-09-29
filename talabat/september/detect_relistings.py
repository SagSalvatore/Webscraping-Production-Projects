"""Step 3 - decide which September listings are genuinely new VENUES.

WHY branch_id IS NOT ENOUGH. The crawler's dedup seed only proves an id is
unseen. Talabat re-registers an existing venue under a FRESH branch_id when a
restaurant re-onboards or changes ownership - same place, same name, new id. On
branch_id alone every one of those looks like a new business.

So each new listing is matched against the whole known universe using the
cascading ladder in listing_comparison/match_strategies.py. That module is
IMPORTED, never copied: its thresholds encode bugs already paid for once, and a
divergent second copy would silently drift from them.

    s1   slug_exact     + coords <=100m   0.99
    s2   restaurant_id  + coords <=30m    0.98
    s4   name_exact     + coords <=50m    0.95
    s6   name_exact     + coords <=250m   0.85
    s7   fuzzy_name >=90+ coords <=150m   0.80   (cuisine must corroborate)
    s1b  slug_exact     + FAR             0.25   (a different branch, not a re-list)

Every rule pairs identity with DISTANCE, deliberately. restaurant_id names a
CHAIN, not a venue - KFC carries one across 258 branches - and the URL slug is
brand-based too. An unguarded slug rule once matched an Abu Dhabi venue to one
155 km away in RAK at 0.99 confidence.

WHAT CHANGES FOR SEPTEMBER. listing_comparison/common.load_universe() pins "the
existing universe" to the run-1 files. Run that unchanged and all 7,129 August
restaurants come back as new. Here the universe is every restaurant we have
confirmed to date - run-1 AND run-2 - so only September's genuine arrivals
survive.

    python detect_relistings.py --dry-run
    python detect_relistings.py --new data/september_crawl.jsonl
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "listing_comparison"))

from common import geo_key, read_jsonl                    # noqa: E402
from match_strategies import ACCEPT_THRESHOLD, match, prepare  # noqa: E402

from config import DATA, MONTH, RI, URLS                  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

# every restaurant confirmed in any prior cycle
KNOWN_CONFIRMED = [
    RI / "restaurants_confirmed.jsonl",
    RI / "restaurants_confirmed_run2.jsonl",
]
# slug lives only in the raw crawl output, so it is merged back in
RAW_FOR_SLUGS = [
    URLS / "talabat_restaurant_urls.jsonl",
    URLS / "talabat_restaurant_urls_run2.jsonl",
]

OUT_RELIST = DATA / f"relistings_{MONTH}.jsonl"
OUT_NEW = DATA / f"genuinely_new_venues_{MONTH}.jsonl"
OUT_REPORT = DATA / f"relisting_report_{MONTH}.json"

NEIGHBOURS = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
STEP = 0.001


def load_known():
    """Every restaurant we have already confirmed, deduped on branch_id."""
    by_id = {}
    for p in KNOWN_CONFIRMED:
        for r in read_jsonl(p):
            bid = r.get("branch_id")
            if bid is not None:
                by_id[int(bid)] = r          # later file wins; same schema
    return list(by_id.values())


def merge_slugs(rows):
    """branch_slug is in the RAW crawl files, not the classified ones. Without
    it the strongest strategy (slug_exact) can never fire."""
    slugs = {}
    for p in RAW_FOR_SLUGS:
        for r in read_jsonl(p):
            if r.get("branch_slug") and r.get("branch_id") is not None:
                slugs.setdefault(int(r["branch_id"]), r["branch_slug"])
    n = 0
    for r in rows:
        if not r.get("branch_slug"):
            s = slugs.get(int(r["branch_id"])) if r.get("branch_id") else None
            if s:
                r["branch_slug"] = s
                n += 1
    return n


def main(args):
    print("=" * 74)
    print(f"  SEPTEMBER RE-LISTING DETECTION   accept >= {ACCEPT_THRESHOLD}")
    print("=" * 74)

    new_path = Path(args.new) if args.new else DATA / f"september_crawl.jsonl"
    if not new_path.exists():
        print(f"  ABORT: September crawl output not found: {new_path}")
        print("         run listing_scraper/run2_collector.py seeded from")
        print("         build_universe.py, then pass --new <its output>")
        return 2

    known = load_known()
    new = read_jsonl(new_path)
    print(f"  known universe : {len(known):,} confirmed restaurants")
    print(f"  september crawl: {len(new):,} listings")
    if not new:
        print("  nothing to compare")
        return 1

    got = merge_slugs(known) + merge_slugs(new)
    print(f"  slugs merged in: {got:,}")

    known = [prepare(r) for r in known]
    new = [prepare(r) for r in new]
    print(f"  known with coords {sum(1 for r in known if r['_coords']):,} | "
          f"with slug {sum(1 for r in known if r['_slug']):,}")

    # three independent indexes so a row with bad coordinates is still reachable
    grid, by_slug, by_chain = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in known:
        if r["_coords"]:
            k = geo_key(r["_coords"][0], r["_coords"][1])
            if k:
                grid[k].append(r)
        if r["_slug"]:
            by_slug[r["_slug"]].append(r)
        if r.get("restaurant_id"):
            by_chain[r["restaurant_id"]].append(r)

    relistings, genuine = [], []
    strat = Counter()
    for r in new:
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

        if best and best[0][1] >= ACCEPT_THRESHOLD:
            (strategy, conf, dist), c = best
            strat[strategy] += 1
            relistings.append({
                "strategy": strategy, "confidence": conf, "distance_m": dist,
                "new_branch_id": r.get("branch_id"), "new_name": r.get("name"),
                "new_url": r.get("url"),
                "existing_branch_id": c.get("branch_id"),
                "existing_name": c.get("name"),
                "same_chain": r.get("restaurant_id") == c.get("restaurant_id"),
            })
        else:
            row = {k: v for k, v in r.items() if not k.startswith("_")}
            if best:
                row["_weak_match"] = {"strategy": best[0][0],
                                      "confidence": best[0][1]}
            genuine.append(row)

    print(f"\n  RE-LISTINGS (same venue, new id) : {len(relistings):,}")
    for s, n in strat.most_common():
        print(f"     {n:>6,}  {s}")
    print(f"  GENUINELY NEW VENUES             : {len(genuine):,}")
    weak = sum(1 for g in genuine if "_weak_match" in g)
    print(f"     of which had a weak match below threshold: {weak:,}")

    if relistings:
        print("\n  sample re-listings:")
        for m in relistings[:6]:
            print(f"     {str(m['new_name'])[:28]:30} {m['new_branch_id']:>8} "
                  f"<- was {m['existing_branch_id']:>8}  "
                  f"{m['strategy'][:34]}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    with open(OUT_RELIST, "w", encoding="utf-8") as f:
        for m in relistings:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    with open(OUT_NEW, "w", encoding="utf-8") as f:
        for g in genuine:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")
    OUT_REPORT.write_text(json.dumps({
        "month": MONTH,
        "known_universe": len(known),
        "september_crawl": len(new),
        "relistings": len(relistings),
        "genuinely_new_venues": len(genuine),
        "by_strategy": dict(strat),
        "accept_threshold": ACCEPT_THRESHOLD,
    }, indent=2), encoding="utf-8")
    print(f"\n  -> {OUT_RELIST.name}")
    print(f"  -> {OUT_NEW.name}")
    print(f"  -> {OUT_REPORT.name}")
    print("\n  NEXT: split_new_listings.py")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--new", help="September crawl output jsonl")
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
