"""Canonical `location.area` for the unified deliverable.

WHY THIS EXISTS. The unified file never received the area clean. Tech's own
ri-db.restaurants_full.cleaned.json did (97.64% on area_list.csv), but
talabat_unified_202608.jsonl still carried July's parsed-address vocabulary:
measured 2026-09-17, only 13,329 of 22,327 restaurant records (60%) held a
taxonomy area, with 103 spelling-variant groups ('Al Barsha 1' / 'Al Barsha
First') and 89 cities used as areas - so Tech's two datasets disagreed on the
area of 12,355 restaurants.

WHY NOT SIMPLY COPY THE CLEANED FILE. It was cleaned BEFORE the brand address
smear was found, from each row's own address text - which for smeared branches
was the brand's copied Google address. Scored against Talabat's page areaName:

                              non-smeared branches   smear-repaired branches
    current unified area             63.0%                  99.5%
    ri-db cleaned area               81.3%                  21.4%
    url slug area                    83.1%                  72.5%

So no single source can be applied blindly. The ORDER below was chosen by
measurement on 7,014 non-smeared branches that have a page areaName:

    current -> slug -> ri-db   (September's order)   85.9% right
    slug -> ri-db -> current                         87.4% right   <- used
    ri-db -> slug -> current                         82.5% right

MAIN RECORDS, first source that resolves wins:
  1 page areaName   Talabat's own area for the branch, fetched this month for
                    12,035 branches (every smear-repaired one included). Truth
                    by definition, so an off-taxonomy page value is KEPT.
  2 url slug        Talabat's vocabulary in the branch URL -> taxonomy
  3 ri-db cleaned   the approved bulk clean, single-row source_ids only (a
                    chain's 247 location rows share one source_id and carry no
                    address to tell them apart)
  4 current area    -> taxonomy
  otherwise         keep the current value as off-taxonomy, or NULL if it is a
                    city (either script) - the standing rule: a wrong area is
                    worse than an honest null.

GOOGLE LOCATION RECORDS (is_verified_location) share their parent's source_id
AND URL, so the slug and page tiers describe the PARENT branch, not this
location. They are mapped from their own area, then their own address
segments.

THE MATCHER IS IMPORTED: TaxMapper + taxonomy.load (numbered / wrong-emirate /
tie guards), and ARABIC_CITY / OFF_TAX_CANON / canon_case from
september/sanitize_areas.py. Fuzzy score proposes; it never decides a merge.
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ijson
import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# THREE folders each hold a `config.py`. Only area_classification's defines
# CITY_TOKENS, and whichever `config` is imported first is cached in
# sys.modules for the whole process. Path ORDER alone is not a fix: a caller that
# imports August_classification's config first (serper_contact_fill does, via
# serper_places) breaks `from config import CITY_TOKENS` inside sanitize_areas
# no matter where area_classification sits on the path. So area_classification's
# config is loaded explicitly and bound to the name only while sanitize_areas
# imports, and whatever held the name before is put back.
for p in ("september", "area_classification"):
    sys.path.insert(0, str(ROOT / p))


def import_area_module(name):
    """Import a module that does `import config` expecting area_classification's
    config (sanitize_areas, fix_city_mismatch), with that config bound to the
    name only while it loads. Whatever held the name before is put back, so
    August_classification's importers keep theirs."""
    import importlib
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "area_classification_config", ROOT / "area_classification" / "config.py")
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    prev = sys.modules.get("config")
    sys.modules["config"] = cfg
    try:
        return importlib.import_module(name)
    finally:
        if prev is not None:
            sys.modules["config"] = prev
        else:
            sys.modules.pop("config", None)


_SA = import_area_module("sanitize_areas")
ARABIC_CITY, CITY_TOKENS = _SA.ARABIC_CITY, _SA.CITY_TOKENS
OFF_TAX_CANON, canon_case, slug_area = _SA.OFF_TAX_CANON, _SA.canon_case, _SA.slug_area
from tax_map import TaxMapper                                    # noqa: E402
from taxonomy import key, load                                   # noqa: E402

TAXCSV = ROOT / "area_classification" / "area_list.csv"
CLEANED = ROOT / "area_classification" / "ri-db.restaurants_full.cleaned.json"
PAGE_CACHE = ROOT / "listing_comparison" / "output" / "areaname_cache.json"
URLS = ROOT / "menu_refresh" / "data" / "refresh_202609" / "scrape_targets_202609.jsonl"
SEGMENT = re.compile(r"\s+-\s+|,")
# ARABIC_CITY lists bare city names, but Talabat also writes the EMIRATE form with
# diacritics: 'إمارة الشارقةّ' ("Emirate of Sharjah", with a shadda) passed as an
# area in the first candidate and failed validate_unified_export.py's Arabic gate.
ARABIC_RX = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
HARAKAT = re.compile(r"[ً-ٰٟـ]")          # diacritics + tatweel
EMIRATE_PREFIX = re.compile(r"^(?:إمارة|امارة)\s+")


class AreaRectifier:
    def __init__(self):
        self.areas, self.TAX = load(TAXCSV)
        tax_city, rows = defaultdict(set), defaultdict(list)
        # area -> Counter(city) over EVERY cleaned row, taxonomy or not: the
        # emirate evidence the unified builder's city check arbitrates with
        self.area_city = defaultdict(Counter)
        for rec in ijson.items(open(CLEANED, "rb"), "item"):
            L = rec.get("location") or {}
            if L.get("area") in self.TAX:
                tax_city[L["area"]].add(L.get("city") or "?")
            if L.get("area") and L.get("city"):
                self.area_city[L["area"]][L["city"]] += 1
            rows[str(rec.get("source_id"))].append(L.get("area"))
        self.mapper = TaxMapper(self.areas, self.TAX, tax_city)
        self.ridb = {s: v[0] for s, v in rows.items() if len(v) == 1}
        self.url = {}
        for line in open(URLS, "rb"):
            t = orjson.loads(line)
            self.url[str(t["branch_id"])] = t.get("url") or ""
        self.page = json.loads(PAGE_CACHE.read_text(encoding="utf-8"))
        self.how = Counter()

    # ------------------------------------------------------------ primitives
    def is_city(self, v):
        if not v or not str(v).strip():
            return True
        s = str(v).strip()
        if s.lower() in CITY_TOKENS or s in ARABIC_CITY:
            return True
        if ARABIC_RX.search(s):
            bare = EMIRATE_PREFIX.sub("", HARAKAT.sub("", s)).strip()
            return bare in ARABIC_CITY
        return False

    def canon(self, v, city):
        """-> a taxonomy area, or None. Never a guess."""
        if self.is_city(v):
            return None
        return self.mapper.map(str(v), city)[0]

    def page_area(self, sid, city):
        real = ((self.page.get(sid) or {}).get("area_name_real") or "").strip()
        if not real or real.lower() in ("null", "none") or self.is_city(real):
            return None
        # a page value off the taxonomy is kept as a real place - but never in
        # Arabic script: no Arabic ships, and an unmapped one cannot be verified
        return self.canon(real, city) or (None if ARABIC_RX.search(real) else real)

    # ------------------------------------------------------------ records
    def main_record(self, rec):
        """-> (area, source). source 'off_taxonomy' marks a kept non-list value."""
        sid = str(rec.get("source_id"))
        L = rec.get("location") or {}
        city, cur = L.get("city") or "", L.get("area")
        tiers = (("page areaName", lambda: self.page_area(sid, city)),
                 ("url slug", lambda: self.canon(slug_area(self.url.get(sid), rec.get("name")), city)),
                 ("ri-db cleaned", lambda: self.canon(self.ridb.get(sid), city) if sid in self.ridb else None),
                 ("current area", lambda: self.canon(cur, city)))
        for label, fn in tiers:
            got = fn()
            if got:
                src = label if got in self.TAX else "off_taxonomy"
                self.how[f"main <- {label}" + ("" if got in self.TAX else " (off-taxonomy page value)")] += 1
                return got, src
        if self.is_city(cur) or ARABIC_RX.search(str(cur)):
            self.how["main -> NULL (area field held a city or unmappable Arabic)"] += 1
            return None, "null"
        self.how["main -> kept off-taxonomy"] += 1
        return cur, "off_taxonomy"

    def location_record(self, rec):
        L = rec.get("location") or {}
        city, cur = L.get("city") or "", L.get("area")
        got = self.canon(cur, city)
        if got:
            self.how["location <- own area"] += 1
            return got, "own area"
        for seg in SEGMENT.split(L.get("raw") or ""):
            got = self.canon(seg.strip(), city)
            if got:
                self.how["location <- own address segment"] += 1
                return got, "own address"
        if self.is_city(cur) or ARABIC_RX.search(str(cur)):
            self.how["location -> NULL (area field held a city or unmappable Arabic)"] += 1
            return None, "null"
        self.how["location -> kept off-taxonomy"] += 1
        return cur, "off_taxonomy"

    def rectify(self, rec):
        return (self.location_record(rec) if rec.get("is_verified_location")
                else self.main_record(rec))

    def casing_map(self, off_taxonomy_values):
        """One surface form per off-taxonomy place across the WHOLE file.

        Collapses on taxonomy.key(), NOT on casefold. The first candidate folded
        case only and failed its own gate with 7 groups the article split:
        'Al Rumailah' / 'Rumailah', 'Al Najda' / 'Najda', 'Al - Helio 2' /
        'Helio 2'. key() is the project's definition of "same place, different
        spelling" - case, punctuation, diacritics, the 'Al' article, ordinal
        words - and it keeps the trailing NUMBER, so 'Al Barsha 1' never merges
        into 'Al Barsha 3'. Only OFF-taxonomy values are collapsed: area_list.csv
        itself lists some key-twins ('Al Rumailah' AND 'Rumailah'), and the
        client's list outranks our normalisation, so those are left as listed.

        Survivor: hand-reviewed OFF_TAX_CANON first, then FREQUENCY (never
        .title(), which mangles JVC / DIP / ICAD), then the form without a
        dangling 'Al -' artefact."""
        vals = [OFF_TAX_CANON.get(v.strip().lower(), v) for v in off_taxonomy_values if v]
        by_key = defaultdict(Counter)
        for v, n in Counter(vals).items():
            by_key[key(v)][v] += n
        elect = {}
        for c in by_key.values():
            best = max(c.items(), key=lambda kv: (kv[1], not re.match(r"^al\s*-", kv[0], re.I)))[0]
            for v in c:
                elect[v] = best

        def f(v):
            if not v:
                return v
            v2 = OFF_TAX_CANON.get(v.strip().lower(), v)
            return elect.get(v2, v2)
        return f


def area_state(v, TAX, is_city):
    if v is None or not str(v).strip():
        return "null"
    if v in TAX:
        return "on_taxonomy"
    if is_city(v):
        return "city_as_area"
    return "off_taxonomy"


def spelling_twins(values, TAX=frozenset()):
    """Surface forms that fold to one taxonomy.key() - must be 0 in the output.

    A group made ONLY of taxonomy entries is excluded: area_list.csv lists
    'Al Rumailah' and 'Rumailah' both, and the client's list is the authority.
    Any group containing an off-taxonomy spelling is a real variant."""
    by = defaultdict(set)
    for v in values:
        if v:
            by[key(v)].add(v)
    return {k: v for k, v in by.items() if len(v) > 1 and not v <= set(TAX)}
