"""Build the August logo-scrape input list.

scrape_menu_images.py expects rows of {map_url, source_id, chain_id,
restaurant_name}. July's came from restaurant_urls_for_scraping.jsonl; August's
have to be assembled, because the restaurant list and the URLs live apart:

    August_export.json                 source_id, name, chain_id  (no URL)
    talabat_genuinely_new_brands.jsonl branch_id, url             (no chain_id)

Joined on source_id == branch_id.

SCOPE IS THE SHIPPED DELIVERABLE, not the raw cohort. Only base records are
taken from August_export.json - verified-location records repeat their
representative's source_id, and a logo is per restaurant, so including them
would queue the same page several times. The 111 restaurants with no menu were
already dropped from the export and stay dropped here.

The ?aid= query parameter is load-bearing on Talabat URLs; it is asserted, not
assumed.

    python build_august_logo_input.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXPORT = ROOT / "August_menu" / "data" / "August_export.json"
LISTING = ROOT / "listing_comparison" / "output" / "talabat_genuinely_new_brands.jsonl"
OUT = HERE / "data" / "august_logo_targets.jsonl"

sys.stdout.reconfigure(encoding="utf-8")


def main():
    print("=" * 70)
    print("  BUILD AUGUST LOGO INPUT")
    print("=" * 70)

    urls = {}
    with open(LISTING, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                urls[str(r["branch_id"])] = r.get("url")
    print(f"  listing rows with a URL : {len(urls):,}")

    d = json.loads(EXPORT.read_text(encoding="utf-8"))
    base = [r for r in d if not r.get("is_verified_location")]
    print(f"  August export records   : {len(d):,}  (base {len(base):,})")

    rows, missing = [], []
    seen = set()
    for r in base:
        sid = str(r["source_id"])
        if sid in seen:
            continue
        seen.add(sid)
        u = urls.get(sid)
        if not u:
            missing.append(sid)
            continue
        rows.append({"source_id": sid, "map_url": u,
                     "chain_id": r.get("chain_id"),
                     "restaurant_name": r.get("name")})

    print(f"  targets built           : {len(rows):,}")
    if missing:
        print(f"  NO URL in the listing   : {len(missing):,}  e.g. {missing[:5]}")

    no_aid = [r for r in rows if "?aid=" not in (r["map_url"] or "")]
    print(f"  targets missing ?aid=   : {len(no_aid):,}"
          + ("   <-- these return no page state" if no_aid else "   OK"))
    assert not no_aid, "URLs without ?aid= would silently return nothing"
    assert len({r['source_id'] for r in rows}) == len(rows), "duplicate source_id"

    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n  -> {OUT.name}  ({len(rows):,} rows)")
    print(f"  sample: {json.dumps(rows[0], ensure_ascii=False)[:130]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
