"""Detect brand-level ADDRESS SMEARS in the July export, August export and unified.

THE DEFECT. Baskin Robbins' 7 July listings all read
    "QWJR+387 - Khoor Ras Al Khaimah - Ras Al Khaimah - United Arab Emirates"
yet their coordinates sit in Downtown Dubai, Al Barsha, Nad Al Hammar and Abu
Dhabi, and Talabat's own URLs name seven different areas. One Google listing -
one place_id - was looked up by BRAND name and its address, phone and Maps link
were copied onto every branch. The rows look complete and correct, which is
exactly why nobody sees it.

WHAT COUNTS AS A SMEAR - all three must hold, because sharing an address is
often legitimate:
    1 same chain_id                 one brand
    2 identical FULL raw address    not an area fallback. When there is no
                                    address, raw IS the area ("Al Barsha 3"), and
                                    many branches share that honestly - so raw
                                    must differ from area to count
    3 coordinates > SPREAD_KM apart two listings of one brand in the same mall
                                    share an address and sit metres apart; past
                                    1 km they cannot be at one street address
Google verified-location records are excluded from detection: each has its own
Google address by construction.

EVIDENCE PER ROW, never written back to any source file:
    city_from_coordinates  kNN k=3 over verified-location records (address and
                           coords from the SAME listing) - the fill_cities.py
                           method, measured 99.45% held-out; blank past 15 km
    raw_city               the emirate named inside the shared raw address
    slug_area              Talabat's own area, from the URL after the name
    proposed_area          slug_area mapped onto area_list.csv through the
                           imported TaxMapper - same guards as every area stage
    verdict                wrong_emirate / wrong_area / consistent

TOOLS BY FILE SHAPE, not size. The two exports are single nested JSON arrays ->
ijson streams them. Unified and every url/target file are JSON Lines -> orjson
per line. Light rows are cached with orjson keyed on the source file's size and
mtime, so a re-run to tune the threshold never re-parses 1.2 GB.

    python detect_address_smear.py --dry-run
    python detect_address_smear.py
"""
import argparse
import csv
import hashlib
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import ijson
import numpy as np
import orjson
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
AC = ROOT / "area_classification"
sys.path.insert(0, str(AC))
from tax_map import TaxMapper                                  # noqa: E402
from taxonomy import load as load_taxonomy                     # noqa: E402

DATA = HERE / "data"
CACHE = DATA / "cache"
DATASETS = [  # prefix, name, path, shape
    ("JUL", "july_export", ROOT / "export" / "talabat_export.json", "json"),
    ("AUG", "august_export", ROOT / "August_menu" / "data" / "August_export.json", "json"),
    ("UNI", "unified_202608", ROOT / "unified" / "data" / "talabat_unified_202608.jsonl", "jsonl"),
]
URL_SOURCES = [  # all JSON Lines; first file wins per branch_id
    ROOT / "menu_refresh" / "data" / "refresh_202609" / "scrape_targets_202609.jsonl",
    ROOT / "menu_refresh" / "data" / "scrape_targets.jsonl",
    ROOT / "August_menu" / "data" / "restaurant_status.jsonl",
]
TAXCSV = AC / "area_list.csv"
CLEANED = AC / "ri-db.restaurants_full.cleaned.json"
OUT_ROWS = DATA / "address_smear_rows.csv"
OUT_GROUPS = DATA / "address_smear_groups.csv"

SPREAD_KM = 1.0
K, MAX_KM = 3, 15.0
CANON = {"Ras Al-Khaimah": "Ras Al Khaimah", "Kalba": "Sharjah"}
COUNTRY = {"united arab emirates", "uae"}
PLUS_CODE = re.compile(r"\b[23456789CFGHJMPQRVWX]{4,}\+[23456789CFGHJMPQRVWX]{2,}\b")
_SLUG = re.compile(r"/restaurant/\d+/([^?/#]+)")
sys.stdout.reconfigure(encoding="utf-8")


# ------------------------------------------------------------------ helpers
def as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def hav_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def norm_raw(s):
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


KNOWN_CITIES = {"Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Ras Al Khaimah",
                "Fujairah", "Umm Al Quwain", "Al Ain", "Khor Fakkan"}
_CITY_BY_FOLD = {c.casefold(): c for c in KNOWN_CITIES} | {
    "ras al-khaimah": "Ras Al Khaimah", "kalba": "Sharjah"}


def raw_city(raw):
    """The emirate named inside a raw address, or "" if none is.

    Scans segments from the END for a KNOWN city. Taking the last segment
    blindly returned 'Shop 2204, The Dubai Mall' for addresses that stop before
    the emirate, and every such row was then misread as a wrong emirate."""
    segs = [s.strip() for s in (raw or "").split(" - ") if s.strip()]
    for s in reversed(segs):
        c = _CITY_BY_FOLD.get(s.casefold())
        if c:
            return c
    return ""


def smear_key(chain_id, raw):
    """Same key in every dataset, so a group found in the July export and the
    same group carried into unified line up without any fuzzy matching."""
    return f"{chain_id}-{hashlib.sha1(norm_raw(raw).encode('utf-8')).hexdigest()[:10]}"


# ------------------------------------------------------------ streaming scan
def light(rec, prefix, ds, i):
    loc = rec.get("location") or {}
    geo = rec.get("geo") or {}
    return {
        "row_id": f"{prefix}-{i:06d}", "dataset": ds,
        "record_type": ("google_verified_location" if rec.get("is_verified_location")
                        else "talabat_listing"),
        "source_id": str(rec.get("source_id")), "name": rec.get("name"),
        "chain_id": rec.get("chain_id"), "outlet_type": rec.get("outlet_type"),
        "chain_type": rec.get("chain_type"),
        "raw": loc.get("raw"), "area": loc.get("area"), "city": loc.get("city"),
        "sublocality": loc.get("sublocality"),
        "lat": as_float(geo.get("lat")), "lng": as_float(geo.get("lng")),
        "maps_url": rec.get("maps_url"), "phone": rec.get("contact_phone"),
        "website": rec.get("website"),
        "menu_item_count": len(rec.get("menu_items") or []),
    }


def scan(prefix, ds, path, shape):
    CACHE.mkdir(parents=True, exist_ok=True)
    st = path.stat()
    stamp = f"{st.st_size}-{int(st.st_mtime)}"
    cache, meta = CACHE / f"{ds}.light.jsonl", CACHE / f"{ds}.stamp"
    if cache.exists() and meta.exists() and meta.read_text() == stamp:
        with open(cache, "rb") as f:
            return [orjson.loads(l) for l in f], "cache"
    t0, rows = time.time(), []
    with open(path, "rb") as f:
        it = (ijson.items(f, "item", use_float=True) if shape == "json"
              else (orjson.loads(l) for l in f if l.strip()))
        for i, rec in enumerate(it):
            rows.append(light(rec, prefix, ds, i))
    with open(cache, "wb") as f:
        for r in rows:
            f.write(orjson.dumps(r) + b"\n")
    meta.write_text(stamp)
    return rows, f"{'ijson' if shape == 'json' else 'orjson'} {time.time()-t0:.0f}s"


def load_urls():
    url = {}
    for p in URL_SOURCES:
        if not p.exists():
            continue
        with open(p, "rb") as f:
            for line in f:
                if line.strip():
                    t = orjson.loads(line)
                    if t.get("url"):
                        url.setdefault(str(t.get("branch_id")), t["url"])
    return url


def load_tax_city(TAX):
    """taxonomy area -> cities it was seen in, from the cleaned file Tech holds.
    A nested JSON array, so ijson."""
    tc = defaultdict(set)
    if CLEANED.exists():
        with open(CLEANED, "rb") as f:
            for r in ijson.items(f, "item"):
                L = r.get("location") or {}
                if L.get("area") in TAX:
                    tc[L["area"]].add(L.get("city") or "?")
    return tc


def slug_tail(u, name):
    m = _SLUG.search(u or "")
    if not m:
        return ""
    slug = m.group(1).replace("-", " ").strip()
    nm = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())).strip()
    if not nm or not slug.lower().startswith(nm):
        return ""
    return slug[len(nm):].strip()


# ------------------------------------------------------------------ detection
def detect(rows):
    groups = defaultdict(list)
    for r in rows:
        raw = (r["raw"] or "").strip()
        if (r["record_type"] != "talabat_listing" or r["chain_id"] is None
                or not raw or raw == "NA"
                or norm_raw(raw) == norm_raw(r["area"])):
            continue
        groups[(r["chain_id"], norm_raw(raw))].append(r)
    cand, smears = [], []
    for (cid, _), rs in groups.items():
        by_id = {}
        for r in rs:
            by_id.setdefault(r["source_id"], r)
        if len(by_id) < 2:
            continue
        pts = [(r["lat"], r["lng"]) for r in by_id.values()
               if r["lat"] is not None and r["lng"] is not None]
        spread = max((hav_km(a, b) for i, a in enumerate(pts)
                      for b in pts[i + 1:]), default=0.0)
        g = {"key": smear_key(cid, rs[0]["raw"]), "chain_id": cid,
             "rows": list(by_id.values()), "spread_km": spread}
        cand.append(g)
        if spread > SPREAD_KM:
            smears.append(g)
    return cand, smears


def main(args):
    print("=" * 80)
    print(f"  ADDRESS SMEAR AUDIT   (same chain_id + same full raw address + "
          f">{SPREAD_KM:g} km apart)")
    print("=" * 80)

    data = {}
    for prefix, ds, path, shape in DATASETS:
        if not path.exists():
            print(f"  {ds:16} MISSING {path}")
            continue
        rows, how = scan(prefix, ds, path, shape)
        data[ds] = rows
        n_l = sum(1 for r in rows if r["record_type"] == "talabat_listing")
        print(f"  {ds:16} {len(rows):>7,} records | {n_l:>6,} listings | "
              f"{len(rows)-n_l:>5,} verified   [{how}]")

    # city-from-coordinates labels: verified records from the two EXPORTS only.
    # unified repeats the same records, and counting them twice would weight
    # the vote without adding any evidence.
    X, y = [], []
    for ds in ("july_export", "august_export"):
        for r in data.get(ds, []):
            if (r["record_type"] == "google_verified_location" and r["city"]
                    and r["lat"] is not None and r["lng"] is not None):
                X.append((r["lat"], r["lng"]))
                y.append(CANON.get(r["city"], r["city"]))
    tree, y = cKDTree(np.array(X)), np.array(y)
    areas, TAX = load_taxonomy(TAXCSV)
    mapper = TaxMapper(areas, TAX, load_tax_city(TAX))
    urls = load_urls()
    print(f"\n  evidence: {len(X):,} verified points | {len(areas)} taxonomy areas | "
          f"{len(urls):,} talabat urls")

    all_rows, all_groups, found = [], [], {}
    for prefix, ds, _, _ in DATASETS:
        if ds not in data:
            continue
        cand, smears = detect(data[ds])
        found[ds] = {g["key"]: g for g in smears}
        buckets = Counter()
        for g in cand:
            s = g["spread_km"]
            buckets["<0.2 km" if s < 0.2 else "0.2-1 km" if s <= 1 else
                    "1-5 km" if s <= 5 else "5-20 km" if s <= 20 else ">20 km"] += 1
        print(f"\n  {ds}: {len(cand):,} brand groups share a full address; "
              f"spread: " + " | ".join(f"{b} {buckets[b]}" for b in
                                        ("<0.2 km", "0.2-1 km", "1-5 km", "5-20 km", ">20 km")))
        print(f"    SMEARS (>{SPREAD_KM:g} km): {len(smears):,} groups, "
              f"{sum(len(g['rows']) for g in smears):,} branches")

        for n, g in enumerate(sorted(smears, key=lambda g: -len(g["rows"])), 1):
            rs = g["rows"]
            shared = rs[0]
            rc = raw_city(shared["raw"])
            verdicts, slug_areas, coord_cities = Counter(), set(), Counter()
            for r in rs:
                cc = ""
                if r["lat"] is not None and r["lng"] is not None:
                    d, ix = tree.query([(r["lat"], r["lng"])], k=K)
                    if d[0][0] * 111 <= MAX_KM:
                        cc = Counter(y[ix[0]]).most_common(1)[0][0]
                coord_cities[cc or "?"] += 1
                u = urls.get(r["source_id"], "")
                tail = slug_tail(u, r["name"])
                on_tax = False
                if tail:
                    hit, how = mapper.map(tail, cc)
                    if hit:
                        prop, method, on_tax = hit, how, True
                    else:
                        prop, method = tail, f"off_taxonomy ({how or 'no match'})"
                else:
                    prop, method = "", "no slug"
                fhit, _ = mapper.map(r["area"] or "", cc)
                # verdicts only claim what the evidence supports; no evidence is
                # its own answer, never a silent "consistent"
                if cc and rc and cc != rc:
                    verdict = "wrong_emirate"
                elif on_tax and fhit and prop == fhit:
                    verdict = "consistent"
                elif on_tax:
                    verdict = "wrong_area"      # Talabat names a different, or a
                                                # real, area where the file has junk
                else:
                    verdict = "unverified"      # no slug, or slug off the taxonomy
                verdicts[verdict] += 1
                if prop:
                    slug_areas.add(prop.casefold())
                all_rows.append({
                    "dataset": ds, "smear_key": g["key"], "row_id": r["row_id"],
                    "source_id": r["source_id"], "name": r["name"],
                    "chain_id": r["chain_id"], "talabat_url": u,
                    "shared_raw": r["raw"], "file_area": r["area"],
                    "file_city": r["city"], "raw_city": rc,
                    "city_from_coordinates": cc, "slug_area": tail,
                    "proposed_area": prop, "proposed_area_method": method,
                    "file_area_on_taxonomy": fhit or "", "verdict": verdict,
                    "lat": r["lat"], "lng": r["lng"],
                    "coords_map_url": (f"https://www.google.com/maps?q={r['lat']},{r['lng']}"
                                       if r["lat"] is not None else ""),
                    "file_maps_url": r["maps_url"], "file_phone": r["phone"],
                    "file_website": r["website"], "sublocality": r["sublocality"],
                    "group_branches": len(rs), "group_spread_km": round(g["spread_km"], 2),
                })
            all_groups.append({
                "dataset": ds, "smear_key": g["key"],
                "brand": Counter(r["name"] for r in rs).most_common(1)[0][0],
                "chain_id": g["chain_id"], "branches": len(rs),
                "spread_km": round(g["spread_km"], 2), "shared_raw": shared["raw"],
                "raw_city": rc, "has_plus_code": bool(PLUS_CODE.search(shared["raw"] or "")),
                "coord_cities": "; ".join(f"{c} {n}" for c, n in coord_cities.most_common()),
                "distinct_true_areas": len(slug_areas),
                "true_areas": "; ".join(sorted(slug_areas)),
                "same_maps_url": len({r["maps_url"] for r in rs}) == 1,
                "same_phone": len({r["phone"] for r in rs}) == 1,
                "verdicts": "; ".join(f"{v} {n}" for v, n in verdicts.most_common()),
            })

    # ---- independent validation: do Talabat's own URLs agree? --------------
    # The detector only looked at the address string and the coordinates. The
    # URL slug is a third, separate source - Talabat's own area per branch. If
    # a group is really one address copied onto many branches, its branches'
    # slugs should name DIFFERENT areas, as Baskin Robbins' seven did.
    print("\n" + "-" * 80)
    print("  VALIDATION - distinct Talabat URL areas per smear group")
    for ds in [d for _, d, _, _ in DATASETS if d in found]:
        gs = [g for g in all_groups if g["dataset"] == ds]
        tab = Counter()
        for g in gs:
            b = ("1-5 km" if g["spread_km"] <= 5 else "5-20 km"
                 if g["spread_km"] <= 20 else ">20 km")
            k = ("no slugs" if g["distinct_true_areas"] == 0 else
                 "1 area" if g["distinct_true_areas"] == 1 else "2+ areas")
            tab[(b, k)] += 1
        print(f"    {ds}")
        for b in ("1-5 km", "5-20 km", ">20 km"):
            row = {k: tab[(b, k)] for k in ("2+ areas", "1 area", "no slugs")}
            tot = sum(row.values())
            if tot:
                print(f"      {b:8} {tot:>5} groups | 2+ areas {row['2+ areas']:>5} "
                      f"({row['2+ areas']/tot*100:5.1f}%) | 1 area {row['1 area']:>4} "
                      f"| no slugs {row['no slugs']:>4}")
        print(f"      same maps_url across whole group: "
              f"{sum(1 for g in gs if g['same_maps_url']):,} of {len(gs):,} | "
              f"same phone: {sum(1 for g in gs if g['same_phone']):,} | "
              f"plus code in shared raw: {sum(1 for g in gs if g['has_plus_code']):,}")

    # ---- does unified carry the same smears as the exports? ----------------
    exp_keys = set(found.get("july_export", {})) | set(found.get("august_export", {}))
    uni_keys = set(found.get("unified_202608", {}))
    exp_ids = {r["source_id"] for ds in ("july_export", "august_export")
               for g in found.get(ds, {}).values() for r in g["rows"]}
    uni_by_id = {r["source_id"]: r for r in data.get("unified_202608", [])
                 if r["record_type"] == "talabat_listing"}
    exp_raw = {r["source_id"]: r["raw"] for ds in ("july_export", "august_export")
               for g in found.get(ds, {}).values() for r in g["rows"]}
    same_raw = sum(1 for s in exp_ids if s in uni_by_id
                   and norm_raw(uni_by_id[s]["raw"]) == norm_raw(exp_raw[s]))
    print("\n" + "-" * 80)
    print("  EXPORTS vs UNIFIED")
    print(f"    smear groups in the exports          : {len(exp_keys):,}")
    print(f"      ...also present in unified         : {len(exp_keys & uni_keys):,}")
    print(f"      ...fixed / absent in unified       : {len(exp_keys - uni_keys):,}")
    print(f"    smear groups ONLY in unified         : {len(uni_keys - exp_keys):,}")
    print(f"    affected branches in the exports     : {len(exp_ids):,}")
    print(f"      ...present in unified              : {sum(1 for s in exp_ids if s in uni_by_id):,}")
    print(f"      ...carrying the identical raw there: {same_raw:,}")
    # a group whose key differs between export and unified is usually the SAME
    # group with its raw text rewritten by the unified build - show it, so a
    # key mismatch is not mistaken for a fix
    diff = [s for s in exp_ids if s in uni_by_id
            and norm_raw(uni_by_id[s]["raw"]) != norm_raw(exp_raw[s])]
    for s in diff[:4]:
        print(f"      raw rewritten in unified, {s}:")
        print(f"         export : {str(exp_raw[s])[:90]}")
        print(f"         unified: {str(uni_by_id[s]['raw'])[:90]}")

    print("\n  VERDICTS (all datasets): "
          f"{dict(Counter((r['dataset'], r['verdict']) for r in all_rows))}")
    print("\n  largest groups:")
    for g in sorted(all_groups, key=lambda g: (-g["branches"], g["dataset"]))[:25]:
        print(f"    {g['dataset'][:6]:7} {g['branches']:>3} br  {g['spread_km']:>7.1f} km  "
              f"{str(g['brand'])[:26]:27} raw city {g['raw_city'][:14]:15} coords: "
              f"{g['coord_cities'][:34]}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    for out, recs in ((OUT_ROWS, all_rows), (OUT_GROUPS, all_groups)):
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            if recs:
                w = csv.DictWriter(f, fieldnames=list(recs[0].keys()))
                w.writeheader()
                w.writerows(recs)
    print(f"\n  -> {OUT_GROUPS.relative_to(ROOT)}  ({len(all_groups):,} groups)")
    print(f"  -> {OUT_ROWS.relative_to(ROOT)}  ({len(all_rows):,} rows)")
    print("  source files: untouched")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
