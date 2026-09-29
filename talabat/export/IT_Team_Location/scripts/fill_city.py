"""Fill every blank `city` in ri-db.restaurants_id.final.json.

Cheapest source first:
  1. area -> city, inferred from rows that already have a city. UAE area names
     are city-specific ('Al Barsha 3' is only ever Dubai), so this is safe -
     but only where the mapping is UNAMBIGUOUS (>=90% of rows agree). Free.
  2. the geocode cache already built for the coordinate corrections. Free.
  3. a fresh Nominatim lookup for whatever is left. 1 req/sec.

An ambiguous area (the same name in two emirates) never gets guessed - it falls
through to the coordinate, which is unambiguous.

    python fill_city.py --dry-run
    python fill_city.py
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
logger.add(LOG_DIR / "fill_city.log", level="DEBUG", encoding="utf-8")

FINAL = OUTPUT_DIR / "ri-db.restaurants_id.final.json"
EXPORT = ROOT.parent / "talabat_export.json"
CACHE = CACHE_DIR / "geocode_cache.json"
UA = {"User-Agent": "MordorIntelligence-AreaCleanup/1.0 (data quality research)"}
DELAY = 1.1
CITIES = {"dubai", "abu dhabi", "sharjah", "ajman", "fujairah", "al ain",
          "umm al quwain", "ras al khaimah"}
DOMINANCE = 0.90        # an area must be this consistent to infer its city


def norm_city(v):
    if not v:
        return None
    v = v.replace("’", "'").strip()
    v = re.sub(r"^Emirate of\s+", "", v)
    v = re.sub(r"\s+Emirate$", "", v).strip()
    return v if v.lower() in CITIES else None


def main(args):
    records = orjson.loads(FINAL.read_bytes())
    original = {o["_id"]["$oid"]: o for o in orjson.loads(INPUT_JSON.read_bytes())}
    coords = {}
    for o in orjson.loads(EXPORT.read_bytes()):
        g = o.get("geo") or {}
        if g.get("lat") and g.get("lng"):
            k = (o["source_id"], ((o.get("location") or {}).get("area") or "").strip())
            coords.setdefault(k, (float(g["lat"]), float(g["lng"])))

    def coord_of(r):
        oa = ((original[r["_id"]["$oid"]].get("location") or {}).get("area") or "").strip()
        return coords.get((r["source_id"], oa))

    # ---- 1. area -> city, only where unambiguous ----
    votes = defaultdict(Counter)
    for r in records:
        a, c = r["location"].get("area"), norm_city(r["location"].get("city"))
        if a and c:
            votes[a][c] += 1
    area_city, ambiguous = {}, []
    for a, cnt in votes.items():
        top, n = cnt.most_common(1)[0]
        if n / sum(cnt.values()) >= DOMINANCE:
            area_city[a] = top
        else:
            ambiguous.append((a, dict(cnt)))
    logger.info(f"area->city map: {len(area_city):,} unambiguous | "
                f"{len(ambiguous)} ambiguous (will use coordinates instead)")
    for a, c in ambiguous[:6]:
        logger.warning(f"    ambiguous: {a!r} -> {c}")

    blanks = [r for r in records if not (r["location"].get("city") or "").strip()]
    logger.info(f"rows with blank city: {len(blanks):,}")

    from_area = [r for r in blanks if r["location"].get("area") in area_city]
    rest = [r for r in blanks if r["location"].get("area") not in area_city]
    logger.info(f"  fillable from area map : {len(from_area):,}")
    logger.info(f"  need coordinates       : {len(rest):,}")

    cache = orjson.loads(CACHE.read_bytes()) if CACHE.exists() else {}
    need_lookup = []
    for r in rest:
        c = coord_of(r)
        if c and f"{round(c[0],4)},{round(c[1],4)}" not in cache:
            need_lookup.append((r, c))
    logger.info(f"  fresh lookups required : {len(need_lookup):,} "
                f"(~{len(need_lookup)*DELAY/60:.0f} min)")

    if args.dry_run:
        logger.warning("--dry-run: nothing written")
        return

    if need_lookup:
        with httpx.Client(headers=UA, timeout=45) as client:
            for i, (r, c) in enumerate(need_lookup, 1):
                k = f"{round(c[0],4)},{round(c[1],4)}"
                try:
                    resp = client.get("https://nominatim.openstreetmap.org/reverse",
                                      params={"lat": c[0], "lon": c[1], "format": "json",
                                              "zoom": 16, "addressdetails": 1,
                                              "accept-language": "en"})
                    cache[k] = (resp.json() or {}).get("address", {}) if resp.status_code == 200 else {}
                except Exception as exc:
                    logger.warning(f"{k}: {type(exc).__name__}")
                    cache[k] = {}
                time.sleep(DELAY)
                if i % 25 == 0:
                    tmp = CACHE.with_suffix(".tmp")
                    tmp.write_bytes(orjson.dumps(cache, option=orjson.OPT_INDENT_2))
                    tmp.replace(CACHE)
                    logger.info(f"  {i}/{len(need_lookup)} "
                                f"ETA {(len(need_lookup)-i)*DELAY/60:.0f} min")
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_bytes(orjson.dumps(cache, option=orjson.OPT_INDENT_2))
        tmp.replace(CACHE)

    src = Counter()
    log = []
    for r in blanks:
        a = r["location"].get("area")
        city = None
        if a in area_city:
            city, how = area_city[a], "area_map"
        else:
            c = coord_of(r)
            if c:
                addr = cache.get(f"{round(c[0],4)},{round(c[1],4)}") or {}
                for f in ("city", "town", "state", "county"):
                    city = norm_city(addr.get(f))
                    if city:
                        break
                how = "geocode"
        if city:
            r["location"]["city"] = city
            src[how] += 1
            log.append([r["source_id"], r["name"], a, city, how])
        else:
            src["unresolved"] += 1

    shutil.copy2(FINAL, FINAL.with_suffix(".json.bak20"))
    FINAL.write_bytes(orjson.dumps(records, option=orjson.OPT_INDENT_2))
    with open(OUTPUT_DIR / "city_fill_map.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["source_id", "name", "area", "city_filled", "method"])
        w.writerows(log)

    still = sum(1 for r in records if not (r["location"].get("city") or "").strip())
    logger.success(f"filled {sum(v for k,v in src.items() if k!='unresolved'):,}  {dict(src)}")
    logger.info(f"blank city remaining: {still:,}")
    logger.info("cities: " + str(dict(Counter(r['location'].get('city') or '(blank)'
                                              for r in records).most_common())))

    orig = orjson.loads(INPUT_JSON.read_bytes())
    assert len(records) == len(orig) == 17164
    assert len({r["_id"]["$oid"] for r in records}) == 17164
    for x, y in zip(orig, records):
        assert x["_id"] == y["_id"]
        assert list(x["location"].keys()) == list(y["location"].keys())
    logger.success("integrity OK")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    main(p.parse_args())
