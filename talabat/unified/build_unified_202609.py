"""Build talabat_unified_202609.jsonl - last month's file, plus this month.

SAGAR'S LOGIC (Sept 17 2026):
  "talabat_unified_202609.jsonl should have our old existing data with new menu
   items which got there ... rest would everything be same, then this new sept
   data should append ... make sure we rectify area as well canonical"

  1 BASE       talabat_unified_202608.jsonl - every record kept, in order.
  2 NEW ITEMS  the refresh's ADDED items (labelled, all kNN guesses human-
               reviewed, sanitized, translated) APPENDED to the menu of the
               restaurant they were found on. APPEND-ONLY: no existing item is
               removed or rewritten, so no price or label Tech holds changes.
               Google location records share their parent's source_id and menu
               (unified D4), so they receive the same new items.
  3 SEPTEMBER  September_export.json appended, is_verified_location=false added
               so every line carries the unified schema's 19 fields.
  4 AREA       location.area rectified to canonical on every record - the one
               field Sagar asked to change on existing data. rectify_areas.py.
  5 LOCATION   Sagar-approved repairs of the brand smear, each behind a flag:
               --fix-city, --fix-shared-links, --fix-raw-address (the copied
               brand address, and every address naming another emirate than
               the record's city - own verified address naming the record's
               area, else "NA").

ONE NAME, ONE LABEL. validate_unified_export.py fails the file if an item name
carries two std_terms or two ingredient lists anywhere in it. September was
never checked against 202608's names, and a new item can land on an existing
name after cleaning ("latte <emoji>" -> "Latte"). When they collide, 202608's
label wins outright, so no label Tech already holds ever changes; otherwise a
COMPLETE label beats an empty one, then Sagar-reviewed > September > machine.
Every harmonisation is audited.

VERIFIED WHILE WRITING, NOT AFTER. Each base record is compared with its source
line: every field except location.area and menu_items must be identical, and
menu_items must START with the original items unchanged. Any breach, or any
name left with two labels, discards the .tmp and writes nothing.

    python build_unified_202609.py --dry-run
    python build_unified_202609.py --out <scratch>/candidate.jsonl
    python build_unified_202609.py
"""
import argparse
import copy
import csv
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import ijson
import numpy as np
import orjson
from rapidfuzz import fuzz
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "August_menu"))
sys.path.insert(0, str(ROOT / "menu"))
sys.path.insert(0, str(ROOT))
from brand_locations import CANON, K as CITY_K, MAX_KM as CITY_MAX_KM   # noqa: E402
from build_august_export import menu_item                        # noqa: E402
from build_unified_export import SOFT_FIELDS, NA, clean_text     # noqa: E402
from clean_menu_data import clean_text as menu_clean_text        # noqa: E402
from export_to_json import parse_address_components              # noqa: E402
from rectify_areas import AreaRectifier, import_area_module      # noqa: E402
from taxonomy import key as area_key                             # noqa: E402
text_supports = import_area_module("fix_city_mismatch").text_supports
from validate_unified_export import (ARABIC, CJK, CTRL, EMOJI,   # noqa: E402
                                     INVIS, MOJI, NON_FOOD)

sys.stdout.reconfigure(encoding="utf-8")

MONTH = "202609"
BASE = HERE / "data" / "talabat_unified_202608.jsonl"
NEW = ROOT / "menu_refresh" / "data" / "refresh_202609" / "newitems" / "menu_items_final.jsonl"
SEPT = ROOT / "september" / "data" / "September_export.json"
PROFILES = ROOT / "menu_refresh" / "data" / "std_term_canonical_ingredients.json"
TAXO = ROOT / "August_menu" / "ingredients_export_02.json"
PRICE_MAX = 5000.0
REVIEWED = {"manual_review", "non_menu_recheck"}
TEXT_RX = (EMOJI, ARABIC, CJK, CTRL, INVIS, MOJI)
LOCATION_TEXT = ("raw", "city", "area", "sublocality")

# --fix-city. Towns inside an emirate are NOT errors against the emirate: an
# 'Al Ain' restaurant whose coordinates say Abu Dhabi is consistent. Comparisons
# are made emirate-to-emirate; the value written is the town when it is one.
EMIRATE_OF = {"Al Ain": "Abu Dhabi", "Khor Fakkan": "Sharjah", "Kalba": "Sharjah"}
# the one city misspelling in the file - the same correction Tech authorised in
# the area clean (reference: project_talabat_area_classification)
CITY_SPELLING = {"Ras Al-Khaimah": "Ras Al Khaimah"}

# --fix-shared-links. A Google Maps PLACE link names exactly one place, so on two
# branches more than FAR_KM apart it is a copy. The place's own coordinates come
# from the Serper caches; a branch within NEAR_KM of it owns the link - the same
# 300 m rule the July/August contact repair used.
SERPER_CACHES = (ROOT / "August_classification" / "data" / "serper_maps_cache.json",
                 ROOT / "August_classification" / "data_sep" / "serper_maps_cache.json",
                 ROOT / "address_audit" / "data" / "serper_branch_cache.json")
PLACE_LINK = re.compile(r"cid=|/place/|place_id|ftid=", re.I)
PLACE_ID = re.compile(r"cid=(\d+)|query_place_id=([\w-]+)")
FAR_KM, NEAR_KM = 1.0, 0.3

# --fix-raw-address. The copied brand address also sits in location.raw (3,263
# branches). Sagar chose each branch's OWN verified Google address, and gave the
# rule that makes it useful: "if we get this location raw then the area would
# only be Al Barsha 1 and ... city would be Dubai" - raw, area and city must
# describe one place. Decisions come from address_audit/branch_own_address.csv
# (text search, name-match recovery, centred search; every one same brand within
# 300 m). Not on Google Maps at the branch -> raw "NA" (Sagar's rule).
OWN_ADDRESS = ROOT / "address_audit" / "data" / "branch_own_address.csv"

# Google's official Dubai community spellings, so the area test can read them:
# 'Al Qouz Ind.first' is Al Quoz Industrial Area 1.
GOOGLE_SPELLING = ((re.compile(r"\bqouz\b", re.I), "Quoz"),
                   (re.compile(r"\bind\.\s*|\bind\b\s*", re.I), "Industrial Area "))
AREA_STEM_RATIO = 80


def area_names(a):
    """'Tourist Club Area (Al Zahiya)' -> {'touristclubarea', 'zahiya'}:
    the name and the alias the taxonomy writes beside it."""
    return {k for k in (area_key(p) for p in re.split(r"\s+-\s+|\(|\)", str(a or ""))) if k}


CITY_KEYS = {area_key(c) for c in ("Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Umm Al Quwain", "Ras Al Khaimah",
                                    "Fujairah", "Al Ain", "Khor Fakkan", "Kalba", "UAE", "U.A.E",
                                    "United Arab Emirates")}


def is_area_level_raw(rect, raw, area, city):
    """'JLT, Dubai' on a JLT restaurant: every comma part is the record's own
    area (any spelling area_list.csv knows), an emirate or the country - no
    street, shop or building. It names only the area, so it pertains to it even
    when a brand copied it onto several branches."""
    names = area_names(area)
    if not names or not isinstance(raw, str) or "," not in raw or " - " in raw:
        return False
    hit = False
    for seg in (s.strip() for s in raw.split(",")):
        if not seg:
            continue
        k = area_key(seg)
        m = rect.canon(seg, city or "")
        if k in names or (m and area_names(m) & names):
            hit = True
        elif not (k in CITY_KEYS or (city and k == area_key(city))):
            return False
    return hit


def is_area_name_raw(raw, area):
    """A raw that is only a place name - no street, no ' - ' or ',' parts - and
    that place is the record's own area ('Kalba' on area 'Kalba')."""
    return (isinstance(raw, str) and " - " not in raw and "," not in raw
            and bool(area_names(raw) & area_names(area)))


def address_names_area(rect, area, city, addr):
    """Sagar: the new raw must be the address "which area it pertains", else NA.
    -> (True, how) or (False, why). Never a guess:
      a segment IS the area (key(), alias-aware)          -> yes
      a segment maps to it through area_list.csv           -> yes
      NUMBERED area: a segment with the SAME number and a
        close spelling of the stem ('Al Qouz First')        -> yes, else no -
        fuzzy distance alone cannot tell 'Al Majaz 1' from 'Al Majaz 2'
      otherwise the project's fuzzy text_supports, unless a
        segment maps to a DIFFERENT area ('Markaziyah West') -> yes / no
    """
    if not area:
        return False, "record has no area to check the address against"
    names = area_names(area)
    segs = []
    for s in re.split(r"\s+-\s+|,|،", addr):
        for rx, rep in GOOGLE_SPELLING:
            s = rx.sub(rep, s)
        if s.strip():
            segs.append(s.strip())
    if any(area_key(s) in names for s in segs):
        return True, "address names the area"
    mapped = [m for m in (rect.canon(s, city or "") for s in segs) if m]
    if any(area_names(m) & names for m in mapped):
        return True, "address names the area (area_list.csv spelling)"
    other = sorted({m for m in mapped if not area_names(m) & names})
    num = re.search(r"^(.*?)(\d+)$", area_key(re.split(r"\s+-\s+|\(", area)[0]))
    if num:
        stem, n = num.group(1), num.group(2)
        for s in segs:
            ks = area_key(s)
            for m in re.finditer(r"(?<!\d)" + n + r"(?!\d)", ks):
                window = ks[:m.start()][-(len(stem) + 2):]
                # a window shorter than the stem cannot hold it, and partial_ratio
                # would score its letters against part of the stem ('Zone 1')
                if stem and len(window) >= len(stem) and fuzz.partial_ratio(stem, window) >= AREA_STEM_RATIO:
                    return True, "address names the area (numbered, Google spelling)"
        return False, ("address names another area: " + ", ".join(other) if other
                       else "address does not confirm the area's number")
    if text_supports(area, addr) and not other:
        return True, "address names the area (fuzzy spelling)"
    if other:
        return False, "address names another area: " + ", ".join(other)
    return False, "address names no area"


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def point(geo):
    try:
        return float(geo["lat"]), float(geo["lng"])
    except (TypeError, ValueError, KeyError):
        return None


def emirate(city):
    c = CANON.get(city, city)
    return EMIRATE_OF.get(c, c)


def place_coords():
    """cid / placeId -> (lat, lng), from every Serper response we hold."""
    out = {}
    for p in SERPER_CACHES:
        if not p.exists():
            continue
        for v in json.loads(p.read_text(encoding="utf-8")).values():
            for pl in (v.get("places") if isinstance(v, dict) else None) or []:
                if pl.get("latitude") is None:
                    continue
                for k in ("cid", "placeId"):
                    if pl.get(k):
                        out[str(pl[k])] = (pl["latitude"], pl["longitude"])
    return out


def dirty(s):
    return isinstance(s, str) and any(rx.search(s) for rx in TEXT_RX)


def scrub_item_text(it, st, prefix):
    """EVERY item text field, not only visibly dirty ones, goes through the scrub
    the shipped 202608 menus went through (build_july_update.scrub via
    clean_text): curly quotes straightened, mojibake repaired, invisibles and
    non-Latin script removed. The first candidate scrubbed only values matching
    the emoji/Arabic regexes and failed validate_unified_export.py with 1,448
    ftfy hits - 'What’s New', 'Big Nick’s' - on new AND September items.
    scrub() does not remove emoji, so the menu cleaner runs first when one is
    present. A value that cleans to nothing takes the field's empty form."""
    for f, empty in (("name", None), ("section", NA), ("description", None)):
        v = it.get(f)
        if not isinstance(v, str) or not v:
            continue
        s = menu_clean_text(v) or "" if EMOJI.search(v) else v
        s = (clean_text(s) if s else None) or None
        if s != v:
            it[f] = s if s else empty
            st[f"{prefix}_item_{f}_scrubbed"] += 1
    return it


def label_of(it):
    return (it.get("std_term"), tuple(it.get("ingredients") or []))


def main(args):
    t0 = time.time()
    out = Path(args.out) if args.out else HERE / "data" / f"talabat_unified_{MONTH}.jsonl"
    side = out.parent
    print("=" * 78)
    print(f"  BUILD {out.name}   (base 202608 + new menu items + September)")
    print("=" * 78)
    for p in (BASE, NEW, SEPT, PROFILES, TAXO):
        if not p.exists():
            raise SystemExit(f"missing input: {p}")
    st = Counter()

    # ------------------------------------------------ 1. new items -> shape
    vocab = {r["ingredient_name"].strip().lower() for r in json.loads(TAXO.read_text(encoding="utf-8"))}
    prof = {t: [i for i in v if i.lower() in vocab]
            for t, v in json.loads(PROFILES.read_text(encoding="utf-8")).items()}
    new_by_sid, dropped = defaultdict(list), []
    mi_stats = Counter()
    for line in open(NEW, "rb"):
        r = orjson.loads(line)
        p = r.get("price_aed")
        if p is None or p < 0 or p > PRICE_MAX:
            dropped.append((r["branch_id"], r.get("item_name"), p, "price null/negative/>5000"))
            continue
        it = scrub_item_text(menu_item(r, mi_stats), st, "new")
        if not it.get("name"):
            dropped.append((r["branch_id"], r.get("item_name"), p, "no name after cleaning"))
            continue
        new_by_sid[str(r["branch_id"])].append((it, r.get("std_term_method")))
        st["new_items_shaped"] += 1
    print(f"  new items shaped {st['new_items_shaped']:,} across {len(new_by_sid):,} restaurants"
          f" | dropped {len(dropped):,}")

    # ------------------------------------------------ 2. pass 1 over the base
    rect = AreaRectifier()
    base_label, area_of, base_sids = {}, {}, set()
    off_vals = Counter()
    lab_pts, lab_city = [], []          # Google-verified points: the city referee
    mains = {}                          # ("B", i) -> facts the two fixes need
    facts = {}                          # ("B", i) -> (location.raw, chain_id)
    n_base = 0
    for i, line in enumerate(open(BASE, "rb")):
        rec = orjson.loads(line)
        n_base += 1
        base_sids.add(rec["source_id"])
        for it in rec["menu_items"]:
            base_label.setdefault(it["name"], label_of(it))
        area, src = rect.rectify(rec)
        area_of[i] = (area, src)
        if src == "off_taxonomy" and area:
            off_vals[area] += 1
        L, pt = rec.get("location") or {}, point(rec.get("geo") or {})
        if rec.get("is_verified_location"):
            if pt and L.get("city"):
                lab_pts.append(pt)
                lab_city.append(CANON.get(L["city"], L["city"]))
        else:
            mains[("B", i)] = (pt, L.get("city"), rec.get("maps_url"),
                               rec.get("contact_phone"), rec["source_id"], rec.get("name"))
            facts[("B", i)] = (L.get("raw"), rec.get("chain_id"))
        if n_base % 5000 == 0:
            print(f"    pass 1: {n_base:,} records", flush=True)
    print(f"  base records {n_base:,} | distinct item names {len(base_label):,}")

    sept = list(ijson.items(open(SEPT, "rb"), "item", use_float=True))
    overlap = {r["source_id"] for r in sept} & base_sids
    print(f"  September records {len(sept):,} | source_id overlap with base {len(overlap)} (must be 0)")
    if overlap:
        raise SystemExit("September duplicates base restaurants - refusing")
    for r in sept:
        a = (r.get("location") or {}).get("area")
        if a and a not in rect.TAX:
            off_vals[a] += 1
        # scrubbed BEFORE labels are chosen: a straightened name is the name the
        # one-name-one-label rule must see
        kept = []
        for it in r["menu_items"]:
            scrub_item_text(it, st, "september")
            if it.get("name"):
                kept.append(it)
            else:
                st["september_item_dropped_no_name"] += 1
        r["menu_items"] = kept
    orphans = sorted(set(new_by_sid) - base_sids)
    st["new_items_orphan_restaurant"] = sum(len(new_by_sid[s]) for s in orphans)

    # ------------------------------------------------ 3. one name, one label
    # 202608's label wins outright: Tech already holds it. For every OTHER name
    # the candidates from September and the new items are ranked:
    #   1 COMPLETE beats empty - food WITH ingredients, or a non-food term.
    #     An empty list on a food term is a missing value, not a label. The
    #     first dry run proved it: September shipped "3x Iced Cafe Americano"
    #     as Hot & Cold Beverages with ingredients [], and letting that outrank
    #     the reviewed new label left one name with two ingredient lists.
    #   2 Sagar-reviewed new label (2) > September (1) > machine new label (0)
    #   3 how many rows carry it
    # A winner that is still an empty food label is filled ONCE, per name, from
    # its std_term profile - so every item of that name gets the same list.
    def complete(lab):
        return bool(lab[1]) or lab[0] in NON_FOOD

    harmonised = []
    chosen = dict(base_label)
    cands = defaultdict(Counter)
    for r in sept:
        for it in r["menu_items"]:
            if it["name"] not in base_label:
                cands[it["name"]][(label_of(it), 1)] += 1
    for items in new_by_sid.values():
        for it, method in items:
            if it["name"] not in base_label:
                cands[it["name"]][(label_of(it), 2 if method in REVIEWED else 0)] += 1
    for nm, c in cands.items():
        (lab, _rank), _n = max(c.items(),
                               key=lambda kv: (complete(kv[0][0]), kv[0][1], kv[1]))
        if not complete(lab):
            fill = prof.get(lab[0]) or []
            if fill:
                lab = (lab[0], tuple(fill))
                st["label_names_refilled_from_profile"] += 1
            else:
                st["label_names_still_empty_food"] += 1
        chosen[nm] = lab

    def apply_label(it, where):
        want = chosen[it["name"]]
        if label_of(it) != want:
            harmonised.append((where, it["name"], it.get("std_term"), "; ".join(it.get("ingredients") or []),
                               want[0], "; ".join(want[1])))
            it["std_term"], it["ingredients"] = want[0], list(want[1])
            st[f"label_harmonised_{where}"] += 1
        return it

    for r in sept:
        for it in r["menu_items"]:
            apply_label(it, "september")
    for items in new_by_sid.values():
        for it, _ in items:
            apply_label(it, "new_item")

    casing = rect.casing_map(list(off_vals.elements()))

    def final_area(key):
        if key[0] == "B":
            a, s = area_of[key[1]]
            return casing(a) if s == "off_taxonomy" else a
        a = (sept[key[1]].get("location") or {}).get("area")
        return casing(a) if a and a not in rect.TAX else a

    for j, r in enumerate(sept):
        mains[("S", j)] = (point(r.get("geo") or {}), (r.get("location") or {}).get("city"),
                           r.get("maps_url"), r.get("contact_phone"), str(r["source_id"]), r.get("name"))
        facts[("S", j)] = ((r.get("location") or {}).get("raw"), r.get("chain_id"))

    # ------------------------------------------------ 3b. --fix-city decisions
    # The brand smear copied one Google listing's ADDRESS onto every branch, and
    # city was parsed from that address: Baskin Robbins' Dubai branches read
    # 'Ras Al Khaimah'. The area repair fixed area; city was never touched.
    # Referee: coordinate kNN over Google-verified points (99.45% held-out).
    # City is changed only when coordinates are not contradicted by the area:
    #   area's emirate == coordinates   -> fix  (two independent sources agree)
    #   area's emirate unknown          -> fix  (coordinates alone)
    #   area's emirate == file city     -> KEEP (the coordinates are the suspect)
    #   area names a third emirate      -> KEEP, reported
    def area_emirate(a):
        c = rect.area_city.get(a)
        if not c:
            return None
        agg = Counter()
        for city, n in c.items():
            agg[emirate(city)] += n
        top, n = agg.most_common(1)[0]
        tot = sum(agg.values())
        return top if tot >= 3 and n / tot >= 0.9 else None

    tree, labels = cKDTree(np.array(lab_pts)), np.array(lab_city)
    city_fix, city_rows = {}, []
    for key, (pt, fc, _u, _p, sid, name) in mains.items():
        if fc in CITY_SPELLING:
            city_fix[key] = (CITY_SPELLING[fc], "spelling")
            fc = CITY_SPELLING[fc]
        if not pt or not fc:
            continue
        d, idx = tree.query([pt], k=CITY_K)
        if d[0][0] * 111 > CITY_MAX_KM:
            continue
        cc = Counter(labels[idx[0]]).most_common(1)[0][0]
        if emirate(fc) == emirate(cc):
            continue
        a = final_area(key)
        ae = area_emirate(a)
        if ae == emirate(cc):
            rule = "two-source: coordinates + area emirate"
        elif ae is None:
            rule = "coordinates only: area emirate unknown"
        else:
            rule = ("KEPT: area agrees with file city" if ae == emirate(fc)
                    else "KEPT: area names a third emirate")
        st[f"city {rule}"] += 1
        city_rows.append((key[0], key[1], sid, name, fc, cc, a, ae, rule))
        if not rule.startswith("KEPT"):
            city_fix[key] = (cc, rule)

    # ------------------------------------------------ 3c. --fix-shared-links
    places = place_coords()
    groups = defaultdict(list)
    for key, (pt, _c, u, ph, sid, name) in mains.items():
        if pt and u and u != NA and PLACE_LINK.search(u):
            groups[u].append((key, pt, ph, sid, name))
    link_fix, link_rows = {}, []
    for u, m in groups.items():
        if len(m) < 2 or max(km(a[1], b[1]) for x, a in enumerate(m) for b in m[x + 1:]) <= FAR_KM:
            continue
        st["shared place links (>1 km)"] += 1
        mm = PLACE_ID.search(u)
        loc = places.get(mm.group(1) or mm.group(2)) if mm else None
        if not loc:
            st["shared links whose place is not in any cache"] += 1
        shared_ph = {p for p, n in Counter(x[2] for x in m if x[2] and x[2] != NA).items() if n > 1}
        for key, pt, ph, sid, name in m:
            dist = km(pt, loc) if loc else None
            if dist is not None and dist <= NEAR_KM:
                st["shared link kept by its owner branch (<=300 m)"] += 1
                link_rows.append((key[0], key[1], sid, name, u, ph, round(dist, 3), "kept: owner"))
                continue
            fix = {"maps_url": NA}
            if ph in shared_ph:
                fix["contact_phone"] = NA
            link_fix[key] = fix
            st["shared link -> NA on a non-owner branch"] += 1
            st["  ...its copied phone -> NA too"] += "contact_phone" in fix
            link_rows.append((key[0], key[1], sid, name, u, ph,
                              round(dist, 3) if dist is not None else "",
                              "NA: " + ("farther than 300 m from the place" if loc else "place unknown")
                              + (" (phone copied too)" if "contact_phone" in fix else "")))
    print(f"  --fix-city {'ON' if args.fix_city else 'off (reported only)'} | "
          f"--fix-shared-links {'ON' if args.fix_shared_links else 'off (reported only)'} | "
          f"--fix-raw-address {'ON' if args.fix_raw_address else 'off (reported only)'}")
    if not args.fix_city:
        city_fix = {}
    if not args.fix_shared_links:
        link_fix = {}

    # ------------------------------------------------ 3d. --fix-raw-address
    # Runs AFTER the city and link decisions are final, so it sees what will be
    # written. The copied-address signature is the smear detector's: same brand
    # (chain_id), identical full address that is not just the area name, on
    # branches more than FAR_KM apart. For each such branch:
    #   own verified address -> raw = it (scrubbed); sublocality parsed from it
    #     with the export's own parse_address_components
    #     CITY  the address's emirate differs from the record's -> coordinates
    #           decide: they agree with the address -> city from the address;
    #           they do not -> the address is wrong (Google's error) -> NA
    #     AREA  kept if the address names it (text_supports, the project's fuzzy
    #           "the text backs it" test); otherwise taken from the address when
    #           a segment maps to area_list.csv, so raw and area agree
    #     a maps_url / contact_phone still "NA" is filled from the SAME listing
    #   not listed on Google Maps at the branch -> raw "NA", sublocality null
    own = {r["branch_id"]: r for r in csv.DictReader(open(OWN_ADDRESS, encoding="utf-8-sig"))}
    copied = defaultdict(list)
    for key, (raw, cid) in facts.items():
        pt = mains[key][0]
        a = final_area(key)
        if pt and raw and raw != NA and " - " in raw and raw.strip().casefold() != str(a or "").strip().casefold():
            copied[(cid, raw)].append((key, pt))
    raw_fix, raw_rows, copied_keys = {}, [], set()
    for (cid, raw), m in copied.items():
        if len(m) < 2 or max(km(a[1], b[1]) for x, a in enumerate(m) for b in m[x + 1:]) <= FAR_KM:
            continue
        for key, pt in m:
            copied_keys.add(key)
            sid, name = str(mains[key][4]), mains[key][5]
            o = own.get(sid) or {}
            city_now = city_fix[key][0] if key in city_fix else mains[key][1]
            area_now = final_area(key)
            phone_now = link_fix.get(key, {}).get("contact_phone", mains[key][3])
            maps_now = link_fix.get(key, {}).get("maps_url", mains[key][2])
            decision, note = o.get("decision"), ""
            loc, top = {}, {}
            if decision == "own_address":
                addr = clean_text(o.get("address") or "") or ""
                if not addr:
                    decision, note = "not_listed", "address empty after text scrub"
            if decision == "own_address":
                pc, _pa, ps = parse_address_components(addr)
                new_city = city_now
                if pc and emirate(pc) != emirate(city_now or ""):
                    d, idx = tree.query([pt], k=CITY_K)
                    cc = (Counter(labels[idx[0]]).most_common(1)[0][0]
                          if d[0][0] * 111 <= CITY_MAX_KM else None)
                    if cc and emirate(cc) == emirate(pc):
                        new_city, note = CANON.get(pc, pc), f"city from address (coordinates agree: {cc})"
                    else:
                        decision = "not_listed"
                        note = f"address rejected: it says {pc}, coordinates say {cc}"
            if decision == "own_address":
                new_area = area_now
                if not (area_now and text_supports(area_now, addr)):
                    for seg in reversed([s.strip() for s in re.split(r"\s+-\s+|,", addr) if s.strip()]):
                        got = rect.canon(seg, new_city or "")
                        if got:
                            if area_key(got) != area_key(area_now or ""):
                                new_area = got
                                note += ("; " if note else "") + "area from address"
                            break
                loc = {"raw": addr, "sublocality": ps or None}
                if new_city != city_now:
                    loc["city"] = new_city
                if new_area != area_now:
                    loc["area"] = new_area
                if phone_now == NA and o.get("phone"):
                    top["contact_phone"] = o["phone"]
                if maps_now == NA and o.get("cid"):
                    top["maps_url"] = f"https://www.google.com/maps?cid={o['cid']}"
                st["raw: own verified address"] += 1
                st["raw:   ...city taken from the address"] += "city" in loc
                st["raw:   ...area taken from the address"] += "area" in loc
                st["raw:   ...contact_phone filled from the same listing"] += "contact_phone" in top
                st["raw:   ...maps_url filled from the same listing"] += "maps_url" in top
            elif decision == "not_listed":
                loc = {"raw": NA, "sublocality": None}
                st["raw: NA (not on Google Maps at the branch)" if not note
                   else "raw: NA (address rejected or empty)"] += 1
            else:
                st["copied raw address with NO decision"] += 1
                note = "no decision in branch_own_address.csv - left unchanged"
            if loc:
                raw_fix[key] = (loc, top)
            raw_rows.append((("202608" if key[0] == "B" else "september"), key[1], sid, name,
                             o.get("source", ""), raw, loc.get("raw", raw), loc.get("sublocality", ""),
                             city_now, loc.get("city", city_now), area_now, loc.get("area", area_now),
                             top.get("contact_phone", ""), top.get("maps_url", ""), note,
                             "copied brand address"))

    # ------------------------------------------------ 3e. address names another emirate
    # The validated candidate still had 1,053 addresses naming another emirate
    # than the record's city - copied brand addresses written with commas (the
    # copy detector above needs " - "), and smeared addresses on single
    # branches. Coordinates agreed with the CITY on all 1,053, so the address is
    # the wrong field. Sagar, Sept 17: "fix those 1053 ... make sure it should
    # bring the same raw address which area it pertains if not then make it NA".
    # So, stricter than 3d - area and city are NEVER changed here:
    #   own verified address (same brand, <=300 m) that names the record's
    #     emirate AND area (address_names_area)         -> raw = it
    #   listed, but naming another emirate or area,
    #     or no area                                     -> raw "NA"
    #   not on Google Maps at the branch                 -> raw "NA"
    # Records are selected exactly as the output gate A6 selects them, on the
    # values that will be written (September's location text is scrubbed first).
    def written(key, v):
        return (clean_text(v) or None) if key[0] == "S" and isinstance(v, str) and v else v

    emirate_rows = 0
    for key, (raw, _cid) in facts.items():
        if key in copied_keys or key in raw_fix or not mains[key][0]:
            continue
        raw_out = written(key, raw)
        city_now = city_fix[key][0] if key in city_fix else written(key, mains[key][1])
        if not raw_out or raw_out == NA or not city_now:
            continue
        pc = parse_address_components(raw_out)[0]
        if not pc or emirate(pc) == emirate(city_now):
            continue
        emirate_rows += 1
        sid, name = str(mains[key][4]), mains[key][5]
        o = own.get(sid) or {}
        area_now = final_area(key)
        if is_area_name_raw(raw_out, area_now):
            # 'Kalba' on a Kalba restaurant whose city says Fujairah: the raw names
            # its own area, so it "pertains" - the conflict is area vs CITY (Kalba
            # and Khor Fakkan are Sharjah exclaves), not a wrong address. Kept.
            st["raw(emirate): kept - the raw is the record's own area name (city conflict)"] += 1
            raw_rows.append((("202608" if key[0] == "B" else "september"), key[1], sid, name, "",
                             raw_out, raw_out, "", city_now, city_now, area_now, area_now, "", "",
                             "kept: the raw is the record's own area name; its emirate conflicts with "
                             "the city, not with the area", "address named another emirate"))
            continue
        phone_now = link_fix.get(key, {}).get("contact_phone", mains[key][3])
        maps_now = link_fix.get(key, {}).get("maps_url", mains[key][2])
        decision, loc, top = o.get("decision"), {}, {}
        addr = (clean_text(o.get("address") or "") or "") if decision == "own_address" else ""
        if decision == "own_address":
            apc = parse_address_components(addr)[0] if addr else None
            if not addr:
                ok, note = False, "address empty after text scrub"
            elif apc and emirate(apc) != emirate(city_now):
                ok, note = False, f"address names {apc}, record is {city_now}"
            else:
                ok, note = address_names_area(rect, area_now, city_now, addr)
            if ok:
                loc = {"raw": addr, "sublocality": parse_address_components(addr)[2] or None}
                if phone_now == NA and o.get("phone"):
                    top["contact_phone"] = o["phone"]
                if maps_now == NA and o.get("cid"):
                    top["maps_url"] = f"https://www.google.com/maps?cid={o['cid']}"
                st["raw(emirate): own address naming the record's area"] += 1
                st["raw(emirate):   ...contact_phone filled from the same listing"] += "contact_phone" in top
                st["raw(emirate):   ...maps_url filled from the same listing"] += "maps_url" in top
            else:
                loc = {"raw": NA, "sublocality": None}
                why = ("names another emirate" if ", record is " in note else
                       note.split(":")[0].replace("address ", ""))
                st[f"raw(emirate): NA - listed, but {why}"] += 1
        elif decision == "not_listed":
            loc, note = {"raw": NA, "sublocality": None}, "not on Google Maps at the branch"
            st["raw(emirate): NA - not on Google Maps at the branch"] += 1
        else:
            note = "no decision in branch_own_address.csv - left unchanged"
            st["wrong-emirate raw address with NO decision"] += 1
        if loc:
            raw_fix[key] = (loc, top)
        raw_rows.append((("202608" if key[0] == "B" else "september"), key[1], sid, name,
                         o.get("source", ""), raw_out, loc.get("raw", raw_out), loc.get("sublocality", ""),
                         city_now, city_now, area_now, area_now,
                         top.get("contact_phone", ""), top.get("maps_url", ""),
                         note + (f" | Google: {addr}" if addr and loc.get("raw") == NA else ""),
                         "address named another emirate"))
    st["raw(emirate): records selected"] = emirate_rows
    if not args.fix_raw_address:
        raw_fix = {}

    # ------------------------------------------------ 4. pass 2: write + verify
    tmp = out.with_suffix(out.suffix + ".tmp")
    if not args.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
    fh = None if args.dry_run else open(tmp, "wb")
    breach, area_rows, add_rows = Counter(), [], []
    seen_label = {}
    label_conflicts = set()
    written = 0

    def check_labels(rec):
        for it in rec["menu_items"]:
            lab = label_of(it)
            if seen_label.setdefault(it["name"], lab) != lab:
                label_conflicts.add(it["name"])

    def emit(rec):
        nonlocal written
        check_labels(rec)
        if fh:
            fh.write(orjson.dumps(rec) + b"\n")
        written += 1

    try:
        for i, line in enumerate(open(BASE, "rb")):
            rec, orig = orjson.loads(line), orjson.loads(line)
            sid = rec["source_id"]
            area, src = area_of[i]
            if src == "off_taxonomy":
                area = casing(area)
            before = rec["location"].get("area")
            rec["location"]["area"] = area
            if area != before:
                st["area_changed"] += 1
                area_rows.append((i, sid, bool(rec.get("is_verified_location")), rec.get("name"),
                                  rec["location"].get("city"), before, area, src))
            key = ("B", i)
            if key in city_fix:
                rec["location"]["city"] = city_fix[key][0]
                st["city_changed"] += 1
            for f, v in link_fix.get(key, {}).items():
                if rec.get(f) != v:
                    rec[f] = v
                    st[f"{f}_set_NA_shared_link"] += 1
            rf = raw_fix.get(key)
            if rf:
                for f, v in rf[0].items():
                    rec["location"][f] = v
                for f, v in rf[1].items():
                    rec[f] = v
            have = {(it["name"], it["section"], it["price"]) for it in rec["menu_items"]}
            added = 0
            for it, _ in new_by_sid.get(sid, ()):
                k = (it["name"], it["section"], it["price"])
                if k in have:
                    st["new_item_skipped_already_on_menu"] += 1
                    continue
                have.add(k)
                rec["menu_items"].append(copy.deepcopy(it))
                added += 1
            if added:
                st["records_with_new_items"] += 1
                st["new_items_added"] += added
                add_rows.append((sid, bool(rec.get("is_verified_location")), rec.get("name"),
                                 len(orig["menu_items"]), added))
            # ---- verification against the source line ------------------------
            # Only what was authorised may differ: area always; city and the
            # two contact fields ONLY on the records those fixes selected.
            allowed = ({"location", "menu_items"} | set(link_fix.get(key, {}))
                       | (set(rf[1]) if rf else set()))
            a = {k: v for k, v in rec.items() if k not in allowed}
            b = {k: v for k, v in orig.items() if k not in allowed}
            if a != b:
                breach["base field changed outside area/menu/approved fixes"] += 1
            loc_ok = ({"area"} | ({"city"} if key in city_fix else set())
                      | (set(rf[0]) if rf else set()))
            la = {k: v for k, v in rec["location"].items() if k not in loc_ok}
            lb = {k: v for k, v in orig["location"].items() if k not in loc_ok}
            if la != lb:
                breach["base location field changed outside area/approved city"] += 1
            if rec["menu_items"][:len(orig["menu_items"])] != orig["menu_items"]:
                breach["an existing menu item changed or was removed"] += 1
            if list(rec.keys()) != list(orig.keys()):
                breach["base key order changed"] += 1
            emit(rec)

        for j, r in enumerate(sept):
            for f in SOFT_FIELDS:
                if r.get(f) is None:
                    r[f] = NA
                    st["september_soft_field_na"] += 1
            nm = r.get("name")
            if isinstance(nm, str) and nm:
                s = clean_text(nm)
                if s and s != nm:
                    r["name"] = s
                    st["september_name_scrubbed"] += 1
            L = r.get("location") or {}
            for f in LOCATION_TEXT:
                v = L.get(f)
                if isinstance(v, str) and v:
                    s = clean_text(v)
                    if s != v:
                        L[f] = s or None
                        st["september_location_scrubbed"] += 1
            a = L.get("area")
            if a and a not in rect.TAX and casing(a) != a:
                L["area"] = casing(a)
                st["september_area_casing"] += 1
            key = ("S", j)
            if key in city_fix:
                L["city"] = city_fix[key][0]
                st["city_changed"] += 1
            for f, v in link_fix.get(key, {}).items():
                if r.get(f) != v:
                    r[f] = v
                    st[f"{f}_set_NA_shared_link"] += 1
            rf = raw_fix.get(key)
            if rf:
                for f, v in rf[0].items():
                    L[f] = v
                for f, v in rf[1].items():
                    r[f] = v
            r["is_verified_location"] = False
            emit(r)
    finally:
        if fh:
            fh.close()

    # ------------------------------------------------ 5. report
    st["records_written"] = written
    print(f"\n  records written {written:,}  (base {n_base:,} + September {len(sept):,})")
    for k in ("new_items_added", "records_with_new_items", "new_item_skipped_already_on_menu",
              "new_items_orphan_restaurant", "label_harmonised_september", "label_harmonised_new_item",
              "label_names_refilled_from_profile", "label_names_still_empty_food", "area_changed",
              "september_area_casing", "september_name_scrubbed", "september_location_scrubbed",
              "new_item_name_scrubbed", "new_item_section_scrubbed", "new_item_description_scrubbed",
              "september_item_name_scrubbed", "september_item_section_scrubbed",
              "september_item_description_scrubbed", "september_item_dropped_no_name",
              "city_changed", "maps_url_set_NA_shared_link", "contact_phone_set_NA_shared_link"):
        print(f"    {k:42} {st[k]:>9,}")
    print("  area sources:", dict(rect.how.most_common()))
    print("  city check  :", {k: v for k, v in st.items() if k.startswith("city ")})
    print("  shared links:", {k: v for k, v in st.items() if "shared link" in k or k.startswith("  ...")})
    print("  raw address :", {k: v for k, v in st.items() if k.startswith("raw:") or "NO decision" in k})
    print("  raw, address named another emirate:")
    for k, v in st.items():
        if k.startswith("raw(emirate)"):
            print(f"    {k[len('raw(emirate): '):]:66} {v:>6,}")
    print("\n  WRITE-TIME GATES (every one must be 0)")
    gates = dict(breach)
    gates["names with more than one label"] = len(label_conflicts)
    gates["records written != base + September"] = abs(written - n_base - len(sept))
    if args.fix_raw_address:
        gates["copied raw address with no decision"] = st["copied raw address with NO decision"]
        gates["wrong-emirate raw address with no decision"] = st["wrong-emirate raw address with NO decision"]
    for k in ("base field changed outside area/menu/approved fixes",
              "base location field changed outside area/approved city",
              "an existing menu item changed or was removed", "base key order changed"):
        gates.setdefault(k, 0)
    for k, v in gates.items():
        print(f"     {'PASS' if not v else 'FAIL'}  {k:46} {v:,}")
    if label_conflicts:
        print(f"        e.g. {sorted(label_conflicts)[:5]}")
    failed = {k: v for k, v in gates.items() if v}

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 1 if failed else 0
    if failed:
        tmp.unlink(missing_ok=True)
        print(f"\n  BUILD REJECTED - .tmp discarded: {failed}")
        return 1
    tmp.replace(out)

    def write_csv(name, head, rows):
        with open(side / name, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(head)
            w.writerows(rows)
    write_csv(f"unified_{MONTH}_area_rectification.csv",
              ["row_index", "source_id", "is_verified_location", "name", "city",
               "area_before", "area_after", "source"], area_rows)
    write_csv(f"unified_{MONTH}_new_items_by_restaurant.csv",
              ["source_id", "is_verified_location", "name", "items_before", "items_added"], add_rows)
    write_csv(f"unified_{MONTH}_label_harmonisation.csv",
              ["where", "item_name", "std_term_before", "ingredients_before",
               "std_term_after", "ingredients_after"], harmonised)
    write_csv(f"unified_{MONTH}_new_items_dropped.csv",
              ["branch_id", "item_name", "price_aed", "reason"], dropped)
    write_csv(f"unified_{MONTH}_city_check.csv",
              ["cohort", "row_index", "source_id", "name", "city_in_file", "city_from_coordinates",
               "area", "area_emirate", "rule"],
              [(("202608" if c == "B" else "september"), *rest) for c, *rest in city_rows])
    write_csv(f"unified_{MONTH}_raw_address_fix.csv",
              ["cohort", "row_index", "source_id", "name", "address_source", "raw_before", "raw_after",
               "sublocality_after", "city_before", "city_after", "area_before", "area_after",
               "contact_phone_filled", "maps_url_filled", "note", "fix_set"], raw_rows)
    write_csv(f"unified_{MONTH}_shared_place_links.csv",
              ["cohort", "row_index", "source_id", "name", "maps_url", "contact_phone",
               "km_to_place", "action"],
              [(("202608" if c == "B" else "september"), *rest) for c, *rest in link_rows])
    (side / f"unified_build_report_{MONTH}.json").write_text(json.dumps({
        "output": out.name, "bytes": out.stat().st_size, "stats": dict(st),
        "area_sources": dict(rect.how), "orphan_restaurants_for_new_items": orphans,
        "gates": gates, "elapsed_min": round((time.time() - t0) / 60, 1),
    }, indent=2), encoding="utf-8")
    print(f"\n  -> {out}  ({out.stat().st_size / 1e6:.0f} MB)")
    print(f"  audits: unified_{MONTH}_*.csv + unified_build_report_{MONTH}.json in {side}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out", help="write here instead of data/ (a candidate for validation)")
    p.add_argument("--fix-city", action="store_true",
                   help="correct location.city from coordinates where the area does not contradict them")
    p.add_argument("--fix-raw-address", action="store_true",
                   help="replace a copied brand address with the branch's own verified Google address "
                        "(raw/area/city made to agree), or NA where it is not on Google Maps; and an "
                        "address naming another emirate than the city with the branch's own address "
                        "only when it names the record's area, else NA")
    p.add_argument("--fix-shared-links", action="store_true",
                   help="keep a shared Google place link only on the branch within 300 m of the place; NA elsewhere")
    sys.exit(main(p.parse_args()))
