"""
apply_to_supabase.py — Backfill clean categories/names into Supabase.

Reads talabat_menu_items in batches, runs text_cleaner on each row,
and pushes updates only where values actually changed (minimises API calls).

Tables updated:
  talabat_menu_items  → menu_category, item_name (whitespace only)
  talabat_menu_deltas → menu_category, item_name (whitespace only)

Usage:
  cd talabat/sanitization
  python apply_to_supabase.py --dry-run        # preview only, no writes
  python apply_to_supabase.py                  # live update
  python apply_to_supabase.py --table items    # only menu items
  python apply_to_supabase.py --table deltas   # only deltas
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sanitization"))
sys.path.insert(0, str(ROOT / "supabase"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from supabase import create_client
from text_cleaner import clean_category, clean_item_name

import time as _time

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
PAGE_SIZE    = 1000
# Recreate the client every N pages to avoid HTTP/2 connection aging
RECONNECT_EVERY = 50


def get_client():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_ANON_KEY not set in talabat/.env")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _execute_with_retry(query, retries: int = 4):
    """Execute a Supabase query with exponential backoff on transient errors."""
    last_exc = None
    for attempt in range(retries):
        try:
            return query.execute()
        except Exception as exc:
            last_exc = exc
            err = str(exc)
            transient = any(kw in err for kw in [
                "LocalProtocolError", "ConnectionTerminated",
                "RemoteProtocolError", "WinError 10035", "WinError 10054",
                "timed out", "h2", "367",
            ])
            if transient and attempt < retries - 1:
                wait = 2 ** attempt
                print(f"  [retry {attempt+1}] transient error: {err[:80]} — wait {wait}s")
                _time.sleep(wait)
                continue
            raise
    raise last_exc


def sanitize_menu_items(client_factory, dry_run: bool):
    print("\n[1/2] Sanitizing talabat_menu_items ...")
    offset   = 0
    total    = 0
    updated  = 0
    page_num = 0
    client   = client_factory()

    while True:
        # Reconnect every N pages to avoid HTTP/2 connection aging
        if page_num > 0 and page_num % RECONNECT_EVERY == 0:
            client = client_factory()

        rows = _execute_with_retry(
            client.table("talabat_menu_items")
            .select("id,menu_category,item_name")
            .range(offset, offset + PAGE_SIZE - 1)
        ).data

        if not rows:
            break

        dirty = []
        for r in rows:
            orig_cat  = r.get("menu_category") or ""
            orig_name = r.get("item_name") or ""
            new_cat   = clean_category(orig_cat)
            new_name  = clean_item_name(orig_name)

            if new_cat != orig_cat or new_name != orig_name:
                dirty.append({
                    "id":            r["id"],
                    "menu_category": new_cat,
                    "item_name":     new_name,
                })

        total   += len(rows)
        updated += len(dirty)
        page_num += 1

        if dirty and not dry_run:
            # UPDATE (not upsert) — only modifies the two text columns, leaves branch_id etc. intact
            for row_data in dirty:
                _execute_with_retry(
                    client.table("talabat_menu_items")
                    .update({"menu_category": row_data["menu_category"],
                             "item_name":     row_data["item_name"]})
                    .eq("id", row_data["id"])
                )

        pct = updated / total * 100 if total else 0
        print(f"  [{total:,}]  dirty={len(dirty)}  cum_updated={updated:,}  ({pct:.1f}% needed cleaning)")

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    tag = " [DRY RUN]" if dry_run else ""
    print(f"  Done{tag}: {total:,} rows scanned, {updated:,} updated, {total-updated:,} already clean")


def sanitize_menu_deltas(client_factory, dry_run: bool):
    print("\n[2/2] Sanitizing talabat_menu_deltas ...")
    offset   = 0
    total    = 0
    updated  = 0
    page_num = 0
    client   = client_factory()

    while True:
        if page_num > 0 and page_num % RECONNECT_EVERY == 0:
            client = client_factory()

        rows = _execute_with_retry(
            client.table("talabat_menu_deltas")
            .select("id,menu_category,item_name")
            .range(offset, offset + PAGE_SIZE - 1)
        ).data

        if not rows:
            break

        dirty = []
        for r in rows:
            orig_cat  = r.get("menu_category") or ""
            orig_name = r.get("item_name") or ""
            new_cat   = clean_category(orig_cat)
            new_name  = clean_item_name(orig_name)

            if new_cat != orig_cat or new_name != orig_name:
                dirty.append({
                    "id":            r["id"],
                    "menu_category": new_cat,
                    "item_name":     new_name,
                })

        total    += len(rows)
        updated  += len(dirty)
        page_num += 1

        if dirty and not dry_run:
            for row_data in dirty:
                _execute_with_retry(
                    client.table("talabat_menu_deltas")
                    .update({"menu_category": row_data["menu_category"],
                             "item_name":     row_data["item_name"]})
                    .eq("id", row_data["id"])
                )

        print(f"  [{total:,}]  dirty={len(dirty)}  cum_updated={updated:,}")

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    tag = " [DRY RUN]" if dry_run else ""
    print(f"  Done{tag}: {total:,} rows scanned, {updated:,} updated")


def main():
    parser = argparse.ArgumentParser(description="Backfill clean text into Supabase")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report without writing")
    parser.add_argument("--table",   choices=["items", "deltas", "all"], default="all",
                        help="Which table(s) to sanitize (default: all)")
    args = parser.parse_args()

    get_client()   # validate credentials before starting
    print(f"Connected: {SUPABASE_URL[:50]}...")
    if args.dry_run:
        print("[DRY RUN MODE — no writes will happen]")

    if args.table in ("items", "all"):
        sanitize_menu_items(get_client, dry_run=args.dry_run)

    if args.table in ("deltas", "all"):
        sanitize_menu_deltas(get_client, dry_run=args.dry_run)

    print("\nSanitization complete.")


if __name__ == "__main__":
    main()
