"""Step 3 - replace the CRAWL-ZONE area with the venue's ACTUAL area.

The bug: `area_name` in the crawl output is the delivery zone we searched FROM
(the ?aid= parameter), not where the restaurant is. Sour Mango is stored as
"Al Fisht" (aid=1511) but sits at 25.30695, 55.37761, which is Al Nahda in
Sharjah - confirmed independently by Nominatim, by the page's own `areaName`
key, and by its URL slug `sour-mango-al-nahda`.

Scale of the problem: 94% of sampled rows have a stored area that does not
appear in their own slug.

Note the JSON-LD `address.addressLocality` is ALSO wrong - it echoes the crawl
zone ("Al Fisht"), so it cannot be used as the fix. The trustworthy signals are
the coordinates and the separate `areaName` key.

Chosen source: Nominatim reverse geocoding of coordinates we already hold.
Free, authoritative, and needs no proxy credits or re-fetch (re-fetching 5,709
pages would burn Oxylabs credit for data the coordinates already give us).
The URL slug is used as a free corroboration signal.

Scope: only category == genuinely_new_brand, per Sagar - the other categories
are branches/re-listings of chains already in the universe.

    python fix_area_names.py --dry-run
    python fix_area_names.py
"""
import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import httpx
from loguru import logger

from common import OUT, norm_name, write_csv, write_json

sys.stdout.reconfigure(encoding="utf-8")

SRC = OUT / "run2_identity_classification.csv"
CACHE = OUT / "geocode_cache.json"
LOGD = Path(__file__).resolve().parent / "logs"
LOGD.mkdir(exist_ok=True)

UA = {"User-Agent": "MordorIntelligence-AreaFix/1.0 (data quality research)"}
DELAY = 1.1                      # Nominatim policy: max 1 req/sec
CHECKPOINT_EVERY = 25

# most specific first; 'suburb' is what carries the UAE community name
FIELDS = ["suburb", "quarter", "neighbourhood", "city_district", "residential",
          "village", "town"]
CITIES = {"dubai", "abu dhabi", "sharjah", "ajman", "fujairah", "al ain",
          "umm al quwain", "ras al khaimah", "united arab emirates", "uae"}
BAD = re.compile(r"\b(street|st|road|rd|highway|interchange|e\s?\d+)\b", re.I)


def pick_area(addr: dict):
    for f in FIELDS:
        v = (addr.get(f) or "").strip()
        if v and v.lower() not in CITIES and not BAD.search(v) and len(v) > 2:
            return v, f
    return None, None


def pick_city(addr: dict):
    for f in ("city", "town", "state", "county"):
        v = (addr.get(f) or "").strip()
        v = re.sub(r"\s*Emirate$", "", v).strip()
        if v.lower() in CITIES:
            return v
    return None


def slug_of(url: str) -> str:
    m = re.search(r"/restaurant/\d+/([^/?#]+)", url or "")
    return m.group(1).replace("-", " ") if m else ""


def load_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("cache unreadable - starting fresh")
    return {}


def save_cache(c):
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE)


def main(args):
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
    logger.add(LOGD / "fix_area.log", level="DEBUG", encoding="utf-8", rotation="10 MB")

    rows = [r for r in csv.DictReader(open(SRC, encoding="utf-8-sig"))
            if r["category"] == "genuinely_new_brand"]
    logger.info(f"genuinely_new_brand rows: {len(rows):,}")

    todo = {}
    for r in rows:
        try:
            k = f"{round(float(r['lat']),5)},{round(float(r['lon']),5)}"
        except (TypeError, ValueError):
            continue
        todo.setdefault(k, []).append(r)
    logger.info(f"distinct coordinates: {len(todo):,}")

    cache = load_cache()
    missing = [k for k in todo if k not in cache]
    logger.info(f"cached {len(todo)-len(missing):,} | to geocode {len(missing):,} "
                f"(~{len(missing)*DELAY/60:.0f} min)")

    if args.dry_run:
        logger.warning("--dry-run: no requests made")
        return 0

    if missing:
        t0 = time.time()
        with httpx.Client(headers=UA, timeout=40) as client:
            for i, k in enumerate(missing, 1):
                lat, lon = k.split(",")
                try:
                    resp = client.get("https://nominatim.openstreetmap.org/reverse",
                                      params={"lat": lat, "lon": lon, "format": "json",
                                              "zoom": 16, "addressdetails": 1,
                                              "accept-language": "en"})
                    cache[k] = (resp.json() or {}).get("address", {}) if resp.status_code == 200 else {}
                except Exception as exc:
                    logger.warning(f"{k}: {type(exc).__name__}")
                    cache[k] = {}
                time.sleep(DELAY)
                if i % CHECKPOINT_EVERY == 0:
                    save_cache(cache)
                    el = time.time() - t0
                    eta = (len(missing) - i) * el / i
                    logger.info(f"  {i:,}/{len(missing):,} ({i/len(missing)*100:.0f}%) "
                                f"ETA {eta/60:.0f} min")
        save_cache(cache)

    out, stats = [], Counter()
    for k, group in todo.items():
        addr = cache.get(k) or {}
        area, field = pick_area(addr)
        city = pick_city(addr)
        for r in group:
            slug = slug_of(r["url"])
            agrees = bool(area) and norm_name(area, strip_branch=False) in \
                norm_name(slug, strip_branch=False)
            stats["resolved" if area else "unresolved"] += 1
            if area:
                stats["slug_agrees" if agrees else "slug_differs"] += 1
            out.append({
                "branch_id": r["branch_id"], "restaurant_id": r["restaurant_id"],
                "name": r["name"], "url": r["url"],
                "lat": r["lat"], "lon": r["lon"],
                "area_crawl_zone_WRONG": r["area_name"],
                "area_corrected": area or "",
                "city_corrected": city or "",
                "osm_field": field or "",
                "slug_agrees": agrees,
                "changed": bool(area) and area.strip().lower() != (r["area_name"] or "").strip().lower(),
            })

    changed = sum(1 for o in out if o["changed"])
    logger.success(f"resolved {stats['resolved']:,}/{len(out):,} | "
                   f"area CHANGED on {changed:,} rows")
    logger.info(f"  slug corroborates: {stats['slug_agrees']:,} | "
                f"differs: {stats['slug_differs']:,}")
    logger.info(f"  unresolved (kept blank): {stats['unresolved']:,}")

    write_csv(OUT / "genuinely_new_area_corrected.csv", out)
    with open(OUT / "genuinely_new_area_corrected.jsonl", "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    write_json(OUT / "area_fix_summary.json", {
        "rows": len(out), "resolved": stats["resolved"],
        "unresolved": stats["unresolved"], "changed": changed,
        "slug_agrees": stats["slug_agrees"], "slug_differs": stats["slug_differs"],
    })

    logger.info("\n  sample corrections:")
    for o in [o for o in out if o["changed"]][:10]:
        logger.info(f"    {o['name'][:26]:28} {o['area_crawl_zone_WRONG'][:18]:20} -> "
                    f"{o['area_corrected'][:22]:24} ({o['city_corrected']})")
    print(f"\n  -> output/genuinely_new_area_corrected.csv")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
