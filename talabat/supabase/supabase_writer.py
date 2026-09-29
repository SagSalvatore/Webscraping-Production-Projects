"""
SupabaseWriter — writes scrape results to Supabase PostgreSQL via RPC functions.

Why RPC-first design:
  Without functions: 150 menu items = 150 HTTP calls to Supabase REST API
  With fn_bulk_upsert_menu_items: 150 items = ONE call, PostgreSQL loops internally
  Result: ~150x fewer round-trips, atomic per-restaurant commit, cleaner error surface.

Functions used:
  fn_upsert_scrape_run           → register run at start
  fn_complete_scrape_run         → write final stats at end
  fn_upsert_talabat_restaurant   → NULL-safe smart upsert (keeps existing geo/ld data)
  fn_upsert_fact_restaurant      → cross-platform entity bridge entry
  fn_bulk_upsert_menu_items      → entire restaurant menu in ONE call
  fn_bulk_insert_menu_deltas     → all delta rows in ONE call

Thread-safe: supabase-py v2 uses httpx internally; concurrent .rpc() calls are safe.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
# SUPABASE_ANON_KEY is the JWT-format key (eyJ...) required by supabase-py
# Falls back to SUPABASE_PUBLISHABLE_KEY when future SDK versions support sb_publishable_ format
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")

import math

_FIELD_LABEL = {
    "price_aed":   "price",
    "category":    "menu_category",
    "description": "description",
}


def _safe_num(v):
    """Return None for NaN/Inf — JSON spec doesn't allow them."""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


class SupabaseWriter:
    """Thin wrapper around Supabase RPC functions for the menu tracker pipeline."""

    def __init__(self):
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY must be set in talabat/.env"
            )
        self._client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print(f"[Supabase] Connected: {SUPABASE_URL[:50]}...")

    # ── Run lifecycle ──────────────────────────────────────────────────────────

    def start_run(self, run_id: str, platform: str = "talabat", mode: str = "TEST") -> None:
        self._rpc("fn_upsert_scrape_run", {
            "p_run_id":     run_id,
            "p_platform":   platform,
            "p_mode":       mode,
            "p_started_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"[Supabase] Run '{run_id}' registered ({platform} / {mode})")

    def complete_run(
        self,
        run_id: str,
        counters: dict,
        all_snapshots: list,
        all_deltas: list,
    ) -> None:
        items_total = sum(s.get("item_count", 0) for s in all_snapshots if s.get("items") is not None)
        stats = {
            "restaurants_scraped":   counters.get("scraped", 0),
            "restaurants_changed":   counters.get("changed", 0),
            "restaurants_no_change": counters.get("no_change", 0),
            "restaurants_failed":    counters.get("failed", 0),
            "items_total":           items_total,
            "items_added":           sum(1 for d in all_deltas if d.get("change_type") == "ADDED"),
            "items_removed":         sum(1 for d in all_deltas if d.get("change_type") == "REMOVED"),
            "items_price_changed":   sum(1 for d in all_deltas if d.get("field_changed") == "price"),
            "items_desc_changed":    sum(1 for d in all_deltas if d.get("field_changed") == "description"),
            "items_cat_changed":     sum(1 for d in all_deltas if d.get("field_changed") == "menu_category"),
        }
        self._rpc("fn_complete_scrape_run", {
            "p_run_id": run_id,
            "p_stats":  stats,
        })
        print(f"[Supabase] Run '{run_id}' completed — "
              f"scraped={stats['restaurants_scraped']} "
              f"changed={stats['restaurants_changed']} "
              f"items_total={stats['items_total']}")

    # ── Per-restaurant write ───────────────────────────────────────────────────

    def write_restaurant_scrape(self, snap: dict, run_id: str) -> None:
        """
        Called once per successfully scraped restaurant.
        Writes 4 tables via 4 RPC calls (vs. potentially hundreds of REST calls):
          talabat_restaurants → fact_restaurants → menu_items → menu_deltas

        Fails silently so a DB issue never stalls the scraper.
        """
        branch_id = snap.get("branch_id")
        step = "init"
        try:
            items = snap.get("items") or []

            # Prefer live page_name (current HTML) over baseline name
            restaurant_name = snap.get("page_name") or snap.get("restaurant_name") or ""

            # serves_cuisine can arrive as "Burgers, Sides" string or already a list
            serves = snap.get("serves_cuisine") or ""
            serves_list = (
                [c.strip() for c in serves.split(",") if c.strip()]
                if isinstance(serves, str)
                else list(serves)
            )

            # 1 — restaurant record (NULL-safe: COALESCE in fn keeps geo/ld fields from seed)
            step = "upsert_restaurant"
            self._rpc("fn_upsert_talabat_restaurant", {
                "p_data": {
                    "branch_id":        branch_id,
                    "restaurant_id":    snap.get("restaurant_id"),
                    "restaurant_name":  restaurant_name,
                    "map_url":          snap.get("url") or "",
                    "serves_cuisine":   serves_list,
                    "area_name":        snap.get("area_name") or "",
                    "first_scraped_at": snap.get("scraped_at"),
                }
            })

            # 2 — cross-platform fact bridge
            step = "upsert_fact"
            self._rpc("fn_upsert_fact_restaurant", {
                "p_data": {
                    "platform":               "talabat",
                    "platform_restaurant_id": str(snap.get("restaurant_id") or ""),
                    "platform_branch_id":     str(branch_id),
                    "restaurant_name":        restaurant_name,
                    "area_name":              snap.get("area_name") or "",
                    "cuisine_types":          serves_list,
                    "first_seen_at":          snap.get("scraped_at"),
                }
            })

            # 3 — bulk menu items (1 HTTP call regardless of menu size)
            if items:
                # Deduplicate by item_key — same item_key in multiple categories keeps last seen
                seen_keys: dict = {}
                for it in items:
                    key = it.get("item_key") or ""
                    if key:
                        seen_keys[key] = it
                deduped = list(seen_keys.values())

                step = "bulk_menu_items"
                self._rpc("fn_bulk_upsert_menu_items", {
                    "p_branch_id": branch_id,
                    "p_run_id":    run_id,
                    "p_items": [
                        {
                            "item_id":       str(it.get("item_id") or ""),
                            "item_name":     it.get("item_name") or "",
                            "item_key":      it.get("item_key") or "",
                            "menu_category": it.get("category") or "",
                            "price_aed":     _safe_num(it.get("price_aed")),
                            "description":   it.get("description") or "",
                            "image_url":     it.get("image_url") or "",
                            "scraped_at":    snap.get("scraped_at"),
                        }
                        for it in deduped
                    ],
                })

            # 4 — bulk delta rows
            # Delete first to make this call idempotent (safe to re-run)
            delta_rows = self._build_delta_rows(snap, run_id)
            if delta_rows:
                step = "delete_old_deltas"
                self._client.table("talabat_menu_deltas") \
                    .delete() \
                    .eq("branch_id", branch_id) \
                    .eq("run_id", run_id) \
                    .execute()
                step = "bulk_deltas"
                self._rpc("fn_bulk_insert_menu_deltas", {"p_deltas": delta_rows})

        except Exception as exc:
            print(f"[Supabase] WARN branch={branch_id} step={step} failed: {exc}")

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _build_delta_rows(self, snap: dict, run_id: str) -> list[dict]:
        """Convert snap.delta structure into rows for fn_bulk_insert_menu_deltas."""
        rows          = []
        branch_id     = snap["branch_id"]
        restaurant_id = snap.get("restaurant_id")   # parent brand — enables chain-level analysis
        ts            = snap.get("scraped_at")
        delta         = snap.get("delta") or {}

        for it in delta.get("added", []):
            rows.append({
                "branch_id":        branch_id,
                "restaurant_id":    restaurant_id,
                "run_id":           run_id,
                "change_type":      "ADDED",
                "item_name":        it.get("item_name") or "",
                "item_key":         it.get("item_key") or "",
                "menu_category":    it.get("category") or "",
                "field_changed":    "new_item",
                "old_value":        None,
                "new_value":        None,
                "old_price_aed":    None,
                "new_price_aed":    _safe_num(it.get("price_aed")),
                "price_change_pct": None,
                "detected_at":      ts,
                "baseline_run_id":  None,
            })

        for it in delta.get("removed", []):
            rows.append({
                "branch_id":        branch_id,
                "restaurant_id":    restaurant_id,
                "run_id":           run_id,
                "change_type":      "REMOVED",
                "item_name":        it.get("item_name") or "",
                "item_key":         it.get("item_key") or "",
                "menu_category":    it.get("category") or "",
                "field_changed":    "removed_item",
                "old_value":        None,
                "new_value":        None,
                "old_price_aed":    _safe_num(it.get("price_aed")),
                "new_price_aed":    None,
                "price_change_pct": None,
                "detected_at":      ts,
                "baseline_run_id":  None,
            })

        for ch in delta.get("changed", []):
            for field, vals in ch.get("field_changes", {}).items():
                old_v = vals.get("old")
                new_v = vals.get("new")
                pct   = None
                if field == "price_aed" and old_v and new_v:
                    try:
                        pct = _safe_num(round(((float(new_v) - float(old_v)) / float(old_v)) * 100, 2))
                    except (ZeroDivisionError, ValueError):
                        pass
                rows.append({
                    "branch_id":        branch_id,
                    "restaurant_id":    restaurant_id,
                    "run_id":           run_id,
                    "change_type":      "CHANGED",
                    "item_name":        ch.get("item_name") or ch.get("item_key") or "",
                    "item_key":         ch.get("item_key") or "",
                    "menu_category":    ch.get("category") or "",
                    "field_changed":    _FIELD_LABEL.get(field, field),
                    "old_value":        str(old_v) if old_v is not None else None,
                    "new_value":        str(new_v) if new_v is not None else None,
                    "old_price_aed":    _safe_num(old_v) if field == "price_aed" else None,
                    "new_price_aed":    _safe_num(new_v) if field == "price_aed" else None,
                    "price_change_pct": pct,
                    "detected_at":      ts,
                    "baseline_run_id":  None,
                })

        return rows

    def _rpc(self, fn_name: str, params: dict):
        import time as _time
        last_exc = None
        for attempt in range(4):
            try:
                return self._client.rpc(fn_name, params).execute()
            except Exception as exc:
                last_exc = exc
                err_str = str(exc)
                # Retry on transient network/socket errors (HTTP/2 resets, Windows WSAEWOULDBLOCK, etc.)
                transient = (
                    "ConnectionTerminated" in err_str
                    or "RemoteProtocolError" in err_str
                    or "WinError 10035" in err_str   # WSAEWOULDBLOCK on Windows
                    or "WinError 10054" in err_str   # WSAECONNRESET
                    or "h2" in err_str.lower()
                    or "timed out" in err_str.lower()
                )
                if transient and attempt < 3:
                    _time.sleep(1.0 * (attempt + 1))
                    continue
                raise
        raise last_exc
