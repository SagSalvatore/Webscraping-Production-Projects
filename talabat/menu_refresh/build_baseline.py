"""Step 1 - build the delta baseline and the scrape target list from Postgres.

The baseline is the JUNE snapshot already in `talabat_menu_items`: 1,107,373
items across 15,199 branches, scraped 2026-06-16..19. That is exactly the data
Tech received last month, so diffing against it gives a true month-over-month
delta.

Supabase is NOT used - it was retired; local Postgres is the source of truth.

Scope is the 15,199 branches that HAVE a June menu. The other 569 restaurants
in talabat_restaurants have no baseline, so every item would read as ADDED and
would inflate the "growth" number with what is really first-capture. Per Sagar,
they are excluded.

Two hashes per branch, matching talabat_menu_tracker.compute_hash exactly:
    price_hash  {item_key: price}          - detects any AED movement
    full_hash   [{k,p,c,d} ...]            - also catches category/description
A branch whose new scrape reproduces both hashes is `no_change` and never gets
deep-diffed. On 15,199 branches that gate is the difference between minutes and
a long grind.

    python build_baseline.py --dry-run
    python build_baseline.py
"""
import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_PASSWORD  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

BASELINE = DATA / "baseline_june.json"
TARGETS = DATA / "scrape_targets.jsonl"

DB = dict(host="localhost", port=5432, dbname="RestaurantIntelligence",
          user="postgres", password=PG_PASSWORD)

sys.stdout.reconfigure(encoding="utf-8")


def compute_hash(data) -> str:
    """Byte-identical to talabat_menu_tracker.compute_hash - do not change
    independently or every branch will look changed."""
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main(args):
    cn = psycopg2.connect(**DB)
    c = cn.cursor()

    c.execute("""select run_id, count(*) from talabat_menu_items
                 group by 1 order by count(*) desc""")
    runs = c.fetchall()
    baseline_run = runs[0][0] if runs else None
    print("=" * 66)
    print("  BUILD BASELINE - June snapshot from Postgres")
    print("=" * 66)
    print(f"  runs present in talabat_menu_items: {len(runs)}")
    for r, n in runs[:5]:
        print(f"     {r:20} {n:>9,}")
    print(f"  dominant run_id (recorded as baseline_run_id): {baseline_run}")

    c.execute("""select m.branch_id, m.item_key, m.item_name, m.menu_category,
                        m.price_aed, m.description, m.run_id
                 from talabat_menu_items m
                 where m.item_key is not null""")
    rows = c.fetchall()
    print(f"\n  baseline item rows: {len(rows):,}")

    by_branch = defaultdict(list)
    runs_by_branch = {}
    for bid, key, name, cat, price, desc, run in rows:
        by_branch[bid].append({
            "item_key": key,
            "item_name": name or "",
            "category": cat or "",
            "price_aed": float(price) if price is not None else None,
            "description": desc or "",
        })
        runs_by_branch.setdefault(bid, run)
    print(f"  branches with a baseline menu: {len(by_branch):,}")

    # scrape targets: only branches that HAVE a baseline
    c.execute("""select branch_id, restaurant_id, restaurant_name, map_url
                 from talabat_restaurants where map_url is not null""")
    urls = {r[0]: r for r in c.fetchall()}
    cn.close()

    targets, no_url = [], 0
    for bid in by_branch:
        u = urls.get(bid)
        if not u:
            no_url += 1
            continue
        targets.append({"branch_id": bid, "restaurant_id": u[1],
                        "name": u[2], "url": u[3]})
    print(f"  scrape targets (baseline AND map_url): {len(targets):,}")
    if no_url:
        print(f"    baseline branches with NO map_url (skipped): {no_url:,}")

    missing_aid = sum(1 for t in targets if "?aid=" not in (t["url"] or ""))
    print(f"    targets missing ?aid= : {missing_aid:,}"
          + ("  <-- these will return 0 items" if missing_aid else "  OK"))

    baseline = {}
    for bid, items in by_branch.items():
        price_map = {i["item_key"]: i["price_aed"] for i in items
                     if i["price_aed"] is not None}
        # SORTED. Talabat returns menu items in a different order on every
        # request, and the DB returns them in yet another. An unsorted list
        # hashes differently on identical menus, so the gate would fire on
        # reordering alone and never skip anything. Sort on the whole tuple,
        # not just item_key - duplicate keys exist and would stay unordered.
        full_map = sorted(
            ({"k": i["item_key"], "p": i["price_aed"],
              "c": i["category"], "d": i["description"]} for i in items),
            key=lambda x: (x["k"], str(x["p"]), x["c"], x["d"]))
        baseline[str(bid)] = {
            "branch_id": bid,
            # talabat_menu_deltas.restaurant_id is 100% populated on the June
            # rows; carry it so August's do not become the odd ones out
            "restaurant_id": urls[bid][1] if bid in urls else None,
            "baseline_run_id": runs_by_branch.get(bid),
            "baseline_items": items,
            "baseline_price_hash": compute_hash(price_map),
            "baseline_full_hash": compute_hash(full_map),
            "baseline_item_count": len(items),
        }

    counts = [v["baseline_item_count"] for v in baseline.values()]
    print(f"\n  items per branch: min {min(counts)} median "
          f"{sorted(counts)[len(counts)//2]} max {max(counts)}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    BASELINE.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    with open(TARGETS, "w", encoding="utf-8") as f:
        for t in targets:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(f"\n  -> {BASELINE.name}  ({len(baseline):,} branches, "
          f"{BASELINE.stat().st_size/1e6:.0f} MB)")
    print(f"  -> {TARGETS.name}   ({len(targets):,} scrape targets)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
