"""
data_summary.py — Complete analytics summary of scraped Talabat data.
Single async RPC call to fn_summary_all() — all sections returned in one shot.

Usage:
  cd talabat/menu
  python data_summary.py                         # full summary (all sections)
  python data_summary.py --section overview
  python data_summary.py --section categories
  python data_summary.py --section areas
  python data_summary.py --section restaurants
  python data_summary.py --section prices
  python data_summary.py --section cuisines
  python data_summary.py --section types
  python data_summary.py --top 30                # top/bottom N restaurants (default 20)
  python data_summary.py --export                # save to data/summary/summary_*.txt
"""

import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import asyncio
import os
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")

W = 72  # display width


# ── async fetch via supabase-py ───────────────────────────────────────────────

async def fetch_summary(top_n: int) -> dict:
    try:
        from supabase._async.client import AsyncClient, create_client
    except ImportError:
        try:
            from supabase import acreate_client as create_client, AsyncClient
        except ImportError:
            print("ERROR: supabase not installed — pip install supabase")
            sys.exit(1)

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL + SUPABASE_ANON_KEY must be set in talabat/.env")
        sys.exit(1)

    client: AsyncClient = await create_client(SUPABASE_URL, SUPABASE_KEY)
    # Single instant read — cache row was pre-computed via fn_refresh_summary / MCP
    resp = await client.rpc("fn_get_summary").execute()
    # supabase-py v2 uses .aclose() on the underlying httpx client
    try:
        await client.aclose()
    except AttributeError:
        pass

    data = resp.data or {}
    # Slice top_n for restaurant lists (cache stores 50, we display top_n)
    if "top_restaurants" in data and data["top_restaurants"]:
        data["top_restaurants"] = data["top_restaurants"][:top_n]
    if "bottom_restaurants" in data and data["bottom_restaurants"]:
        data["bottom_restaurants"] = data["bottom_restaurants"][:top_n]
    return data


# ── display helpers ───────────────────────────────────────────────────────────

def hdr(title: str):
    pad = max(0, (W - len(title) - 2) // 2)
    print("\n" + "=" * W)
    print(" " * pad + f" {title} ")
    print("=" * W)


def sub(title: str):
    print(f"\n  {'─' * (W - 4)}")
    print(f"  {title}")
    print(f"  {'─' * (W - 4)}")


def rf(label: str, value, width: int = 36) -> str:
    return f"  {label:<{width}} {value}"


def tbl(rows: list[dict], cols: list[tuple], indent: int = 2):
    """cols = [(key, header, width, 'l'|'r'), ...]"""
    if not rows:
        print(f"{'  ' * indent}(no data)")
        return
    sp = " " * indent
    header = "  ".join(
        f"{h:>{w}}" if a == "r" else f"{h:<{w}}" for _, h, w, a in cols
    )
    print(f"{sp}{header}")
    print(f"{sp}{'-' * len(header)}")
    for r in rows:
        line = "  ".join(
            f"{str(r.get(k) or '')[:w]:>{w}}" if a == "r"
            else f"{str(r.get(k) or '')[:w]:<{w}}"
            for k, _, w, a in cols
        )
        print(f"{sp}{line}")


# ── render functions ──────────────────────────────────────────────────────────

def render_overview(d: dict):
    hdr("OVERVIEW")
    s = d.get("overview", d)  # flat cache or nested

    total   = int(s["total_restaurants"] or 0)
    scraped = int(s["scraped_restaurants"] or 0)
    pending = int(s["pending_restaurants"] or 0)
    pct     = scraped / total * 100 if total else 0
    fill    = int(pct / 2)
    bar     = "[" + "█" * fill + "░" * (50 - fill) + "]"

    print(rf("Total restaurants in DB :", f"{total:,}"))
    print(rf("Scraped (have menu)     :", f"{scraped:,}  ({pct:.1f}%)"))
    print(rf("Pending (no menu yet)   :", f"{pending:,}"))
    print(f"  {bar} {pct:.1f}%")
    print()
    print(rf("Total menu items        :", f"{int(s['total_items'] or 0):,}"))
    print(rf("Unique menu categories  :", f"{int(s['unique_categories'] or 0):,}"))
    print(rf("Unique areas            :", f"{int(s['unique_areas'] or 0):,}"))
    print()
    print(rf("Avg price (AED)         :", f"{s['avg_price'] or 'N/A'}"))
    print(rf("Median price (AED)      :", f"{s['median_price'] or 'N/A'}"))
    print(rf("Min price (AED)         :", f"{s['min_price'] or 'N/A'}"))
    print(rf("Max price (AED)         :", f"{s['max_price'] or 'N/A'}"))
    print()
    print(rf("Avg items / restaurant  :", f"{s['avg_items_per_rest'] or 'N/A'}"))
    print(rf("Min items / restaurant  :", f"{s['min_items_per_rest'] or 'N/A'}"))
    print(rf("Max items / restaurant  :", f"{s['max_items_per_rest'] or 'N/A'}"))
    print(f"\n  Generated : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")


def render_categories(d: dict):
    hdr("MENU CATEGORIES  (top 40 by item count)")
    tbl(d.get("categories") or [], [
        ("category",    "Category",     32, "l"),
        ("items",       "Items",         8, "r"),
        ("pct",         "  %",           5, "r"),
        ("restaurants", "Restaurants",  12, "r"),
        ("avg_price",   "Avg AED",       8, "r"),
        ("min_price",   "Min",           7, "r"),
        ("max_price",   "Max",           7, "r"),
    ])
    no_cat = int(d.get("no_category") or 0)
    if no_cat:
        print(f"\n  Items with no category: {no_cat:,}")


def render_areas(d: dict):
    hdr("AREA / CITY BREAKDOWN  (top 40 by restaurants)")
    tbl(d.get("areas") or [], [
        ("area_name",   "Area / City",  30, "l"),
        ("restaurants", "Restaurants",  12, "r"),
        ("total_items", "Menu Items",   10, "r"),
        ("avg_price",   "Avg AED",       8, "r"),
        ("categories",  "Categories",   10, "r"),
    ])
    pending = d.get("areas_pending") or []
    if pending:
        sub("Areas with restaurants but NO menu items scraped yet")
        tbl(pending, [
            ("area_name",   "Area",    44, "l"),
            ("restaurants", "Pending", 10, "r"),
        ])
    else:
        print("\n  All areas have been scraped!")


def render_restaurants(d: dict, top_n: int):
    hdr(f"RESTAURANTS — TOP {top_n} BY MENU ITEM COUNT")
    tbl((d.get("top_restaurants") or [])[:top_n], [
        ("restaurant_name", "Restaurant", 30, "l"),
        ("area_name",       "Area",       20, "l"),
        ("items",           "Items",       6, "r"),
        ("categories",      "Cats",        5, "r"),
        ("avg_price",       "Avg AED",     8, "r"),
        ("min_price",       "Min",         6, "r"),
        ("max_price",       "Max",         6, "r"),
    ])
    sub(f"BOTTOM {top_n} — fewest menu items")
    tbl((d.get("bottom_restaurants") or [])[:top_n], [
        ("restaurant_name", "Restaurant", 38, "l"),
        ("area_name",       "Area",       22, "l"),
        ("items",           "Items",       6, "r"),
        ("avg_price",       "Avg AED",     8, "r"),
    ])


def render_prices(d: dict):
    hdr("PRICE ANALYSIS")

    sub("Price band distribution")
    for b in (d.get("price_bands") or []):
        bar = "█" * int(float(b.get("pct") or 0) / 2)
        print(f"  {str(b['band']):<22}  {bar:<26}  {b['pct']:>5}%  ({int(b['items'] or 0):,} items)")

    sub("Avg price by area  (top 20 most expensive, min 3 restaurants)")
    tbl(d.get("price_by_area") or [], [
        ("area_name",   "Area",         34, "l"),
        ("avg_price",   "Avg AED",       8, "r"),
        ("restaurants", "Restaurants",  12, "r"),
    ])

    sub("Avg price by category  (top 20 most expensive, min 10 items)")
    tbl(d.get("price_by_cat") or [], [
        ("category",  "Category",  34, "l"),
        ("avg_price", "Avg AED",    8, "r"),
        ("items",     "Items",      8, "r"),
    ])


def render_cuisines(d: dict):
    hdr("CUISINE BREAKDOWN  (serves_cuisine field, top 40)")
    rows = d.get("cuisines") or []
    if not rows:
        print("  No cuisine data found.")
        return
    mx = max(int(r.get("restaurants") or 0) for r in rows) or 1
    for r in rows:
        n = int(r.get("restaurants") or 0)
        bar = "█" * int(n / mx * 40)
        print(f"  {str(r['cuisine']):<30}  {bar:<42}  {n:,}")


def render_types(d: dict):
    hdr("CLASSIFICATION BREAKDOWN")

    sub("Restaurant type  (FSR / QSR / Cafe / Bakery / Cloud Kitchen)")
    tbl(d.get("rest_types") or [], [
        ("label",       "Type",         28, "l"),
        ("restaurants", "Restaurants",  12, "r"),
        ("pct",         "%",             6, "r"),
    ])

    sub("Outlet type  (Independent / Chain)")
    tbl(d.get("outlet_types") or [], [
        ("label",       "Outlet Type",  20, "l"),
        ("restaurants", "Restaurants",  12, "r"),
        ("pct",         "%",             6, "r"),
    ])

    sub("Chained outlet type  (Local Chain / MNC Chain / N/A)")
    tbl(d.get("chained_types") or [], [
        ("label",       "Chained Type", 22, "l"),
        ("restaurants", "Restaurants",  12, "r"),
        ("pct",         "%",             6, "r"),
    ])


# ── entrypoint ────────────────────────────────────────────────────────────────

ALL_SECTIONS = ["overview", "categories", "areas", "restaurants", "prices", "cuisines", "types"]

RENDER = {
    "overview":    render_overview,
    "categories":  render_categories,
    "areas":       render_areas,
    "prices":      render_prices,
    "cuisines":    render_cuisines,
    "types":       render_types,
}


async def run(sections: list[str], top_n: int, export: bool):
    out_file = None
    if export:
        export_dir = Path(__file__).resolve().parent / "data" / "summary"
        export_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = export_dir / f"summary_{ts}.txt"
        out_file = open(out_path, "w", encoding="utf-8")

        class Tee:
            def __init__(self, *files): self.files = files
            def write(self, s):
                for f in self.files: f.write(s)
            def flush(self):
                for f in self.files: f.flush()
            @property
            def encoding(self): return "utf-8"
            @property
            def errors(self): return "replace"

        sys.stdout = Tee(sys.__stdout__, out_file)

    print("Fetching summary from Supabase ...")
    t0 = time.perf_counter()
    data = await fetch_summary(top_n)
    elapsed = time.perf_counter() - t0
    print(f"Data fetched in {elapsed:.2f}s\n")

    for sec in sections:
        if sec == "restaurants":
            render_restaurants(data, top_n)
        else:
            RENDER[sec](data)

    print("\n" + "=" * W + "\n")

    if out_file:
        out_file.close()
        sys.stdout = sys.__stdout__
        print(f"Summary exported → {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Talabat data analytics summary",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Sections: overview | categories | areas | restaurants | prices | cuisines | types
            Examples:
              python data_summary.py
              python data_summary.py --section areas
              python data_summary.py --section restaurants --top 30
              python data_summary.py --export
        """),
    )
    parser.add_argument("--section", choices=ALL_SECTIONS,
                        help="Run only one section (default: all)")
    parser.add_argument("--top",    type=int, default=20,
                        help="Top/bottom N restaurants (default 20)")
    parser.add_argument("--export", action="store_true",
                        help="Save output to data/summary/summary_YYYYMMDD_HHMMSS.txt")
    args = parser.parse_args()

    sections = [args.section] if args.section else ALL_SECTIONS
    asyncio.run(run(sections, args.top, args.export))


if __name__ == "__main__":
    main()
