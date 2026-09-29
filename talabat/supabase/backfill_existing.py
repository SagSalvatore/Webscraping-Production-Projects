"""
backfill_existing.py — Load all existing snapshot JSON files into Supabase.

Finds snapshot_*.json files in talabat/menu/data/snapshots/ and backfills:
  - scrape_runs          : one row per run (with actual started_at from snapshot data)
  - talabat_restaurants  : upserted with latest scraped name/url (COALESCE keeps geo from seed)
  - fact_restaurants     : cross-platform bridge entries
  - talabat_menu_items   : full current menu per restaurant (upserted → latest run wins)
  - talabat_menu_deltas  : all delta rows from all runs (append-only, full history)

Safety: checks scrape_runs.completed_at before processing — skips already-backfilled runs
so this script can be safely re-run without duplicating delta rows.

Usage:
  cd talabat/supabase
  python backfill_existing.py             # backfill all runs
  python backfill_existing.py --dry-run   # preview without writing
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT          = Path(__file__).resolve().parent.parent
SNAPSHOTS_DIR = ROOT / "menu" / "data" / "snapshots"

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(ROOT / ".env")

from supabase_writer import SupabaseWriter

_FIELD_LABEL = {
    "price_aed":   "price",
    "category":    "menu_category",
    "description": "description",
}


def run_id_from_file(path: Path) -> str:
    return path.stem.replace("snapshot_", "")


def compute_run_stats(snaps: list) -> dict:
    ok     = [s for s in snaps if s.get("items") is not None]
    failed = len(snaps) - len(ok)
    price_ch = desc_ch = cat_ch = added_ct = removed_ct = 0

    for s in ok:
        d = s.get("delta") or {}
        added_ct   += len(d.get("added", []))
        removed_ct += len(d.get("removed", []))
        for ch in d.get("changed", []):
            for field in ch.get("field_changes", {}).keys():
                label = _FIELD_LABEL.get(field, field)
                if label == "price":         price_ch += 1
                elif label == "description": desc_ch  += 1
                elif label == "menu_category": cat_ch += 1

    return {
        "restaurants_scraped":   len(ok),
        "restaurants_changed":   sum(1 for s in ok if s.get("status") == "changed"),
        "restaurants_no_change": sum(1 for s in ok if s.get("status") == "no_change"),
        "restaurants_failed":    failed,
        "items_total":           sum(len(s.get("items") or []) for s in ok),
        "items_added":           added_ct,
        "items_removed":         removed_ct,
        "items_price_changed":   price_ch,
        "items_desc_changed":    desc_ch,
        "items_cat_changed":     cat_ch,
    }


def backfill_run(writer: SupabaseWriter, run_id: str, snaps: list, dry_run: bool):
    ok_snaps = [s for s in snaps if s.get("items") is not None]
    print(f"  Restaurants: {len(ok_snaps)} ok / {len(snaps)-len(ok_snaps)} failed")

    items_total = sum(len(s.get("items") or []) for s in ok_snaps)
    print(f"  Menu items : {items_total:,}")

    if dry_run:
        print(f"  [dry-run] would write {len(ok_snaps)} restaurants, {items_total} items")
        return

    # Register run with actual started_at from first snapshot timestamp
    started_at = next(
        (s["scraped_at"] for s in snaps if s.get("scraped_at")), None
    ) or datetime.now(timezone.utc).isoformat()

    writer._rpc("fn_upsert_scrape_run", {
        "p_run_id":     run_id,
        "p_platform":   "talabat",
        "p_mode":       "BACKFILL",
        "p_started_at": started_at,
    })

    # Write each restaurant
    for i, snap in enumerate(ok_snaps):
        writer.write_restaurant_scrape(snap, run_id)
        if (i + 1) % 25 == 0 or (i + 1) == len(ok_snaps):
            print(f"  [{i+1}/{len(ok_snaps)}] written")

    # Mark run complete with real stats
    stats = compute_run_stats(snaps)
    writer._rpc("fn_complete_scrape_run", {
        "p_run_id": run_id,
        "p_stats":  stats,
    })
    print(f"  Stats: {stats}")


def main():
    parser = argparse.ArgumentParser(description="Backfill snapshot data to Supabase")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written without DB calls")
    parser.add_argument("--force",   action="store_true", help="Re-process runs even if already marked complete")
    args = parser.parse_args()

    writer  = SupabaseWriter()
    client  = writer._client

    snapshot_files = sorted(SNAPSHOTS_DIR.glob("snapshot_*.json"))
    if not snapshot_files:
        print(f"No snapshot files in {SNAPSHOTS_DIR}")
        sys.exit(0)

    print(f"Found {len(snapshot_files)} snapshot file(s)\n")

    for snap_file in snapshot_files:
        run_id = run_id_from_file(snap_file)
        print(f"{'='*60}")
        print(f"Run: {run_id}  ({snap_file.name})")

        if not args.dry_run and not args.force:
            existing = client.table("scrape_runs") \
                .select("run_id,completed_at") \
                .eq("run_id", run_id) \
                .execute()
            if existing.data and existing.data[0].get("completed_at"):
                print(f"  Already backfilled (completed_at set) — skipping (use --force to re-run)\n")
                continue

        if not args.dry_run and args.force:
            # Clear completed_at so we can re-register the run cleanly
            client.table("scrape_runs").update({"completed_at": None}).eq("run_id", run_id).execute()

        print(f"  Loading {snap_file.name} ...")
        with open(snap_file, "r", encoding="utf-8") as f:
            snaps = json.load(f)

        backfill_run(writer, run_id, snaps, dry_run=args.dry_run)
        print()

    print("Backfill complete.")


if __name__ == "__main__":
    main()
