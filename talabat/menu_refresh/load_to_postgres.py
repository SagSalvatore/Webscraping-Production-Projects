"""Step 5 - land the August refresh in Postgres.

ONE transaction. Everything is asserted BEFORE commit; any failure rolls the
whole thing back, so a half-loaded month is not reachable.

What it writes:
  scrape_runs          1 row, with the real counters (June's runs were left as
                       mode='PENDING_FULL' with every stat at 0)
  talabat_menu_items   DELETE the re-scraped branches, INSERT 1,246,162 new rows
  talabat_menu_deltas  APPEND 397,329 rows, run_id AND baseline_run_id set

WHY DELETE+INSERT AND NOT UPSERT: talabat_menu_items has no unique key on
(branch_id, item_key) and cannot have one - 105,686 keys repeat within a branch
(112,945 duplicate rows), which is legitimate on Talabat (same item name under
two sections). So ON CONFLICT has nothing to target.

THE 80 DELISTED BRANCHES ARE DELIBERATELY UNTOUCHED. Per management, Tech keeps
using last month's data for them. They are not in the staging set, so the DELETE
never reaches them and their June rows survive. They also get no delta rows -
3,294 REMOVED rows would conflate "restaurant went dark" with "item removed"
and inflate item churn by ~3%.

AFTER THIS RUNS, the new rows have NULL std_term / taxonomy / *_clean. Those
are produced by the sanitisation and std_term stages and must be re-run before
an export can be generated - the export carries std_term and ingredients per
item.

    python load_to_postgres.py --dry-run     # assertions only, zero writes
    python load_to_postgres.py --confirm
"""
import argparse
import io
import json
import sys
from collections import Counter
from datetime import datetime, timezone
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
ITEMS = DATA / "scrape" / "menu_items.jsonl"
STATUS = DATA / "scrape" / "restaurant_status.jsonl"
DELTAS = DATA / "menu_deltas.jsonl"
REPORT = DATA / "delta_report.json"

DB = dict(host="localhost", port=5432, dbname="RestaurantIntelligence",
          user="postgres", password=PG_PASSWORD)

sys.stdout.reconfigure(encoding="utf-8")


def read_jsonl(p):
    with open(p, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def copy_items(cur, run_id):
    """COPY into a staging table - 1.25M single INSERTs would take hours."""
    cur.execute("""
        create temp table stg_items (
            branch_id bigint, item_id bigint, item_name text, item_key text,
            menu_category text, price_aed numeric, description text,
            scraped_at timestamptz
        ) on commit drop""")
    buf = io.StringIO()
    n = 0
    for r in read_jsonl(ITEMS):
        row = [
            str(r["branch_id"]),
            str(r["item_id"]) if r.get("item_id") is not None else r"\N",
            (r.get("item_name") or "").replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", " "),
            (r.get("item_key") or "").replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", " "),
            (r.get("category") or "").replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", " "),
            str(r["price_aed"]) if r.get("price_aed") is not None else r"\N",
            (r.get("description") or "").replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", " "),
            r.get("scraped_at") or datetime.now(timezone.utc).isoformat(),
        ]
        buf.write("\t".join(row) + "\n")
        n += 1
        if n % 200000 == 0:
            buf.seek(0)
            cur.copy_from(buf, "stg_items", null=r"\N")
            buf = io.StringIO()
            print(f"    staged {n:,}", flush=True)
    buf.seek(0)
    cur.copy_from(buf, "stg_items", null=r"\N")
    return n


def copy_deltas(cur, run_id):
    cur.execute("""
        create temp table stg_deltas (
            branch_id bigint, restaurant_id bigint, run_id text,
            baseline_run_id text, change_type text, item_name text,
            item_key text, menu_category text, field_changed text,
            old_value text, new_value text, old_price_aed numeric,
            new_price_aed numeric, price_change_pct numeric,
            detected_at timestamptz
        ) on commit drop""")

    def clean(v):
        if v is None:
            return r"\N"
        return (str(v).replace("\\", "\\\\").replace("\t", " ")
                .replace("\n", " ").replace("\r", " "))

    buf = io.StringIO()
    n = 0
    for d in read_jsonl(DELTAS):
        buf.write("\t".join([
            str(d["branch_id"]), clean(d.get("restaurant_id")), d["run_id"],
            clean(d.get("baseline_run_id")), d["change_type"],
            clean(d.get("item_name")), clean(d.get("item_key")),
            clean(d.get("menu_category")), clean(d.get("field_changed")),
            clean(d.get("old_value")), clean(d.get("new_value")),
            clean(d.get("old_price_aed")), clean(d.get("new_price_aed")),
            clean(d.get("price_change_pct")), d.get("detected_at"),
        ]) + "\n")
        n += 1
        if n % 200000 == 0:
            buf.seek(0)
            cur.copy_from(buf, "stg_deltas", null=r"\N")
            buf = io.StringIO()
            print(f"    staged {n:,}", flush=True)
    buf.seek(0)
    cur.copy_from(buf, "stg_deltas", null=r"\N")
    return n


def main(args):
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    run_id = rep["run_id"]
    status = list(read_jsonl(STATUS))
    scraped = [s for s in status if s.get("status") == "ok"]
    empties = [s for s in status if s.get("status") != "ok"]
    started = min(s["scraped_at"] for s in status)
    completed = max(s["scraped_at"] for s in status)

    print("=" * 68)
    print(f"  LOAD TO POSTGRES   run_id={run_id}")
    print("=" * 68)
    print(f"  items file   : {ITEMS.name}")
    print(f"  deltas file  : {DELTAS.name}  ({rep['delta_rows']:,} rows)")
    print(f"  branches ok  : {len(scraped):,}   empty (untouched): {len(empties)}")

    cn = psycopg2.connect(**DB)
    cn.autocommit = False
    cur = cn.cursor()
    try:
        cur.execute("select 1 from scrape_runs where run_id=%s", (run_id,))
        if cur.fetchone():
            print(f"  run_id {run_id} ALREADY in scrape_runs - aborting")
            cn.rollback()
            return 1

        # 1. register the run (FK target for both item and delta rows)
        cur.execute("""
            insert into scrape_runs (run_id, platform, mode, restaurants_scraped,
                restaurants_changed, restaurants_no_change, restaurants_failed,
                items_total, items_added, items_removed, items_price_changed,
                items_desc_changed, items_cat_changed, started_at, completed_at)
            values (%s,'talabat','MONTHLY_REFRESH',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (run_id, len(scraped),
             rep["branch_outcomes"].get("changed", 0),
             rep["branch_outcomes"].get("no_change", 0), 0,
             sum(1 for _ in read_jsonl(ITEMS)),
             rep["by_change_type"].get("ADDED", 0),
             rep["by_change_type"].get("REMOVED", 0),
             rep["changed_by_field"].get("price", 0),
             rep["changed_by_field"].get("description", 0),
             rep["changed_by_field"].get("menu_category", 0),
             started, completed))
        print("\n  [1/4] scrape_runs row inserted")

        # 2. stage
        print("  [2/4] staging items ...")
        n_items = copy_items(cur, run_id)
        print(f"        staged {n_items:,} items")
        print("        staging deltas ...")
        n_deltas = copy_deltas(cur, run_id)
        print(f"        staged {n_deltas:,} delta rows")

        # every staged branch must already exist (FK) and must be one we scraped
        cur.execute("""select count(*) from (select distinct branch_id from stg_items) s
                       left join talabat_restaurants r using (branch_id)
                       where r.branch_id is null""")
        orphans = cur.fetchone()[0]
        if orphans:
            raise AssertionError(f"{orphans} staged branches missing from "
                                 "talabat_restaurants - FK would fail")

        cur.execute("select count(distinct branch_id) from stg_items")
        stg_branches = cur.fetchone()[0]
        print(f"        distinct branches staged: {stg_branches:,}")

        # 3. replace the snapshot for re-scraped branches only
        cur.execute("""delete from talabat_menu_items
                       where branch_id in (select distinct branch_id from stg_items)""")
        deleted = cur.rowcount
        cur.execute("""
            insert into talabat_menu_items
                (branch_id, run_id, item_id, item_name, item_key, menu_category,
                 price_aed, description, scraped_at)
            select branch_id, %s, item_id, item_name, item_key, menu_category,
                   price_aed, description, scraped_at
            from stg_items""", (run_id,))
        inserted = cur.rowcount
        print(f"  [3/4] menu_items: deleted {deleted:,} old, inserted {inserted:,} new")

        # 4. append deltas
        cur.execute("""
            insert into talabat_menu_deltas
                (branch_id, restaurant_id, run_id, baseline_run_id, change_type,
                 item_name, item_key, menu_category, field_changed, old_value,
                 new_value, old_price_aed, new_price_aed, price_change_pct,
                 detected_at)
            select branch_id, restaurant_id, run_id, baseline_run_id, change_type,
                   item_name, item_key, menu_category, field_changed, old_value,
                   new_value, old_price_aed, new_price_aed, price_change_pct,
                   detected_at from stg_deltas""")
        d_ins = cur.rowcount
        print(f"  [4/4] menu_deltas: inserted {d_ins:,}")

        # ---------------- assertions, still inside the transaction ----------
        print("\n  --- assertions ---")
        checks = []

        def chk(name, cond, detail=""):
            checks.append((name, cond, detail))
            print(f"    {'PASS' if cond else 'FAIL'}  {name}"
                  + (f"   {detail}" if detail else ""))

        chk("inserted item count == file", inserted == n_items,
            f"{inserted:,} vs {n_items:,}")
        chk("inserted delta count == file", d_ins == n_deltas,
            f"{d_ins:,} vs {n_deltas:,}")
        chk("delta count == delta_report", d_ins == rep["delta_rows"],
            f"{d_ins:,} vs {rep['delta_rows']:,}")

        cur.execute("select count(*) from talabat_menu_items where run_id=%s", (run_id,))
        chk("all new rows carry the new run_id", cur.fetchone()[0] == n_items)

        cur.execute("""select count(*) from (
                         select branch_id from talabat_menu_items
                         group by branch_id having count(distinct run_id) > 1) x""")
        chk("no branch spans two runs", cur.fetchone()[0] == 0)

        empty_ids = [s["branch_id"] for s in empties]
        cur.execute("""select count(distinct branch_id) from talabat_menu_items
                       where branch_id = any(%s)""", (empty_ids,))
        kept = cur.fetchone()[0]
        chk("the 80 delisted branches keep their June rows",
            kept == len(empty_ids), f"{kept}/{len(empty_ids)}")

        cur.execute("""select count(*) from talabat_menu_items
                       where branch_id = any(%s) and run_id = %s""",
                    (empty_ids, run_id))
        chk("...and got no new rows", cur.fetchone()[0] == 0)

        cur.execute("""select count(*) from talabat_menu_deltas
                       where run_id=%s and restaurant_id is null""", (run_id,))
        chk("every delta row has restaurant_id", cur.fetchone()[0] == 0)

        cur.execute("""select count(*) from talabat_menu_deltas
                       where run_id=%s and baseline_run_id is null""", (run_id,))
        chk("every delta row has baseline_run_id", cur.fetchone()[0] == 0)

        cur.execute("""select count(*) from talabat_menu_deltas
                       where run_id=%s and change_type not in
                       ('ADDED','REMOVED','CHANGED')""", (run_id,))
        chk("change_type within CHECK constraint", cur.fetchone()[0] == 0)

        cur.execute("""select count(*) from talabat_menu_items
                       where run_id=%s and (item_key is null or item_name is null)""",
                    (run_id,))
        chk("no NULL item_key/item_name", cur.fetchone()[0] == 0)

        cur.execute("select count(*) from talabat_menu_items")
        total_after = cur.fetchone()[0]
        print(f"\n    talabat_menu_items after load : {total_after:,}")
        cur.execute("select count(*) from talabat_menu_deltas")
        print(f"    talabat_menu_deltas after load: {cur.fetchone()[0]:,}")

        failed = [c for c in checks if not c[1]]
        if failed:
            cn.rollback()
            print(f"\n  {len(failed)} ASSERTION(S) FAILED - rolled back, "
                  "database unchanged")
            return 1

        if not args.confirm:
            cn.rollback()
            print("\n  --dry-run (no --confirm): rolled back, database unchanged")
            print("     every assertion above ran against real staged data.")
            return 0

        cn.commit()
        print("\n  COMMITTED")
        return 0

    except Exception as exc:
        cn.rollback()
        print(f"\n  ERROR: {type(exc).__name__}: {exc}")
        print("  rolled back - database unchanged")
        return 1
    finally:
        cn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm", action="store_true")
    sys.exit(main(p.parse_args()))
