"""Step 1 - identity-level comparison of run-2 against the existing universe.

Compares on the keys Sagar named (branch_id, restaurant_id, name) and reports
each honestly, including where a key is not informative:

  branch_id      TAUTOLOGICAL. run2_collector already excluded every existing
                 branch_id at collection time, so this is guaranteed 100% new.
                 Reported only as an integrity assertion - a non-zero overlap
                 would mean the collector's dedup failed.

  restaurant_id  MEANINGFUL. Tells us whether a listing is a new branch of a
                 chain we already track, or a brand new chain.

  name           MEANINGFUL. Brand-level novelty after stripping the branch
                 suffix Talabat appends ("Brand, Al Barsha").

DUAL-UNIVERSE MODE (--since)
  Per Sagar: do NOT merge the two universes. A batch is compared against
  run-1's 17,211 separately AND against run-2's 5,709 genuinely-new brands
  separately, and both results are reported. A row only survives as
  `genuinely_new_brand` if it is new against BOTH - otherwise the 5,709 we
  already found in run-2 would be re-reported as new every time.

  --since scopes the target rows to one crawl batch using `scraped_at`,
  because run2_collector appends to the same JSONL.

    python compare_identity.py
    python compare_identity.py --since 2026-08-11     top-20 priority batch
"""
import argparse
import csv
from pathlib import Path
import sys
from collections import Counter, defaultdict

from common import (EXISTING_RAW, NEW_RAW, OUT, load_universe, norm_name,
                    read_jsonl, write_csv, write_json)

sys.stdout.reconfigure(encoding="utf-8")

# run-2's already-accepted new brands. These are NOT merged into the run-1
# universe - they are a second, independently-reported comparison set.
RUN2_NEW = OUT / "run2_identity_classification.csv"


def load_run2_new_brands(extra_csvs=None):
    """Brands ALREADY accepted as genuinely_new by any prior cycle.

    Not just run-2's 5,709: the 2026-08-11 priority batch added 856 more in its
    own CSV. Miss one of those files and its brands get re-reported as new every
    subsequent month, inflating the count against the 10,000 target.
    """
    paths = [RUN2_NEW] + [Path(x) for x in (extra_csvs or [])]
    paths = [p for p in paths if p.exists()]
    if not paths:
        return set(), set(), 0
    names, chains, n = set(), set(), 0
    rows = []
    for p in paths:
        with open(p, encoding="utf-8-sig") as f:
            rows.extend(list(csv.DictReader(f)))
    for r in rows:
        if True:
            if r.get("category") != "genuinely_new_brand":
                continue
            n += 1
            if r.get("name_key"):
                names.add(r["name_key"])
            if r.get("restaurant_id"):
                chains.add(r["restaurant_id"])
    return names, chains, n


def main(args):
    # The existing universe GROWS each cycle. September must compare against
    # run-1 AND run-2/August, or every August restaurant reads as brand-new.
    existing = [x for x in (args.existing or "").split(",") if x.strip()] or None
    old, new = load_universe(existing, args.new_confirmed)

    if args.since:
        # The confirmed file carries `filtered_at` (when the CLASSIFIER ran),
        # not when the URL was crawled - and the classifier re-runs over old
        # rows, so its date cannot identify a crawl batch. The raw URL file is
        # the only place `scraped_at` lives, so the batch is defined there and
        # joined on branch_id.
        batch_ids = set()
        raw_path = args.new_raw or NEW_RAW
        for r in read_jsonl(raw_path):
            if (r.get("scraped_at") or "") >= args.since:
                batch_ids.add(r["branch_id"])
        before = len(new)
        new = [r for r in new if r["branch_id"] in batch_ids]
        print(f"  --since {args.since}: {len(batch_ids):,} branches crawled in "
              f"this batch; {len(new):,} of {before:,} confirmed rows match")
    if not new:
        print("run-2 confirmed file is empty - has the classifier finished?")
        return 1
    print("=" * 68)
    print("IDENTITY COMPARISON - run2 vs existing universe")
    print("=" * 68)
    print(f"  existing confirmed restaurants : {len(old):,}")
    print(f"  run2 confirmed restaurants     : {len(new):,}")

    # ---------- branch_id (integrity assertion) ----------
    old_b = {r["branch_id"] for r in old}
    new_b = {r["branch_id"] for r in new}
    overlap_b = old_b & new_b
    print(f"\n  branch_id overlap : {len(overlap_b)}")
    print("    (expected 0 - the collector excluded known branch_ids up front;")
    print("     a non-zero value here would mean that dedup failed)")

    # ---------- restaurant_id (chain) ----------
    old_r = {r.get("restaurant_id") for r in old if r.get("restaurant_id")}
    new_r = {r.get("restaurant_id") for r in new if r.get("restaurant_id")}
    shared_chains = old_r & new_r
    brand_new_chains = new_r - old_r
    rows_known_chain = sum(1 for r in new if r.get("restaurant_id") in shared_chains)
    rows_new_chain = len(new) - rows_known_chain
    print(f"\n  CHAIN level (restaurant_id)")
    print(f"    chains in existing            : {len(old_r):,}")
    print(f"    chains in run2                : {len(new_r):,}")
    print(f"    chains already known          : {len(shared_chains):,}")
    print(f"    BRAND-NEW chains              : {len(brand_new_chains):,}")
    print(f"    run2 rows that are a new branch of a known chain : {rows_known_chain:,}")
    print(f"    run2 rows belonging to a brand-new chain         : {rows_new_chain:,}")

    # ---------- name ----------
    old_n = {r["_name_key"] for r in old if r["_name_key"]}
    new_n = {r["_name_key"] for r in new if r["_name_key"]}
    print(f"\n  BRAND level (normalised name)")
    print(f"    distinct brands existing      : {len(old_n):,}")
    print(f"    distinct brands run2          : {len(new_n):,}")
    print(f"    brands already known          : {len(old_n & new_n):,}")
    print(f"    BRAND-NEW names               : {len(new_n - old_n):,}")

    # ---------- combined key Sagar asked for ----------
    old_k = {(r["branch_id"], r["_name_key"]) for r in old}
    new_k = {(r["branch_id"], r["_name_key"]) for r in new}
    print(f"\n  (branch_id + name) overlap      : {len(old_k & new_k)}")

    # ---------- SECOND universe: run-2's already-accepted new brands --------
    # Reported separately, never merged into `old`.
    r2_names, r2_chains, r2_rows = load_run2_new_brands(
        [x for x in (args.known_csv or "").split(",") if x.strip()])
    if args.since and r2_rows:
        hit_n = len(new_n & r2_names)
        print(f"\n  vs RUN-2 GENUINELY-NEW SET (reported separately, not merged)")
        print(f"    run-2 genuinely_new_brand rows : {r2_rows:,}")
        print(f"    distinct brands in that set    : {len(r2_names):,}")
        print(f"    this batch's brands already in it : {hit_n:,}")
        print(f"    brands new to BOTH universes      : {len(new_n - old_n - r2_names):,}")

    # ---------- per-row classification ----------
    per_row = []
    for r in new:
        known_chain = r.get("restaurant_id") in shared_chains
        known_brand = r["_name_key"] in old_n
        # a brand run-2 already banked counts as known too, so it is not
        # re-reported as new on every subsequent batch
        seen_in_run2 = bool(args.since) and (
            r["_name_key"] in r2_names or str(r.get("restaurant_id")) in r2_chains)
        if known_chain:
            cat = "new_branch_of_known_chain"
        elif known_brand:
            cat = "known_brand_new_chain_id"
        elif seen_in_run2:
            cat = "already_found_in_run2"
        else:
            cat = "genuinely_new_brand"
        per_row.append({
            "branch_id": r["branch_id"], "restaurant_id": r.get("restaurant_id"),
            "name": r.get("name"), "name_key": r["_name_key"],
            "area_name": r.get("area_name"), "url": r.get("url"),
            "lat": r.get("lat"), "lon": r.get("lon"),
            "category": cat,
        })
    counts = Counter(p["category"] for p in per_row)
    print(f"\n  RUN2 ROW BREAKDOWN")
    for k, v in counts.most_common():
        print(f"    {k:32} {v:>6,}  ({v/len(per_row)*100:.1f}%)")

    # NEVER overwrite run2_identity_classification.csv from a later cycle.
    # That file IS run-2's record and is read back as a comparison universe.
    # The guard used to key on --since only, so a September run driven by
    # --new-confirmed fell through to the default name and destroyed it: 9,446
    # rows replaced by 1,914, and it could not be reproduced because
    # restaurants_confirmed_run2.jsonl had grown from 9,446 to 15,855 rows since.
    if args.out_prefix:
        stem = args.out_prefix
    elif args.since:
        stem = f"batch_{args.since}"
    elif args.new_confirmed:
        raise SystemExit(
            "--new-confirmed given without --out-prefix or --since. "
            "Refusing to write the default run2_identity_classification.csv - "
            "that is run-2's own record and would be overwritten.")
    else:
        stem = "run2"
    out_name = (f"{stem}_identity_classification.csv" if stem != "run2"
                else "run2_identity_classification.csv")
    sum_name = (f"{stem}_identity_summary.json" if stem != "run2"
                else "identity_summary.json")
    write_csv(OUT / out_name, per_row)
    write_json(OUT / sum_name, {
        "existing_confirmed": len(old), "run2_confirmed": len(new),
        "branch_id_overlap": len(overlap_b),
        "chains_existing": len(old_r), "chains_run2": len(new_r),
        "chains_shared": len(shared_chains), "chains_brand_new": len(brand_new_chains),
        "brands_existing": len(old_n), "brands_run2": len(new_n),
        "brands_shared": len(old_n & new_n), "brands_new": len(new_n - old_n),
        "row_breakdown": dict(counts),
    })

    print(f"\n  top 12 brand-new names:")
    newb = Counter(r["name"] for r in new if r["_name_key"] not in old_n)
    for n, c in newb.most_common(12):
        print(f"    {c:>3}x  {n[:52]}")

    print(f"\n  -> output/{out_name}")
    print(f"  -> output/{sum_name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--since", help="only rows with scraped_at >= this (e.g. 2026-08-11)")
    p.add_argument("--existing", default="",
                   help="COMMA-SEPARATED confirmed files forming the existing "
                        "universe. Defaults to run-1 only, which is wrong for "
                        "any cycle after run-2 - pass every prior cycle.")
    p.add_argument("--new-confirmed", default=None,
                   help="this cycle's confirmed file (default: run-2's)")
    p.add_argument("--new-raw", default=None,
                   help="this cycle's raw URL file, for --since")
    p.add_argument("--known-csv", default="",
                   help="COMMA-SEPARATED prior identity_classification CSVs whose "
                        "genuinely_new_brand rows must ALSO be excluded")
    p.add_argument("--out-prefix", default=None,
                   help="output filename prefix (default: batch_<since>)")
    sys.exit(main(p.parse_args()))
