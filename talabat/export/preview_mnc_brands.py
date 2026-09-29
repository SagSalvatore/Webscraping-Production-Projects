"""
Fast preview: build IT-schema JSON records for ONLY the 326 branches matching
the 26 tracked MNC brands, reusing the exact same logic as export_to_json.py
(build_mnc_overrides / fetch_batch / build_record) — without re-running the
full 15,768-restaurant / ~10-minute export.

Once this preview looks right, re-run export_to_json.py to regenerate the
full talabat_export.json with the same logic applied to every restaurant.

Run:
  python talabat/export/preview_mnc_brands.py
"""

import asyncio
import re
from pathlib import Path

import asyncpg
import orjson
from loguru import logger

import export_to_json as exp

PREVIEW_PATH = Path(__file__).parent / "mnc_brands_preview.json"


async def main():
    pool = await asyncpg.create_pool(exp.DB_DSN, min_size=2, max_size=4, init=exp._init_connection)

    overrides, branch_to_brand, brand_locations_full = await exp.build_mnc_overrides(pool)
    item_key_ingredients_map = await exp.build_item_key_ingredients_map(pool)

    async with pool.acquire() as conn:
        talabat_rows = await conn.fetch("SELECT branch_id, restaurant_name FROM talabat_restaurants")

    def matches_any_brand(name):
        norm = exp.normalize_for_match(name)
        return any(re.search(pattern, norm) for _, pattern in exp.MNC_BRAND_CONFIG)

    excluded_ids = exp.load_excluded_branch_ids()
    talabat_rows = [r for r in talabat_rows if r["branch_id"] not in excluded_ids]
    # chain_id/counts computed over the FULL exported set (matches what the real
    # production run would produce), even though this preview only writes out
    # the MNC-matched subset.
    chain_ids = exp.build_chain_ids(talabat_rows, branch_to_brand)

    branch_ids = [r["branch_id"] for r in talabat_rows if matches_any_brand(r["restaurant_name"])]
    logger.info(f"Branches matching one of the 26 MNC brands (excluding {len(excluded_ids)} flagged empty-menu branches): {len(branch_ids)}")

    sem = asyncio.Semaphore(4)
    rows = await exp.fetch_batch(pool, sem, branch_ids)

    records = [exp.build_record(r, overrides, branch_to_brand, brand_locations_full, item_key_ingredients_map, chain_ids) for r in rows]

    # Every verified-location entry for a brand reuses ONE representative
    # branch's menu_items (these addresses were never independently scraped,
    # and IT's upload pipeline rejects entries with no menu at all) — prefer
    # a branch that actually HAS a non-empty menu rather than just the first
    # one encountered, so a brand doesn't silently end up with menu_items=[]
    # across all of its location entries.
    candidates_by_brand = {}
    for r in records:
        if r["name"] in brand_locations_full:
            candidates_by_brand.setdefault(r["name"], []).append(r)
    brand_representative = {}
    for name, candidates in candidates_by_brand.items():
        best = next((r for r in candidates if r["menu_items"]), candidates[0])
        if not best["menu_items"]:
            logger.warning(f"No branch with a non-empty menu found for brand '{name}' ({len(candidates)} branches checked)")
        brand_representative[name] = {
            "source_name": best["source_name"], "source_id": best["source_id"], "name": best["name"],
            "cuisine": best["cuisine"], "sub_cuisines": best["sub_cuisines"], "key_cuisines": best["key_cuisines"],
            "restaurant_type": best["restaurant_type"], "outlet_type": best["outlet_type"],
            "chain_type": best["chain_type"], "chain_id": best["chain_id"], "menu_items": best["menu_items"],
        }
    location_entries = exp.build_verified_location_entries(brand_locations_full, brand_representative)

    by_brand = {}
    for r in records:
        by_brand.setdefault(r["name"], []).append(r)
    for brand, group in sorted(by_brand.items(), key=lambda x: -len(x[1])):
        loc_count = sum(1 for e in location_entries if e["name"] == brand)
        print(f"  {brand}: {len(group)} branch entries + {loc_count} verified-location entries")

    # Verified-location entries (lean, chain_id-linked, no menu) come FIRST;
    # the branch entries (full menu_items) come LAST — so reviewing the new
    # location data doesn't require scrolling past every branch's full menu.
    all_out = location_entries + records
    with open(PREVIEW_PATH, "wb") as f:
        f.write(orjson.dumps(all_out, option=orjson.OPT_INDENT_2))

    await pool.close()
    logger.info(
        f"Saved {len(records)} branch entries + {len(location_entries)} verified-location entries "
        f"= {len(all_out)} total -> {PREVIEW_PATH} ({PREVIEW_PATH.stat().st_size/1e6:.2f} MB)"
    )


if __name__ == "__main__":
    asyncio.run(main())
