"""Build the refresh baseline + scrape targets from FILES, not Postgres.

WHY NOT POSTGRES. build_baseline.py reads `talabat_menu_items`. Postgres is
deliberately parked (Sagar: "many things need to be updated in postgre which we
would do later"), so the baseline has to come from disk.

WHY NOT THE EXPORTS EITHER - this is the load-bearing choice. The monthly export
runs `smart_title` over name, section and description, and normalises "NA".
A new scrape is RAW. Diffing cleaned-against-raw reports our own sanitization as
restaurant activity: on the August run that trap fabricated 23,971 of 421,300
delta rows (5.7%) from emoji and whitespace alone, and title-casing would be far
worse - every ALL-CAPS category in the baseline would read as an edit.

So the baseline is the RAW scrape JSONL each cohort already wrote. Same schema
as the scraper's own output, so the comparison is raw-against-raw and the whole
class of problem disappears:

    branch_id · item_id · item_name · item_key · category · price_aed · description

COHORTS AND THEIR WINDOWS. The baselines were taken on different days, so a
pooled "median price change" would silently mix a 22-day move with a 29-day one.
Every branch carries `baseline_scraped_at`, and compute_delta writes
`days_since_baseline` per row so velocity can be stated per 30 days.

SEPTEMBER IS EXCLUDED, per Sagar: its baseline is two days old. There is nothing
to measure, and its first-capture items would read as ADDED and inflate growth.
Same reason build_baseline.py dropped the 569 branches that had no baseline.

    python build_baseline_from_files.py --dry-run
    python build_baseline_from_files.py
"""
import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
OUTDIR = DATA / "refresh_202609"
OUTDIR.mkdir(parents=True, exist_ok=True)

# (label, raw menu items, restaurant_status carrying url + scraped_at)
COHORTS = [
    ("july", DATA / "scrape" / "menu_items.jsonl",
     DATA / "scrape" / "restaurant_status.jsonl"),
    ("august", ROOT / "August_menu" / "data" / "menu_items.jsonl",
     ROOT / "August_menu" / "data" / "restaurant_status.jsonl"),
    # september deliberately absent - see the module docstring
]
OLD_TARGETS = DATA / "scrape_targets.jsonl"      # July's urls + restaurant_id
BASELINE = OUTDIR / "baseline_202609.json"
TARGETS = OUTDIR / "scrape_targets_202609.jsonl"
sys.stdout.reconfigure(encoding="utf-8")


def compute_hash(data) -> str:
    """Byte-identical to build_baseline.compute_hash and to
    talabat_menu_tracker.compute_hash. Do not change independently or every
    branch looks changed."""
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main(args):
    print("=" * 78)
    print("  BUILD REFRESH BASELINE  (from raw scrape files)")
    print("=" * 78)

    # July's target file is the only place restaurant_id lives outside the DB.
    # talabat_menu_deltas.restaurant_id is 100% populated on the June rows, so
    # it is carried wherever it can be.
    rid, url_of = {}, {}
    if OLD_TARGETS.exists():
        for line in open(OLD_TARGETS, encoding="utf-8"):
            t = json.loads(line)
            rid[str(t["branch_id"])] = t.get("restaurant_id")
            url_of[str(t["branch_id"])] = t.get("url")

    by_branch, meta = defaultdict(list), {}
    for label, items_p, status_p in COHORTS:
        n0 = len(by_branch)
        for line in open(status_p, "rb"):
            s = orjson.loads(line)
            b = str(s.get("branch_id"))
            if s.get("url"):
                url_of.setdefault(b, s["url"])
            meta[b] = {"cohort": label,
                       "baseline_scraped_at": s.get("scraped_at")}
        rows = 0
        for line in open(items_p, "rb"):
            r = orjson.loads(line)
            if not r.get("item_key"):
                continue
            by_branch[str(r["branch_id"])].append({
                "item_key": r["item_key"],
                "item_name": r.get("item_name") or "",
                "category": r.get("category") or "",
                "price_aed": (float(r["price_aed"])
                              if r.get("price_aed") is not None else None),
                "description": r.get("description") or "",
            })
            rows += 1
        print(f"  {label:9} {rows:>9,} items | branches "
              f"{len(by_branch)-n0:>6,} | baseline "
              f"{(list(meta.values())[-1]['baseline_scraped_at'] or '?')[:10]}")

    print(f"\n  branches with a baseline menu : {len(by_branch):,}")

    baseline, no_url = {}, 0
    for b, items in by_branch.items():
        u = url_of.get(b)
        if not u:
            no_url += 1
            continue
        price_map = {i["item_key"]: i["price_aed"] for i in items
                     if i["price_aed"] is not None}
        # SORTED on the WHOLE tuple. Talabat returns items in a different order
        # every request; an unsorted list hashes differently on identical menus
        # and the no-change gate never fires. Sorting on item_key alone leaves
        # duplicate keys unordered - that bug skipped 42 branches instead of
        # 3,084 on the August run.
        full_map = sorted(
            ({"k": i["item_key"], "p": i["price_aed"],
              "c": i["category"], "d": i["description"]} for i in items),
            key=lambda x: (x["k"], str(x["p"]), x["c"], x["d"]))
        m = meta.get(b, {})
        baseline[b] = {
            "branch_id": int(b),
            "restaurant_id": rid.get(b),
            "cohort": m.get("cohort"),
            "baseline_run_id": f"{m.get('cohort')}_"
                               f"{(m.get('baseline_scraped_at') or '')[:10]}",
            "baseline_scraped_at": m.get("baseline_scraped_at"),
            "baseline_items": items,
            "baseline_price_hash": compute_hash(price_map),
            "baseline_full_hash": compute_hash(full_map),
            "baseline_item_count": len(items),
        }
    if no_url:
        print(f"  branches with NO url (skipped): {no_url:,}")

    targets = [{"branch_id": v["branch_id"],
                "restaurant_id": v["restaurant_id"],
                "name": None, "url": url_of[b]}
               for b, v in baseline.items()]
    missing_aid = sum(1 for t in targets if "?aid=" not in (t["url"] or ""))
    print(f"  scrape targets                : {len(targets):,}")
    print(f"    missing ?aid=               : {missing_aid:,}"
          + ("   <-- these return 0 items" if missing_aid else "   OK"))

    counts = [v["baseline_item_count"] for v in baseline.values()]
    print(f"  baseline items                : {sum(counts):,}")
    print(f"    per branch  min {min(counts)} | median "
          f"{sorted(counts)[len(counts)//2]} | max {max(counts)}")
    from collections import Counter
    print(f"  by cohort: "
          f"{dict(Counter(v['cohort'] for v in baseline.values()))}")

    # the hash gate is worthless if the hashes are not distinct
    assert len({v["baseline_full_hash"] for v in baseline.values()}) > \
        len(baseline) * 0.5, "full_hash collapsing - check the sort"
    print(f"  distinct full_hash            : "
          f"{len({v['baseline_full_hash'] for v in baseline.values()}):,} "
          f"of {len(baseline):,}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    BASELINE.write_bytes(orjson.dumps(baseline))
    with open(TARGETS, "w", encoding="utf-8") as f:
        for t in targets:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(f"\n  -> {BASELINE.name}  ({BASELINE.stat().st_size/1e6:.0f} MB)")
    print(f"  -> {TARGETS.name}  ({len(targets):,} targets)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
