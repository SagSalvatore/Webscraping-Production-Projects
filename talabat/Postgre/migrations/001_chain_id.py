"""Migration 001 - put chain_id into the database.

Until now chain_id existed ONLY inside export/talabat_export.json, recomputed at
export time, so chain lineage could not be queried in SQL. This lands it.

WHAT IT DOES (additive only - nothing is dropped, deleted or overwritten):
  1a. populate `chain_brands` (currently empty) with every chain in the export
  1b. add `talabat_restaurants.chain_id` + index + FK, and backfill it
  1c. add `mnc_chain_locations.chain_id` + index, and backfill it, so the
      Google Maps census joins to the same brand as the Talabat listings

chain_id itself is NOT invented here. It is SHA-256(brand slug)[:13] as int,
exactly as export_to_json.chain_id_int() computes it - deterministic across
runs (Python's hash() is randomised per process and would differ every time),
52 bits so it stays under JS MAX_SAFE_INTEGER for the client app.

THE EXPORT HAS TWO KINDS OF ROW - filtering is load-bearing:
    is_verified_location IS NULL  -> 15,198 real Talabat listings, 1 per branch
    is_verified_location = True   -> 1,989 Google Maps locations for the 28 MNC
                                     brands, DELIBERATELY fanned out so each
                                     physical KFC location carries the KFC menu
                                     scraped from Talabat. Same source_id and
                                     same chain_id repeated per location.
Backfilling branch chain_id from all 17,187 rows would process a source_id 31
times over; only the NULL-verified rows are one-per-branch.

    python 001_chain_id.py              # dry run - counts only, no writes
    python 001_chain_id.py --apply      # DDL + load, in one transaction
    python 001_chain_id.py --verify     # re-check assertions on live data
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from db_config import PG_PASSWORD  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
EXPORT = ROOT / "export" / "talabat_export.json"

DB = dict(host="localhost", port=5432, dbname="RestaurantIntelligence",
          user="postgres", password=PG_PASSWORD)

DDL = """
-- chain_brands.chain_id defaults to a sequence, but ours are SHA-256 derived.
-- Inserting explicitly bypasses the sequence; the default is left in place so
-- nothing else that relies on it breaks.
ALTER TABLE talabat_restaurants  ADD COLUMN IF NOT EXISTS chain_id BIGINT;
ALTER TABLE mnc_chain_locations  ADD COLUMN IF NOT EXISTS chain_id BIGINT;
CREATE INDEX IF NOT EXISTS idx_restaurants_chain_id ON talabat_restaurants(chain_id);
CREATE INDEX IF NOT EXISTS idx_mnc_locations_chain_id ON mnc_chain_locations(chain_id);
"""

# FK added separately: it can only succeed after chain_brands is populated.
DDL_FK = """
ALTER TABLE talabat_restaurants
  DROP CONSTRAINT IF EXISTS fk_restaurants_chain_id;
ALTER TABLE talabat_restaurants
  ADD CONSTRAINT fk_restaurants_chain_id
  FOREIGN KEY (chain_id) REFERENCES chain_brands(chain_id);
"""


def load_export():
    rows = json.loads(EXPORT.read_text(encoding="utf-8"))
    listings = [r for r in rows if not r.get("is_verified_location")]
    verified = [r for r in rows if r.get("is_verified_location")]
    return rows, listings, verified


def build_brands(rows):
    """One record per chain_id. Name/type/count taken from the most common
    value across that chain's rows, so a stray variant cannot decide it."""
    agg = defaultdict(lambda: {"names": Counter(), "types": Counter(),
                               "counts": Counter(), "sites": Counter(),
                               "branches": set()})
    for r in rows:
        cid = r.get("chain_id")
        if cid is None:
            continue
        a = agg[cid]
        a["names"][r.get("name")] += 1
        a["types"][r.get("chain_type") or "Independent"] += 1
        if r.get("chain_locations_count"):
            a["counts"][r["chain_locations_count"]] += 1
        if r.get("website"):
            a["sites"][r["website"]] += 1
        a["branches"].add(str(r.get("source_id")))
    out = []
    for cid, a in agg.items():
        out.append({
            "chain_id": cid,
            "chain_name": a["names"].most_common(1)[0][0],
            "chain_type": a["types"].most_common(1)[0][0],
            "total_locations_uae": (a["counts"].most_common(1)[0][0]
                                    if a["counts"] else len(a["branches"])),
            "website": a["sites"].most_common(1)[0][0] if a["sites"] else None,
        })
    return out


def main(args):
    rows, listings, verified = load_export()
    brands = build_brands(rows)

    print("=" * 70)
    print("  MIGRATION 001 - chain_id into the database")
    print("=" * 70)
    print(f"  export rows                    : {len(rows):,}")
    print(f"    real Talabat listings        : {len(listings):,}  (1 per branch)")
    print(f"    MNC Maps locations (fan-out) : {len(verified):,}  (menus repeated per location)")
    print(f"  distinct chain_id              : {len(brands):,}")
    print(f"    by type: {dict(Counter(b['chain_type'] for b in brands))}")

    # name collisions would break chain_brands.chain_name UNIQUE
    nm = Counter(b["chain_name"] for b in brands)
    clash = {n: c for n, c in nm.items() if c > 1}
    print(f"  chain_name collisions          : {len(clash)}"
          + (f"  <-- {list(clash)[:3]}" if clash else "  OK"))

    cn = psycopg2.connect(**DB)
    cn.autocommit = False
    c = cn.cursor()
    c.execute("select branch_id from talabat_restaurants")
    db_branches = {str(r[0]) for r in c.fetchall()}
    exp_branches = {str(r["source_id"]) for r in listings}
    print(f"\n  talabat_restaurants rows       : {len(db_branches):,}")
    print(f"    covered by the export        : {len(db_branches & exp_branches):,}")
    print(f"    NO export row -> chain_id NULL: {len(db_branches - exp_branches):,}")
    print(f"    export branch not in DB      : {len(exp_branches - db_branches):,}")

    c.execute("select brand_name, count(*) from mnc_chain_locations group by 1")
    mnc_db = dict(c.fetchall())
    by_name = {b["chain_name"]: b["chain_id"] for b in brands}
    matched = {b: n for b, n in mnc_db.items() if b in by_name}
    print(f"\n  mnc_chain_locations brands     : {len(mnc_db)}")
    print(f"    matched to a chain_id        : {len(matched)}  "
          f"({sum(matched.values()):,} of {sum(mnc_db.values()):,} rows)")
    if len(matched) < len(mnc_db):
        print(f"    UNMATCHED: {sorted(set(mnc_db) - set(matched))}")

    if not args.apply:
        print("\n  DRY RUN - nothing written. Re-run with --apply")
        cn.rollback(); cn.close()
        return 0

    if clash:
        print("\n  ABORT: chain_name collisions would violate the UNIQUE index.")
        cn.rollback(); cn.close()
        return 1

    try:
        print("\n  [1/4] DDL (additive) ...")
        c.execute(DDL)

        print("  [2/4] loading chain_brands ...")
        # chain_name_normalized is a GENERATED column (lower(trim(chain_name))),
        # so Postgres computes it - inserting a value raises GeneratedAlways.
        psycopg2.extras.execute_values(c, """
            INSERT INTO chain_brands
              (chain_id, chain_name, chain_type, total_locations_uae, website)
            VALUES %s
            ON CONFLICT (chain_id) DO UPDATE SET
              chain_name = EXCLUDED.chain_name,
              chain_type = EXCLUDED.chain_type,
              total_locations_uae = EXCLUDED.total_locations_uae,
              website = COALESCE(EXCLUDED.website, chain_brands.website),
              updated_at = now()
        """, [(b["chain_id"], b["chain_name"], b["chain_type"],
               b["total_locations_uae"], b["website"])
              for b in brands], page_size=1000)
        c.execute("select count(*) from chain_brands")
        print(f"        chain_brands rows: {c.fetchone()[0]:,}")

        print("  [3/4] backfilling talabat_restaurants.chain_id ...")
        pairs = [(int(r["source_id"]), r["chain_id"]) for r in listings
                 if str(r["source_id"]) in db_branches]
        psycopg2.extras.execute_values(c, """
            UPDATE talabat_restaurants t SET chain_id = v.cid
            FROM (VALUES %s) AS v(bid, cid)
            WHERE t.branch_id = v.bid
        """, pairs, page_size=1000)
        c.execute("select count(*) from talabat_restaurants where chain_id is not null")
        print(f"        restaurants with chain_id: {c.fetchone()[0]:,}")

        print("  [4/4] backfilling mnc_chain_locations.chain_id + FK ...")
        psycopg2.extras.execute_values(c, """
            UPDATE mnc_chain_locations m SET chain_id = v.cid
            FROM (VALUES %s) AS v(bname, cid)
            WHERE m.brand_name = v.bname
        """, [(b, by_name[b]) for b in matched], page_size=100)
        c.execute(DDL_FK)

        verify(c)
        cn.commit()
        print("\n  COMMITTED")
    except Exception as exc:
        cn.rollback()
        print(f"\n  ROLLED BACK - {type(exc).__name__}: {exc}")
        cn.close()
        return 1
    cn.close()
    return 0


def verify(c):
    print("\n  --- assertions ---")
    checks = []

    c.execute("select count(*) from chain_brands")
    n_brands = c.fetchone()[0]
    checks.append(("chain_brands populated", n_brands > 9000, n_brands))

    c.execute("select count(*) from talabat_restaurants where chain_id is not null")
    n_link = c.fetchone()[0]
    checks.append(("restaurants linked", n_link >= 15000, n_link))

    c.execute("""select count(*) from talabat_restaurants t
                 left join chain_brands b on b.chain_id = t.chain_id
                 where t.chain_id is not null and b.chain_id is null""")
    orphan = c.fetchone()[0]
    checks.append(("no orphan chain_id", orphan == 0, orphan))

    # the whole point: every branch of one brand shares one chain_id
    c.execute("""select count(*) from (
                   select chain_id from talabat_restaurants
                   where chain_id is not null group by chain_id
                   having count(*) > 1) x""")
    multi = c.fetchone()[0]
    checks.append(("multi-branch chains exist", multi > 1000, multi))

    c.execute("""select b.chain_name, count(*) n from talabat_restaurants t
                 join chain_brands b on b.chain_id=t.chain_id
                 group by 1 order by n desc limit 5""")
    top = c.fetchall()

    c.execute("select count(*) from mnc_chain_locations where chain_id is not null")
    n_mnc = c.fetchone()[0]
    checks.append(("maps census linked", n_mnc > 1500, n_mnc))

    for name, ok, val in checks:
        print(f"    {'PASS' if ok else 'FAIL'}  {name:28} {val:,}")
    print(f"\n    largest chains: {', '.join(f'{n} ({c_})' for n, c_ in top)}")
    if not all(ok for _, ok, _ in checks):
        raise AssertionError("verification failed")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="write changes")
    p.add_argument("--verify", action="store_true", help="assertions only")
    a = p.parse_args()
    if a.verify:
        cn = psycopg2.connect(**DB); verify(cn.cursor()); cn.close(); sys.exit(0)
    sys.exit(main(a))
