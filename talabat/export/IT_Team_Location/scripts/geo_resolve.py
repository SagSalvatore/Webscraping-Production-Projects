"""Resolve the last unfixable rows by REVERSE GEOCODING their coordinates.

Better than any web search for this problem: 'Sheikh Zayed Road' has no single
containing district, but the *branch's own lat/lng* does. talabat_export.json
already carries per-branch `geo`, so nothing needs to be searched for - only
looked up.

Coordinates are joined per BRANCH on (source_id, original area text), never per
source_id: a chain shares one source_id across branches in different cities.
Verified before use - KFC's 6 review rows carry 6 distinct coordinates.

Uses OpenStreetMap Nominatim: free, no key, authoritative. Their usage policy
caps this at 1 request/second, so it runs sequentially with a 1.1s delay - ~90s
for 82 rows. Do not parallelise it.

    python geo_resolve.py --dry-run   coverage only, no requests
    python geo_resolve.py             run
"""
import argparse
import csv
import re
import shutil
import sys
import time
from collections import Counter, defaultdict

import httpx
import orjson
from loguru import logger

from config import CACHE_DIR, INPUT_JSON, LOG_DIR, OUTPUT_DIR, ROOT

sys.stdout.reconfigure(encoding="utf-8")
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
logger.add(LOG_DIR / "geo_resolve.log", level="DEBUG", encoding="utf-8")

FINAL = OUTPUT_DIR / "ri-db.restaurants_id.final.json"
REVIEW = OUTPUT_DIR / "area_needs_review.csv"
EXPORT = ROOT.parent / "talabat_export.json"
CACHE = CACHE_DIR / "geocode_cache.json"

UA = {"User-Agent": "MordorIntelligence-AreaCleanup/1.0 (data quality research)"}
DELAY = 1.1                       # Nominatim policy: max 1 req/sec

# most specific first - 'suburb' is what carries the UAE community name
FIELDS = ["suburb", "quarter", "neighbourhood", "city_district", "residential",
          "village", "town"]

CITIES = {"dubai", "abu dhabi", "sharjah", "ajman", "fujairah", "al ain",
          "umm al quwain", "ras al khaimah", "united arab emirates", "uae"}
BAD = re.compile(r"\b(street|st|road|rd|highway|e\s?\d+|interchange)\b", re.I)
NO_ART = {"saadiyat island", "meydan", "mirdif", "business bay", "jumeirah",
          "barsha heights", "dubai marina", "zayed sports city"}


def pick(address):
    for f in FIELDS:
        v = (address.get(f) or "").strip()
        if v and v.lower() not in CITIES and not BAD.search(v) and len(v) > 2:
            return v, f
    return None, None


def load_cache():
    if CACHE.exists():
        try:
            return orjson.loads(CACHE.read_bytes())
        except Exception:
            pass
    return {}


def save_cache(c):
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_bytes(orjson.dumps(c, option=orjson.OPT_INDENT_2))
    tmp.replace(CACHE)


def main(args):
    rows = list(csv.DictReader(open(REVIEW, encoding="utf-8-sig")))
    records = orjson.loads(FINAL.read_bytes())
    original = {o["_id"]["$oid"]: o for o in orjson.loads(INPUT_JSON.read_bytes())}

    # branch-level coordinates
    coords = {}
    for o in orjson.loads(EXPORT.read_bytes()):
        g = o.get("geo") or {}
        if g.get("lat") and g.get("lng"):
            k = (o["source_id"], ((o.get("location") or {}).get("area") or "").strip())
            coords.setdefault(k, (float(g["lat"]), float(g["lng"])))

    todo = []
    for r in rows:
        oa = ((original[r["_id"]].get("location") or {}).get("area") or "").strip()
        c = coords.get((r["source_id"], oa))
        if c:
            todo.append((r, c))
    logger.info(f"{len(rows)} review rows | {len(todo)} have branch-level coordinates")

    if args.dry_run:
        for r, c in todo[:12]:
            logger.info(f"    {r['name'][:26]:28} {r['area_current'][:24]:26} {c}")
        logger.warning("--dry-run: no requests made")
        return

    cache = load_cache()
    resolved, failed = {}, []
    with httpx.Client(headers=UA, timeout=40) as client:
        for i, (r, (lat, lon)) in enumerate(todo, 1):
            key = f"{lat:.6f},{lon:.6f}"
            if key not in cache:
                try:
                    resp = client.get("https://nominatim.openstreetmap.org/reverse",
                                      params={"lat": lat, "lon": lon, "format": "json",
                                              "zoom": 16, "addressdetails": 1,
                                              "accept-language": "en"})
                    cache[key] = (resp.json() or {}).get("address", {}) if resp.status_code == 200 else {}
                except Exception as exc:
                    logger.warning(f"geocode failed {key}: {type(exc).__name__}")
                    cache[key] = {}
                time.sleep(DELAY)
                if i % 10 == 0:
                    save_cache(cache)
                    logger.info(f"  {i}/{len(todo)} geocoded")
            area, field = pick(cache[key] or {})
            if area:
                resolved[r["_id"]] = (area, field, key)
            else:
                failed.append(r)
    save_cache(cache)
    logger.success(f"reverse-geocoded {len(resolved)}/{len(todo)}")

    # apply
    applied = []
    for rec in records:
        rid = rec["_id"]["$oid"]
        if rid in resolved:
            area, field, key = resolved[rid]
            applied.append((rec["location"]["area"], area, field, key))
            rec["location"]["area"] = area

    # re-assert the Al-prefix convention on anything new
    cnt = Counter((r["location"] or {}).get("area") or "" for r in records)
    cnt.pop("", None)
    g = defaultdict(list)
    for k, v in cnt.items():
        g[re.sub(r"^al\s+", "", k.lower()).strip()].append((k, v))
    fix = {}
    for key, var in g.items():
        if len(var) < 2 or key in NO_ART:
            continue
        al = [x for x in var if x[0].lower().startswith("al ")]
        bare = [x for x in var if not x[0].lower().startswith("al ")]
        if al and bare:
            t = max(al, key=lambda x: x[1])[0]
            for b, _ in bare:
                fix[b] = t
    n_fix = 0
    for rec in records:
        a = rec["location"].get("area")
        if a in fix:
            rec["location"]["area"] = fix[a]
            n_fix += 1
    if fix:
        logger.info(f"re-applied Al-prefix to {n_fix} rows: {fix}")

    shutil.copy2(FINAL, FINAL.with_suffix(".json.bak13"))
    FINAL.write_bytes(orjson.dumps(records, option=orjson.OPT_INDENT_2))

    with open(OUTPUT_DIR / "geo_resolution_map.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["from", "to", "osm_field", "coordinates"])
        for a, b, fl, k in sorted(set(applied)):
            w.writerow([a, b, fl, k])

    after = Counter((r["location"] or {}).get("area") or "" for r in records)
    after.pop("", None)
    logger.success(f"applied to {len(applied):,} rows | distinct -> {len(after):,}")
    logger.info(f"still unresolved: {len(failed) + (len(rows)-len(todo))}")
    for a, b, fl, k in sorted(set(applied))[:15]:
        logger.info(f"    {a[:30]:32} -> {b[:26]:28} (osm:{fl})")

    orig_list = orjson.loads(INPUT_JSON.read_bytes())
    assert len(records) == len(orig_list) == 17164
    assert len({r["_id"]["$oid"] for r in records}) == 17164
    for x, y in zip(orig_list, records):
        assert x["_id"] == y["_id"]
        assert list(x["location"].keys()) == list(y["location"].keys())
    logger.success("integrity OK")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    main(p.parse_args())
