"""Second defect class: one AREA-FALLBACK value repeated across far-apart branches.

The smear repair keyed on a shared full street address (raw != area). This class
is different and was deliberately skipped there: these rows have **raw == area**,
i.e. no street address at all, just an area name. For a single branch that is a
legitimate fallback. Repeated across branches of one brand sitting 133 km apart,
it is the crawl-zone value - measured at 3.6% accuracy against Talabat's own
areaName - and it is wrong.

    Jj Chicken           5 branches   133 km apart   all "al barsha"
    Pf Chang's           4 branches    28 km apart   all "al barsha 3"
    The Irish Village    2 branches    24 km apart   all "al garhoud"

SELECTION: same chain_id + identical area + coordinates more than MIN_KM apart,
excluding anything the smear repair already touched. Groups 1-5 km apart are
INCLUDED rather than judged: Business Bay really is that big, so if the page
agrees with the existing value the branch simply maps back to it and nothing
changes. The fetch decides, not a guess about area sizes.

SAME SOURCE AND GUARDS AS THE SMEAR REPAIR: Talabat's own `areaName` from
__NEXT_DATA__, mapped onto area_list.csv through the shared TaxMapper with the
numbered / wrong-emirate / tie guards. No areaName on the page -> unchanged.
ONLY `location.area` is written; patch and verify are imported from
apply_area_fix so both repairs behave identically.

    python fix_area_fallback.py --emit-targets     list for fetch_area_names.py
    python fix_area_fallback.py --dry-run
    python fix_area_fallback.py
"""
import argparse
import csv
import json
import math
import sys
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
sys.path.insert(0, str(HERE))
from apply_area_fix import TARGETS, patch, verify                # noqa: E402
from tax_map import TaxMapper                                    # noqa: E402
from taxonomy import load as load_taxonomy                       # noqa: E402

DATA = HERE / "data"
CACHE_DIR = DATA / "cache"
FIXED = DATA / "area_fix_applied.csv"
CACHE = ROOT / "listing_comparison" / "output" / "areaname_cache.json"
TAXCSV = AC / "area_list.csv"
CLEANED = AC / "ri-db.restaurants_full.cleaned.json"
TARGETS_CSV = DATA / "fallback_branches_to_fetch.csv"
AUDIT = DATA / "area_fallback_fix_applied.csv"
URL_SOURCES = [
    ROOT / "menu_refresh" / "data" / "refresh_202609" / "scrape_targets_202609.jsonl",
    ROOT / "menu_refresh" / "data" / "scrape_targets.jsonl",
    ROOT / "August_menu" / "data" / "restaurant_status.jsonl",
]
UNIFIED = ROOT / "unified" / "data" / "talabat_unified_202608.jsonl"
MIN_KM = 1.0
sys.stdout.reconfigure(encoding="utf-8")


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def norm(s):
    return " ".join(str(s or "").split()).casefold()


def select():
    """The affected branches. Read from unified, which holds both cohorts."""
    already = set()
    if FIXED.exists():
        already = {r["source_id"] for r in
                   csv.DictReader(open(FIXED, encoding="utf-8-sig"))}
    by = defaultdict(list)
    with open(UNIFIED, "rb") as f:
        for line in f:
            if not line.strip():
                continue
            r = orjson.loads(line)
            if r.get("is_verified_location") or r.get("chain_id") is None:
                continue
            L, g = r.get("location") or {}, r.get("geo") or {}
            a, raw = L.get("area"), L.get("raw")
            try:
                la, ln = float(g.get("lat")), float(g.get("lng"))
            except (TypeError, ValueError):
                continue
            # this class only: the area IS the whole location value
            if not a or norm(raw) != norm(a):
                continue
            by[(r["chain_id"], norm(a))].append(
                {"branch_id": str(r["source_id"]), "name": r.get("name"),
                 "area": a, "lat": la, "lng": ln})
    picked, groups = {}, 0
    for _, ms in by.items():
        if len(ms) < 2 or any(m["branch_id"] in already for m in ms):
            continue
        sp = max(km((a["lat"], a["lng"]), (b["lat"], b["lng"]))
                 for i, a in enumerate(ms) for b in ms[i + 1:])
        if sp <= MIN_KM:
            continue
        groups += 1
        for m in ms:
            m["spread_km"] = round(sp, 1)
            picked[m["branch_id"]] = m
    return picked, groups


def city_lookup():
    """Verified-location points -> the same coordinate kNN the repair used."""
    X, y = [], []
    for p in CACHE_DIR.glob("*_export.light.jsonl"):
        with open(p, "rb") as f:
            for line in f:
                v = orjson.loads(line)
                if (v["record_type"] == "google_verified_location" and v["city"]
                        and v["lat"] is not None):
                    X.append((v["lat"], v["lng"]))
                    y.append(v["city"])
    return cKDTree(np.array(X)), np.array(y)


def emit_targets(picked):
    url, meta = {}, {}
    for p in URL_SOURCES:
        if not p.exists():
            continue
        with open(p, "rb") as f:
            for line in f:
                if not line.strip():
                    continue
                t = orjson.loads(line)
                b = str(t.get("branch_id"))
                if b in picked and t.get("url"):
                    url.setdefault(b, t["url"])
                    meta.setdefault(b, t)
    rows = [{"branch_id": b, "restaurant_id": (meta.get(b) or {}).get("restaurant_id") or "",
             "name": m["name"], "url": url.get(b, ""), "lat": m["lat"],
             "lon": m["lng"], "area_name": m["area"]}
            for b, m in picked.items() if url.get(b)]
    with open(TARGETS_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return rows


def main(args):
    print("=" * 78)
    print("  AREA-FALLBACK REPEATS   (raw == area, same brand, far apart)")
    print("=" * 78)
    picked, groups = select()
    print(f"  groups: {groups:,} | branches: {len(picked):,}")
    sp = Counter("1-5 km" if m["spread_km"] <= 5 else "5-20 km"
                 if m["spread_km"] <= 20 else ">20 km" for m in picked.values())
    print(f"  by spread: {dict(sp)}")

    if args.emit_targets:
        rows = emit_targets(picked)
        print(f"  with a url: {len(rows):,}  -> {TARGETS_CSV.relative_to(ROOT)}")
        return 0

    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    have = sum(1 for b in picked if b in cache)
    print(f"  areaName fetched for: {have:,} of {len(picked):,}")
    if have == 0:
        print("  nothing fetched yet - run --emit-targets then fetch_area_names.py")
        return 1

    tree, y = city_lookup()
    areas, TAX = load_taxonomy(TAXCSV)
    tc = defaultdict(set)
    with open(CLEANED, "rb") as f:
        for rec in ijson.items(f, "item"):
            L = rec.get("location") or {}
            if L.get("area") in TAX:
                tc[L["area"]].add(L.get("city") or "?")
    mapper = TaxMapper(areas, TAX, tc)

    fix, stats = {}, Counter()
    for b, m in picked.items():
        real = ((cache.get(b) or {}).get("area_name_real") or "").strip()
        if not real or real.lower() in ("null", "none"):
            stats["no areaName on the page - unchanged"] += 1
            continue
        d, i = tree.query([(m["lat"], m["lng"])], k=3)
        cc = Counter(y[i[0]]).most_common(1)[0][0] if d[0][0] * 111 <= 15 else ""
        hit, how = mapper.map(real, cc)
        new = hit or real
        if norm(new) == norm(m["area"]):
            stats["page agrees with the existing area - no change"] += 1
            continue
        fix[b] = (new, f"page areaName {'-> taxonomy' if hit else 'kept off-taxonomy'} ({how or 'no match'})")
        stats["area replaced" if hit else "area replaced, off taxonomy"] += 1
    for k, v in sorted(stats.items()):
        print(f"    {k:46} {v:>5,}")
    print(f"  branches to change: {len(fix):,}")
    for b, (new, _) in list(fix.items())[:8]:
        print(f"    {b:>8} {str(picked[b]['name'])[:24]:26} "
              f"{str(picked[b]['area'])[:22]:24} -> {new}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    audit = []
    for name, path, shape, style in TARGETS:
        if not path.exists():
            continue
        ch, seen, rows, bak = patch(path, shape, style, fix, dry=False)
        n, bad = verify(path, shape, set(fix), bak)
        ok = (not bad) or set(bad) == {"area changed"}
        print(f"  {name:16} {seen:>7,} records | area changed {ch:>5,} | "
              f"verify {'PASS' if ok else 'FAIL ' + str(dict(bad))}")
        if not ok:
            raise SystemExit(f"{name}: verification failed - restore from backup")
        for sid, old, new in rows:
            audit.append({"dataset": name, "source_id": sid,
                          "name": picked[sid]["name"], "area_before": old,
                          "area_after": new, "method": fix[sid][1],
                          "group_spread_km": picked[sid]["spread_km"]})
    with open(AUDIT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(audit[0].keys()))
        w.writeheader()
        w.writerows(audit)
    print(f"\n  -> {AUDIT.relative_to(ROOT)}  ({len(audit):,} rows)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--emit-targets", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
