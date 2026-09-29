"""Step 1 - assemble every branch_id we have already evaluated.

This is the crawler's dedup seed. Anything in it is skipped at collection time,
so the September crawl returns only listings we have never assessed.

THE SEED IS NOT "WHAT WE SHIPPED". Measured: 22,327 branch_ids are in the two
shipped exports, but 35,220 were actually crawled. The 12,893 difference is
listings we found and then rejected - groceries, pharmacies, florists, empty
menus. Seeding from the exports alone would rediscover all 12,893 next month and
pay the identification and classification cost again for a known answer.

ONE DELIBERATE EXCEPTION. failed_urls*.jsonl are branch_ids whose page never
fetched - a timeout or a transport error. They were never JUDGED, so excluding
them would bury them permanently. They are removed from the seed so the crawler
offers them again.

    python build_universe.py
    python build_universe.py --no-retry     seed everything, retry nothing
"""
import argparse
import json
import sys
from collections import Counter

import ijson

from config import (AUGUST_EXPORT, IDENTIFIED, JULY_EXPORT, RAW_CRAWLS, RETRY,
                    UNIVERSE)

sys.stdout.reconfigure(encoding="utf-8")


def ids_from_jsonl(path, key="branch_id"):
    out = set()
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            v = json.loads(line).get(key)
            if v is not None:
                out.add(int(v))
    return out


def ids_from_export(path, key="source_id"):
    out = set()
    if not path.exists():
        return out
    with open(path, "rb") as f:
        for rec in ijson.items(f, "item", use_float=True):
            v = rec.get(key)
            if v is not None:
                out.add(int(v))
    return out


def main(args):
    print("=" * 72)
    print("  BUILD SEPTEMBER UNIVERSE  (crawler dedup seed)")
    print("=" * 72)

    universe = set()
    st = Counter()

    for label, path, fn in (
        ("july_export", JULY_EXPORT, ids_from_export),
        ("august_export", AUGUST_EXPORT, ids_from_export),
    ):
        got = fn(path)
        added = len(got - universe)
        universe |= got
        st[label] = len(got)
        print(f"  {label:22} {len(got):>7,} ids | +{added:>6,} new "
              f"-> {len(universe):,}")

    for path in RAW_CRAWLS:
        got = ids_from_jsonl(path)
        added = len(got - universe)
        universe |= got
        print(f"  {path.name[:22]:22} {len(got):>7,} ids | +{added:>6,} new "
              f"-> {len(universe):,}")

    for path in IDENTIFIED:
        got = ids_from_jsonl(path)
        added = len(got - universe)
        universe |= got
        print(f"  {path.name[:22]:22} {len(got):>7,} ids | +{added:>6,} new "
              f"-> {len(universe):,}")

    # --- carve out the never-judged failures ------------------------------
    retry = set()
    for path in RETRY:
        retry |= ids_from_jsonl(path)
    print(f"\n  fetch failures found      : {len(retry):,}")
    if args.no_retry:
        print("  --no-retry: they stay in the seed and will NOT be revisited")
    else:
        before = len(universe)
        universe -= retry
        print(f"  removed from seed        : {before - len(universe):,}"
              f"  -> the crawler will offer these again")

    lo, hi = min(universe), max(universe)
    print("\n" + "=" * 72)
    print(f"  SEED SIZE          {len(universe):>9,} branch_ids")
    print(f"  branch_id range    {lo:>9,}  ..  {hi:,}")
    print(f"  LAYER-3 THRESHOLD  {hi:>9,}   ids above this were listed after "
          f"our last crawl")

    UNIVERSE.write_text(json.dumps({
        "month": UNIVERSE.stem.split("_")[-1],
        "seed_size": len(universe),
        "min_branch_id": lo,
        "max_branch_id": hi,
        "newly_listed_threshold": hi,
        "retry_ids": sorted(retry) if not args.no_retry else [],
        "branch_ids": sorted(universe),
    }), encoding="utf-8")
    print(f"\n  -> {UNIVERSE.name}  ({UNIVERSE.stat().st_size/1e6:.1f} MB)")
    print("\n  NEXT: probe_zones.py")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--no-retry", action="store_true",
                   help="keep failed fetches in the seed (do not revisit them)")
    sys.exit(main(p.parse_args()))
