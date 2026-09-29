"""Step 4 - split the genuinely-new venues into NEWLY LISTED vs NEWLY FOUND.

THE QUESTION THIS EXISTS TO ANSWER. In June we collected 15,198 restaurants; in
August another 7,129 appeared. Management asked the obvious thing: did Talabat
gain 7,129 restaurants in two months, or did our June crawl miss them? Nothing
in the data answered it, because every timestamp we store is our own scraped_at
- when WE looked, never when THEY listed.

WHY THERE IS NO TALABAT TIMESTAMP. Measured, not assumed:
  * HTTP carries none - cache-control is `private, no-cache, no-store` and
    cf-cache-status is DYNAMIC, so there is no Last-Modified or ETag.
  * __NEXT_DATA__ exposes restaurant.createdAt, but it is NOT a date: across 33
    sampled restaurants it decreases strictly as branch_id rises, so it is
    derived from branch_id and carries no independent information.

WHAT DOES WORK. That same measurement is the finding: branch_id is assigned
SEQUENTIALLY. A branch_id above every id we have ever seen cannot have existed
when we last crawled - so it was listed since. One inside our existing range
existed already and our earlier sweep simply did not reach it.

    newly LISTED  branch_id > max(all ids ever seen)   -> real market growth
    newly FOUND   branch_id within that range          -> coverage recovery

Applied retroactively to August: 851 newly listed (11.7%), 6,410 newly found
(88.3%). The market did not grow by 7,129; our coverage did.

HONEST LIMIT. This rests on branch_id being sequential, which the createdAt
relationship strongly implies but does not prove - Talabat could have back-filled
ids at some point. Treat the split as a well-evidenced estimate, not a certified
count, and say so when reporting it.

    python split_new_listings.py
    python split_new_listings.py --input <genuinely_new_venues.jsonl>
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

from config import DATA, MONTH, NEW_SPLIT, UNIVERSE

sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    print("=" * 74)
    print("  NEWLY LISTED  vs  NEWLY FOUND")
    print("=" * 74)

    if not UNIVERSE.exists():
        print(f"  ABORT: {UNIVERSE.name} missing - run build_universe.py first")
        return 2
    uni = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    threshold = uni["newly_listed_threshold"]
    print(f"  universe        : {uni['seed_size']:,} branch_ids")
    print(f"  id range        : {uni['min_branch_id']:,} .. "
          f"{uni['max_branch_id']:,}")
    print(f"  threshold       : {threshold:,}")

    src = Path(args.input) if args.input else \
        DATA / f"genuinely_new_venues_{MONTH}.jsonl"
    if not src.exists():
        print(f"  ABORT: {src.name} missing - run detect_relistings.py first")
        return 2

    rows = []
    with open(src, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        print("  no genuinely-new venues to split")
        return 0

    listed, found = [], []
    for r in rows:
        bid = r.get("branch_id")
        if bid is None:
            continue
        (listed if int(bid) > threshold else found).append(r)

    n = len(listed) + len(found)
    print(f"\n  genuinely new venues : {n:,}")
    print(f"    NEWLY LISTED       : {len(listed):>6,}  "
          f"({len(listed)/n*100:5.1f}%)   id > {threshold:,} - listed since "
          f"our last crawl")
    print(f"    NEWLY FOUND        : {len(found):>6,}  "
          f"({len(found)/n*100:5.1f}%)   existed already, earlier crawl "
          f"missed them")

    for label, sel in (("NEWLY LISTED", listed), ("NEWLY FOUND", found)):
        if not sel:
            continue
        ids = [int(r["branch_id"]) for r in sel]
        print(f"\n  {label}: id range {min(ids):,} .. {max(ids):,}  "
              f"median {int(statistics.median(ids)):,}")
        for r in sel[:5]:
            print(f"     {int(r['branch_id']):>9,}  "
                  f"{str(r.get('name'))[:34]:36} {str(r.get('area_name'))[:20]}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    for label, sel in (("newly_listed", listed), ("newly_found", found)):
        p = DATA / f"{label}_{MONTH}.jsonl"
        with open(p, "w", encoding="utf-8") as f:
            for r in sel:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  -> {p.name}  ({len(sel):,})")

    NEW_SPLIT.write_text(json.dumps({
        "month": MONTH,
        "threshold_branch_id": threshold,
        "genuinely_new_venues": n,
        "newly_listed": len(listed),
        "newly_found": len(found),
        "newly_listed_pct": round(len(listed) / n * 100, 1),
        "basis": "branch_id is sequential; verified via restaurant.createdAt "
                 "decreasing strictly as branch_id rises (33/33 samples). "
                 "Estimate, not a certified count.",
    }, indent=2), encoding="utf-8")
    print(f"  -> {NEW_SPLIT.name}")

    print("\n  FOR MANAGEMENT:")
    print(f"    {len(listed):,} restaurants were newly listed on Talabat since "
          f"our last crawl.")
    print(f"    {len(found):,} existed already and were recovered by improved "
          f"coverage.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--input")
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
