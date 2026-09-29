"""The gates validate_unified_export.py does not have - run on the OUTPUT.

validate_unified_export.py proves structure, schema, the NA convention, menu
integrity, one-label-per-name and clean text. It says nothing about WHERE a
restaurant is. These gates do, because that is where this file went wrong:

AREA (Sagar: "make sure we rectify area as well canonical")
  A1 FAIL  a city used as an area ('Dubai', 'دبي')
  A2 FAIL  spelling variants of one place ('Al Barsha 1' / 'Al Barsha First')
  A3 info  share of records on area_list.csv, per cohort
  A4 WARN  cross-emirate leak - the two-condition detector: the record's own
           address does not back its area AND its city is not one that area is
           confidently seen in (reference_cross_emirate_area_leak)

SMEAR (the Baskin Robbins case: one Google listing copied onto every branch)
  S1 FAIL  one Google Maps PLACE link on branches more than 1 km apart - a place
           link identifies exactly one place, so this is always a copy
  S2 WARN  one phone on a brand's branches more than 1 km apart - a central
           call-centre number is legitimate, so it is reported, not failed
  S3 info  one area on a brand's branches more than 1 km apart - large areas
           (Business Bay, Al Barsha 1) hold several branches honestly
  S4 info  the Baskin Robbins branches themselves, printed
  S5 FAIL  one full " - " address on a brand's branches more than 1 km apart
  S5b WARN the same copy written with commas (not yet repaired - Sagar to decide)
  S6 WARN  a Maps place link whose place sits more than 1 km from the branch
  A6 FAIL  the address names another emirate than the record's city

SEPTEMBER (Sagar's rules)
  C1 FAIL  chain_locations_count = Google UAE count if >= 1, else Talabat count
  C2 FAIL  Independent <-> chain_type null; Chain <-> Local/MNC and count >= 2
  C3 FAIL  is_verified_location present and false

    python validate_unified_202609_extra.py --file data/talabat_unified_202609.jsonl
"""
import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ijson
import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from rectify_areas import (AreaRectifier, area_state,             # noqa: E402
                           import_area_module, spelling_twins)
_FCM = import_area_module("fix_city_mismatch")   # it does `import config as C`
CITY_SHARE, MIN_CONFIDENT, text_supports = _FCM.CITY_SHARE, _FCM.MIN_CONFIDENT, _FCM.text_supports

sys.stdout.reconfigure(encoding="utf-8")
SEPT = ROOT / "september" / "data" / "September_export.json"
CLASSIFIED = ROOT / "August_classification" / "data_sep" / "restaurants_classified.jsonl"
CHAIN_IDS = ROOT / "september" / "data" / "sept_chain_ids.json"
FAR_KM = 1.0
PLACE_LINK = re.compile(r"cid=|/place/|place_id|ftid=|/maps\?q=place", re.I)


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def spread(pts):
    return max((km(a, b) for i, a in enumerate(pts) for b in pts[i + 1:]), default=0.0)


def na(v):
    return v is None or str(v).strip() in ("", "NA")


def main(args):
    path = Path(args.file)
    print("=" * 78)
    print(f"  EXTRA GATES - area, smear, September rules   {path.name}")
    print("=" * 78)
    rect = AreaRectifier()
    TAX, is_city = rect.TAX, rect.is_city
    sept_ids = {str(r["source_id"]) for r in ijson.items(open(SEPT, "rb"), "item")}

    fails, warns = {}, {}
    states = defaultdict(Counter)
    values = Counter()
    city_as_area = []
    by_link = defaultdict(list)
    by_phone = defaultdict(list)
    by_brand_area = defaultdict(list)
    by_raw = defaultdict(list)
    prof = defaultdict(Counter)
    rows_for_leak = []
    baskin = []
    sept_rows = []
    n = 0
    for line in open(path, "rb"):
        r = orjson.loads(line)
        n += 1
        sid = str(r["source_id"])
        L, g = r.get("location") or {}, r.get("geo") or {}
        area, city, raw = L.get("area"), L.get("city") or "", L.get("raw") or ""
        ver = bool(r.get("is_verified_location"))
        cohort = "september" if sid in sept_ids else ("location record" if ver else "202608 restaurant")
        s = area_state(area, TAX, is_city)
        states[cohort][s] += 1
        if area:
            values[area] += 1
        if s == "city_as_area":
            city_as_area.append((sid, r.get("name"), area))
        try:
            pt = (float(g["lat"]), float(g["lng"]))
        except (TypeError, ValueError, KeyError):
            pt = None
        if area in TAX and raw:
            if text_supports(area, raw):
                prof[area][city] += 1
            else:
                rows_for_leak.append((sid, r.get("name"), area, city, raw))
        if not ver and pt:
            if not na(r.get("maps_url")):
                by_link[r["maps_url"]].append((sid, r.get("name"), pt))
            if not na(r.get("contact_phone")):
                by_phone[(r.get("chain_id"), r["contact_phone"])].append((sid, r.get("name"), pt))
            if area:
                by_brand_area[(r.get("chain_id"), area)].append((sid, r.get("name"), pt))
        if not ver and pt and raw and raw != "NA":
            by_raw[(r.get("chain_id"), raw)].append((sid, r.get("name"), pt, area, city))
        if not ver and "baskin" in str(r.get("name")).lower():
            baskin.append((sid, r.get("name"), area, city, r.get("contact_phone"),
                           (r.get("maps_url") or "")[:48], pt, raw))
        if sid in sept_ids:
            sept_rows.append(r)
    print(f"  records {n:,}")

    # ---------------------------------------------------------------- AREA
    print("\n  --- AREA ---")
    v = len(city_as_area)
    print(f"    A1 city used as an area            {v:>7,}" + ("   <-- FAIL" if v else ""))
    if v:
        fails["A1 city used as an area"] = v
        print(f"       e.g. {city_as_area[:4]}")
    tw = spelling_twins(values, TAX)
    listed = spelling_twins(values) .keys() - tw.keys()
    print(f"    A2 spelling-variant groups          {len(tw):>7,}" + ("   <-- FAIL" if tw else ""))
    if tw:
        fails["A2 spelling variants"] = len(tw)
        for grp in list(tw.values())[:5]:
            print(f"       e.g. {sorted(grp)}")
    print(f"       (key-twins that area_list.csv itself lists, left as the client's list: {len(listed)})")
    from rectify_areas import ARABIC_RX
    nonlatin = [(k, a) for k, a in ((k, v) for k, v in values.items()) if ARABIC_RX.search(k)]
    print(f"    A1b area in Arabic script           {len(nonlatin):>7,}" + ("   <-- FAIL" if nonlatin else ""))
    if nonlatin:
        fails["A1b Arabic area"] = len(nonlatin)
        print(f"       e.g. {nonlatin[:3]}")
    for cohort, c in states.items():
        tot = sum(c.values())
        print(f"    A3 {cohort:20} on area_list.csv {c['on_taxonomy'] / tot * 100:5.1f}% "
              f"| off-taxonomy {c['off_taxonomy']:,} | null {c['null']:,} | of {tot:,}")
    allow = {}
    for a, d in prof.items():
        tot = sum(d.values())
        allow[a] = None if tot < MIN_CONFIDENT else {c for c, k in d.items() if k / tot >= CITY_SHARE}
    leak = [x for x in rows_for_leak if allow.get(x[2]) and x[3] not in allow[x[2]]]
    print(f"    A4 cross-emirate leak (warn)        {len(leak):>7,}")
    if leak:
        warns["A4 cross-emirate leak"] = len(leak)
        for x in leak[:4]:
            print(f"       e.g. {x[1][:24]!r} area={x[2]!r} city={x[3]!r} raw={x[4][:40]!r}")

    # ---------------------------------------------------------------- CITY
    # A5: the city a record states vs the city its coordinates sit in, compared
    # emirate to emirate (Al Ain is in Abu Dhabi, not a contradiction of it).
    # The builder only corrects city where the area does not contradict the
    # coordinates, so a residue is expected - reported, not failed.
    sys.path.insert(0, str(ROOT))
    import numpy as np
    from scipy.spatial import cKDTree
    from brand_locations import CANON, K as CK, MAX_KM as CMAX
    from build_unified_202609 import emirate
    pts, labs, mains = [], [], []
    for line in open(path, "rb"):
        r = orjson.loads(line)
        L, g = r.get("location") or {}, r.get("geo") or {}
        try:
            pt = (float(g["lat"]), float(g["lng"]))
        except (TypeError, ValueError, KeyError):
            continue
        if r.get("is_verified_location"):
            if L.get("city"):
                pts.append(pt)
                labs.append(CANON.get(L["city"], L["city"]))
        elif L.get("city"):
            mains.append((pt, L["city"], r.get("name"), L.get("area")))
    tree, labs = cKDTree(np.array(pts)), np.array(labs)
    mism = []
    for pt, city, name, area in mains:
        d, i = tree.query([pt], k=CK)
        if d[0][0] * 111 > CMAX:
            continue
        cc = Counter(labs[i[0]]).most_common(1)[0][0]
        if emirate(city) != emirate(cc):
            mism.append((name, city, cc, area))
    print(f"\n  --- CITY ---")
    print(f"    A5 city contradicts coordinates (warn) {len(mism):>5,} of {len(mains):,}")
    if mism:
        warns["A5 city vs coordinates"] = len(mism)
        for x in mism[:3]:
            print(f"       e.g. {str(x[0])[:24]!r} city={x[1]!r} coords={x[2]!r} area={x[3]!r}")

    # --------------------------------------------------------------- SMEAR
    print("\n  --- SMEAR (the Baskin Robbins case) ---")
    s1 = [(u, m) for u, m in by_link.items()
          if PLACE_LINK.search(u) and len(m) > 1 and spread([x[2] for x in m]) > FAR_KM]
    s1b = [(u, m) for u, m in by_link.items()
           if not PLACE_LINK.search(u) and len(m) > 1 and spread([x[2] for x in m]) > FAR_KM]
    v = sum(len(m) for _, m in s1)
    print(f"    S1 one Maps PLACE link, >1 km apart {len(s1):>6,} links / {v:,} branches"
          + ("   <-- FAIL" if s1 else ""))
    if s1:
        fails["S1 shared place link"] = len(s1)
        for u, m in s1[:3]:
            print(f"       e.g. {len(m)}x {m[0][1][:28]!r} {u[:60]}")
    print(f"       (non-place links shared >1 km: {len(s1b):,} - search-style URLs, reported only)")
    s2 = [(k, m) for k, m in by_phone.items() if len(m) > 1 and spread([x[2] for x in m]) > FAR_KM]
    print(f"    S2 one phone on a brand >1 km (warn){len(s2):>6,} brand/phone pairs / "
          f"{sum(len(m) for _, m in s2):,} branches")
    if s2:
        warns["S2 shared phone"] = len(s2)
        for (cid, ph), m in sorted(s2, key=lambda kv: -len(kv[1]))[:3]:
            print(f"       e.g. {len(m)}x {m[0][1][:28]!r} {ph}")
    s3 = [(k, m) for k, m in by_brand_area.items() if len(m) > 1 and spread([x[2] for x in m]) > FAR_KM]
    print(f"    S3 one area on a brand >1 km (info) {len(s3):>6,} brand/area pairs")
    # S5: the copied ADDRESS itself - same brand, identical full address that is
    # not just the area name, on branches more than FAR_KM apart. Every one was
    # replaced by the branch's own verified address or NA.
    s5 = [(k, m) for k, m in by_raw.items()
          if len(m) > 1 and " - " in k[1]
          and any(str(x[3] or "").strip().casefold() != k[1].strip().casefold() for x in m)
          and spread([x[2] for x in m]) > FAR_KM]
    v = sum(len(m) for _, m in s5)
    print(f"    S5 one full address, >1 km apart     {len(s5):>6,} addresses / {v:,} branches"
          + ("   <-- FAIL" if s5 else ""))
    if s5:
        fails["S5 copied raw address"] = len(s5)
        for (cid, raw), m in sorted(s5, key=lambda kv: -len(kv[1]))[:3]:
            print(f"       e.g. {len(m)}x {m[0][1][:24]!r} {raw[:50]!r}")
    # A6: the emirate the address names vs the record's city (warn)
    sys.path.insert(0, str(ROOT / "export"))
    from export_to_json import parse_address_components
    from build_unified_202609 import is_area_name_raw
    a6, a6_ex, a6b, a6b_ex = 0, [], 0, []
    for (cid, raw), m in by_raw.items():
        pc = parse_address_components(raw)[0]
        if not pc:
            continue
        for sid, nm, pt, area, city in m:
            if city and emirate(pc) != emirate(city):
                if is_area_name_raw(raw, area):
                    # raw is the record's own area name ('Kalba'): an area-vs-city
                    # conflict, not a wrong address - reported below, not failed
                    a6b += 1
                    if len(a6b_ex) < 3:
                        a6b_ex.append((str(nm)[:22], city, area))
                    continue
                a6 += 1
                if len(a6_ex) < 3:
                    a6_ex.append((str(nm)[:22], city, pc, raw[:44]))
    # A FAIL since Sept 17: every such address was replaced by the branch's own
    # verified address naming its area, or NA (Sagar: "fix those 1053").
    print(f"    A6 address names another emirate than city {a6:>7,}" + ("   <-- FAIL" if a6 else ""))
    if a6:
        fails["A6 address emirate vs city"] = a6
        for x in a6_ex:
            print(f"       e.g. {x}")
    print(f"    A6b raw is the area's own name, its emirate differs from city (info) {a6b:>4,}")
    for x in a6b_ex:
        print(f"       e.g. {x}")
    # S5b: the same copy written with commas instead of " - " (S5 cannot see it)
    s5b = [(k, m) for k, m in by_raw.items()
           if len(m) > 1 and " - " not in k[1]
           and any(str(x[3] or "").strip().casefold() != k[1].strip().casefold() for x in m)
           and spread([x[2] for x in m]) > FAR_KM]
    v = sum(len(m) for _, m in s5b)
    print(f"    S5b one comma-form address, >1 km (warn) {len(s5b):>5,} addresses / {v:,} branches")
    if s5b:
        warns["S5b copied comma-form raw address"] = v
        for (cid, raw), m in sorted(s5b, key=lambda kv: -len(kv[1]))[:3]:
            print(f"       e.g. {len(m)}x {m[0][1][:24]!r} {raw[:50]!r}")
    # S6: a Maps PLACE link whose place (Serper caches) is >1 km from the branch
    from build_unified_202609 import PLACE_ID, place_coords
    places = place_coords()
    s6 = s6_known = 0
    for u, m in by_link.items():
        mm = PLACE_ID.search(u) if PLACE_LINK.search(u) else None
        loc = places.get(mm.group(1) or mm.group(2)) if mm else None
        if not loc:
            continue
        s6_known += len(m)
        s6 += sum(1 for x in m if km(x[2], loc) > FAR_KM)
    print(f"    S6 Maps place link >1 km from its branch (warn) {s6:>5,} of {s6_known:,} with a known place")
    if s6:
        warns["S6 place link far from branch"] = s6
    print(f"    S4 Baskin Robbins branches: {len(baskin)} | distinct areas "
          f"{len({b[2] for b in baskin})} | distinct phones {len({b[4] for b in baskin if not na(b[4])})} "
          f"| NA phones {sum(1 for b in baskin if na(b[4]))}")
    for b in sorted(baskin, key=lambda x: str(x[3]))[:12]:
        print(f"       {b[0]:>7} {str(b[3])[:10]:10} {str(b[2])[:22]:22} {str(b[4])[:16]:16} raw={str(b[7])[:44]!r}")

    # ----------------------------------------------------------- SEPTEMBER
    print("\n  --- SEPTEMBER RULES ---")
    cls = {str(orjson.loads(l)["branch_id"]): orjson.loads(l) for l in open(CLASSIFIED, "rb")}
    chain = json.loads(CHAIN_IDS.read_text(encoding="utf-8"))
    chain = chain.get("branches", chain) if isinstance(chain, dict) else chain
    tal = {}
    if isinstance(chain, dict):
        for k, v2 in chain.items():
            if isinstance(v2, dict):
                tal[str(k)] = v2.get("chain_locations_count")
    elif isinstance(chain, list):
        for v2 in chain:
            tal[str(v2.get("branch_id") or v2.get("source_id"))] = v2.get("chain_locations_count")
    c1 = c2 = c3 = 0
    ex1 = []
    for r in sept_rows:
        sid = str(r["source_id"])
        g = (cls.get(sid) or {}).get("google_locations_uae") or 0
        want = g if g >= 1 else tal.get(sid)
        if want is not None and r.get("chain_locations_count") != want:
            c1 += 1
            if len(ex1) < 3:
                ex1.append((sid, r["name"], r.get("chain_locations_count"), g, tal.get(sid)))
        ot, ct, cnt = r.get("outlet_type"), r.get("chain_type"), r.get("chain_locations_count") or 0
        if (ot == "Independent" and ct is not None) or \
           (ot == "Chain" and (ct not in ("Local Chain", "MNC Chain") or cnt < 2)):
            c2 += 1
        if r.get("is_verified_location") is not False:
            c3 += 1
    print(f"    records checked {len(sept_rows):,} | Talabat counts found for {len(tal):,}")
    for label, v in (("C1 chain_locations_count rule", c1), ("C2 outlet/chain consistency", c2),
                     ("C3 is_verified_location false", c3)):
        print(f"    {label:36}{v:>7,}" + ("   <-- FAIL" if v else ""))
        if v:
            fails[label] = v
    if ex1:
        print(f"       e.g. (sid, name, shipped, google, talabat) {ex1}")

    print("\n" + "=" * 78)
    print(f"  {'ALL HARD GATES PASSED' if not fails else f'{len(fails)} HARD GATE(S) FAILED: {fails}'}")
    if warns:
        print(f"  warnings (reported, not failed): {warns}")
    print("=" * 78)
    out = path.with_name(path.stem + ".extra_validation.json")
    out.write_text(json.dumps({"file": path.name, "failed": fails, "warnings": warns,
                               "area_states": {k: dict(v) for k, v in states.items()}},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  -> {out.name}")
    return 1 if fails else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True)
    sys.exit(main(p.parse_args()))
