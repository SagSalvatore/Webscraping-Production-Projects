"""Correct BOTH classes of source mislabelling from the branch's own coordinates.

  A. city mismatch      labelled emirate contradicts the coordinates (2,700 rows)
  B. copied address     a chain's branches share one area text but sit up to
                        147 km apart (~5,900 rows)

Coordinates are the ground truth here: they are per-branch and were never part
of the copied text. City is derived from the coordinate directly (no API call).
Area needs reverse geocoding - OpenStreetMap Nominatim, which is INDEPENDENT of
Google/Talabat. Serper was evaluated and rejected: Talabat's addresses appear to
originate from Google, so querying Google returns the same source that contains
the error (it echoed the wrong value in 2 of 3 spot-checks).

Rate limit: Nominatim policy is 1 request/second. Runs sequentially with a 1.1s
delay and checkpoints every 25 lookups, so it is safe to interrupt and resume.

    python fix_mislabelled.py --dry-run
    python fix_mislabelled.py --limit 50
    python fix_mislabelled.py
"""
import argparse
import csv
import math
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
logger.add(LOG_DIR / "fix_mislabelled.log", level="DEBUG", encoding="utf-8",
           rotation="20 MB")

FINAL = OUTPUT_DIR / "ri-db.restaurants_id.final.json"
EXPORT = ROOT.parent / "talabat_export.json"
CACHE = CACHE_DIR / "geocode_cache.json"
PRECISION = 4                      # 11 m - well inside any area polygon
UA = {"User-Agent": "MordorIntelligence-AreaCleanup/1.0 (data quality research)"}
DELAY = 1.1

FIELDS = ["suburb", "quarter", "neighbourhood", "city_district", "residential",
          "village", "town"]
CITY_FIELDS = ["city", "town", "state", "county"]
CITIES = {"dubai", "abu dhabi", "sharjah", "ajman", "fujairah", "al ain",
          "umm al quwain", "ras al khaimah", "united arab emirates", "uae"}
BADAREA = re.compile(r"\b(street|st|road|rd|highway|e\s?\d+|interchange)\b", re.I)
NO_ART = {"saadiyat island", "meydan", "mirdif", "business bay", "jumeirah",
          "barsha heights", "dubai marina", "zayed sports city"}


def emirate_from_coord(lat, lon):
    """Coarse boxes - only used to FLAG a mismatch, never to write a value.
    The city actually written comes from the geocoder."""
    if 24.0 <= lat < 25.0 and 53.0 <= lon < 55.5: return "Abu Dhabi"
    if 24.7 <= lat < 25.45 and 54.8 <= lon < 55.65: return "Dubai"
    if 25.25 <= lat < 25.65 and 55.3 <= lon < 55.9: return "Sharjah"
    if 25.35 <= lat < 25.5 and 55.4 <= lon < 55.6: return "Ajman"
    if 25.5 <= lat < 26.2 and 55.7 <= lon < 56.4: return "Ras Al Khaimah"
    if 25.0 <= lat < 25.7 and 56.0 <= lon < 56.4: return "Fujairah"
    if 25.45 <= lat < 25.6 and 55.5 <= lon < 55.8: return "Umm Al Quwain"
    return None


def km(a, b):
    return math.hypot((a[0] - b[0]) * 111, (a[1] - b[1]) * 101)


def pick_area(addr):
    for f in FIELDS:
        v = norm_text((addr.get(f) or "").strip())
        if v and v.lower() not in CITIES and not BADAREA.search(v) and len(v) > 2:
            return v
    return None


def norm_text(s):
    """Nominatim returns curly quotes and 'X Emirate' / 'Emirate of X'."""
    if not s:
        return s
    s = s.replace("’", "'").replace("‘", "'").replace("`", "'")
    return re.sub(r"\s{2,}", " ", s).strip(" ,-")


def pick_city(addr):
    for f in CITY_FIELDS:
        v = norm_text((addr.get(f) or "").strip())
        if not v:
            continue
        v = re.sub(r"^Emirate of\s+", "", v)
        v = re.sub(r"\s+Emirate$", "", v)          # 'Dubai Emirate' -> 'Dubai'
        v = v.strip()
        if v.lower() in CITIES:
            return v
    return None


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

    # ---- identify both defect classes ----
    groups = defaultdict(list)
    for r in records:
        ol = original[r["_id"]["$oid"]].get("location") or {}
        groups[(r["chain_id"], (ol.get("area") or "").strip(),
                (ol.get("sublocality") or "").strip())].append(r)

    affected = {}
    for _, rows in groups.items():
        pts = [(r, coord_of(r)) for r in rows]
        pts = [(r, c) for r, c in pts if c]
        if len(pts) > 1 and max(km(pts[0][1], c) for _, c in pts) > 5:
            for r, c in pts:
                affected[r["_id"]["$oid"]] = (r, c, "copied_address")
    for r in records:
        c = coord_of(r)
        if not c:
            continue
        e = emirate_from_coord(*c)
        city = (r["location"].get("city") or "").strip()
        if e and city and e != city:
            affected.setdefault(r["_id"]["$oid"], (r, c, "city_mismatch"))

    logger.info(f"affected rows: {len(affected):,} "
                f"({Counter(v[2] for v in affected.values())})")

    keys = {}
    for rid, (r, c, why) in affected.items():
        keys[(round(c[0], PRECISION), round(c[1], PRECISION))] = None
    cache = load_cache()
    todo = [k for k in keys if f"{k[0]},{k[1]}" not in cache]
    logger.info(f"distinct coordinates @{PRECISION}dp: {len(keys):,} | "
                f"cached {len(keys)-len(todo):,} | to fetch {len(todo):,} "
                f"(~{len(todo)*DELAY/60:.0f} min)")

    if args.dry_run:
        logger.warning("--dry-run: no requests, nothing written")
        return
    if args.limit:
        todo = todo[:args.limit]
        logger.warning(f"TEST MODE - {len(todo)} lookups")

    with httpx.Client(headers=UA, timeout=45) as client:
        for i, (lat, lon) in enumerate(todo, 1):
            k = f"{lat},{lon}"
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
            if i % 25 == 0:
                save_cache(cache)
                logger.info(f"  {i}/{len(todo)}  ({i/len(todo)*100:.0f}%)  "
                            f"ETA {(len(todo)-i)*DELAY/60:.0f} min")
    save_cache(cache)

    # ---- apply ----
    changed_area = changed_city = 0
    log = []
    for rid, (r, c, why) in affected.items():
        k = f"{round(c[0],PRECISION)},{round(c[1],PRECISION)}"
        addr = cache.get(k)
        if not addr:
            continue
        na, nc = pick_area(addr), pick_city(addr)
        oa, oc = r["location"].get("area"), r["location"].get("city")
        if na and na != oa:
            r["location"]["area"] = na
            changed_area += 1
        if nc and nc != oc:
            r["location"]["city"] = nc
            changed_city += 1
        if (na and na != oa) or (nc and nc != oc):
            log.append([r["source_id"], r["name"], why, oc, nc or oc, oa,
                        na or oa, c[0], c[1], rid])

    logger.success(f"area corrected {changed_area:,} | city corrected {changed_city:,}")

    # ---- re-assert canonical conventions on anything new ----
    def canon_pass():
        cnt = Counter((x["location"] or {}).get("area") or "" for x in records)
        cnt.pop("", None)
        total = 0
        # typography
        for x in records:
            a = x["location"].get("area")
            if not a:
                continue
            n = a.replace("’", "'").replace("‘", "'").replace("`", "'")
            n = re.sub(r"\bCentre\b", "Center", n)
            n = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", n, flags=re.I)
            n = re.sub(r"\s{2,}", " ", n).strip(" ,-")
            if n != a:
                x["location"]["area"] = n
                total += 1
        ORD = {"first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5"}
        for keyfn, prefer_digit, skip in [
            (lambda s: re.sub(r"[^a-z0-9]", "", s.lower()), False, None),
            (lambda s: re.sub(r"[^a-z0-9]", "", re.sub(
                r"\b(first|second|third|fourth|fifth)\b",
                lambda m: ORD[m.group(1).lower()], s.lower())), True, None),
            (lambda s: "".join(re.sub(r"(?<=[a-z]{3})s$", "", w)
                               for w in re.findall(r"[a-z0-9]+", s.lower())), True, None),
            (lambda s: re.sub(r"^al\s+", "", s.lower()).strip(), False, NO_ART),
        ]:
            cnt = Counter((x["location"] or {}).get("area") or "" for x in records)
            cnt.pop("", None)
            g = defaultdict(list)
            for a, n in cnt.items():
                g[keyfn(a)].append((a, n))
            mp = {}
            for key, var in g.items():
                if len(var) < 2 or (skip and key in skip):
                    continue
                var.sort(key=lambda t: -t[1])
                if prefer_digit:
                    dig = [v for v in var if re.search(r"\b\d+\b", v[0])]
                    tgt = dig[0][0] if dig else var[0][0]
                else:
                    tgt = var[0][0]
                for a, _ in var:
                    if a != tgt:
                        mp[a] = tgt
            for x in records:
                a = x["location"].get("area")
                if a in mp:
                    x["location"]["area"] = mp[a]
                    total += 1
        return total

    n_canon = canon_pass()
    logger.info(f"canonical re-pass: {n_canon:,} rows normalised")

    shutil.copy2(FINAL, FINAL.with_suffix(".json.bak17"))
    FINAL.write_bytes(orjson.dumps(records, option=orjson.OPT_INDENT_2))

    with open(OUTPUT_DIR / "coordinate_correction_map.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["source_id", "name", "defect", "city_before", "city_after",
                    "area_before", "area_after", "lat", "lon", "_id"])
        w.writerows(log)

    after = Counter((r["location"] or {}).get("area") or "" for r in records)
    after.pop("", None)
    logger.success(f"distinct areas -> {len(after):,}")

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
    p.add_argument("--limit", type=int)
    main(p.parse_args())
