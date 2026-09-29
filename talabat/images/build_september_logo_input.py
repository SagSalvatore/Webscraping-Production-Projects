"""Build the September logo-scrape input list.

scrape_menu_images.py expects rows of {map_url, source_id, chain_id,
restaurant_name}. September's restaurant list and its URLs live apart:

    unified/data/talabat_unified_202609.jsonl   the SHIPPED records (name, chain_id)
    september/data/sept_menu_targets.jsonl      branch_id, url (with ?aid=)

Joined on source_id == branch_id.

SCOPE IS THE SHIPPED DELIVERABLE: the 1,046 September restaurants exactly as
Tech holds them in talabat_unified_202609.jsonl (names after the build's text
scrub), not the raw September cohort. September ships one record per
restaurant, so there are no verified-location repeats to drop.

Test accounts: August's deliverable carried Talabat's own internal test
records ('Test Order Plugin', 'Pos Test 20'). They are REPORTED here, judged by
menu size, never removed - the deliverable is already published.

The ?aid= query parameter is load-bearing on Talabat URLs; it is asserted, not
assumed.

    python build_september_logo_input.py
"""
import json
import re
import sys
from pathlib import Path

import ijson
import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UNIFIED = ROOT / "unified" / "data" / "talabat_unified_202609.jsonl"
SEPT = ROOT / "september" / "data" / "September_export.json"
TARGETS = ROOT / "september" / "data" / "sept_menu_targets.jsonl"
OUT = HERE / "data" / "september_logo_targets.jsonl"
TEST_RX = re.compile(r"\btest\b|dont-touch|do-not-touch", re.I)

sys.stdout.reconfigure(encoding="utf-8")


def main():
    print("=" * 70)
    print("  BUILD SEPTEMBER LOGO INPUT")
    print("=" * 70)
    urls = {}
    for line in open(TARGETS, "rb"):
        r = orjson.loads(line)
        urls[str(r["branch_id"])] = r.get("url")
    print(f"  September URL targets   : {len(urls):,}")

    sept_ids = {str(r["source_id"]) for r in ijson.items(open(SEPT, "rb"), "item")}
    print(f"  September restaurants   : {len(sept_ids):,}")

    rows, missing, tests, seen = [], [], [], set()
    for line in open(UNIFIED, "rb"):
        r = orjson.loads(line)
        sid = str(r["source_id"])
        if sid not in sept_ids or r.get("is_verified_location") or sid in seen:
            continue
        seen.add(sid)
        u = urls.get(sid)
        if not u:
            missing.append(sid)
            continue
        rows.append({"source_id": sid, "map_url": u, "chain_id": r.get("chain_id"),
                     "restaurant_name": r.get("name")})
        if TEST_RX.search(str(r.get("name"))) or TEST_RX.search(u):
            tests.append((sid, r.get("name"), len(r.get("menu_items") or []), u))

    print(f"  shipped September rows  : {len(seen):,}")
    print(f"  targets built           : {len(rows):,}")
    if missing:
        print(f"  NO URL for              : {len(missing):,}  e.g. {missing[:5]}")
    no_aid = [r for r in rows if "?aid=" not in (r["map_url"] or "")]
    print(f"  targets missing ?aid=   : {len(no_aid):,}"
          + ("   <-- these return no page state" if no_aid else "   OK"))
    assert not no_aid, "URLs without ?aid= would silently return nothing"
    assert len({r["source_id"] for r in rows}) == len(rows), "duplicate source_id"
    assert len(seen) == len(sept_ids), "shipped September count differs from the export"

    print(f"\n  possible Talabat test accounts (reported, NOT removed): {len(tests)}")
    for sid, name, n_items, u in tests:
        print(f"    {sid:>8}  {name!r:36} {n_items:>4} menu items  {u}")

    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n  -> {OUT.name}  ({len(rows):,} rows)")
    print(f"  sample: {json.dumps(rows[0], ensure_ascii=False)[:130]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
