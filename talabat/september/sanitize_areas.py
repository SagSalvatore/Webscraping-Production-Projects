"""Sanitize `location.area` on September_export.json against area_list.csv.

Sagar's standing rule: ANY area name is compared and mapped against
area_list.csv FIRST, and only what cannot be mapped there is treated as anything
else. That was done once, as a bulk clean of Tech's 24,689 rows; from September
it runs MONTHLY on each new cohort, so a raw Talabat area never reaches Tech.

THE MATCHER IS IMPORTED, NOT REIMPLEMENTED. `TaxMapper` and `taxonomy.load`
come from area_classification/, so every guard the bulk clean paid for applies
here unchanged:
    numbered       'Al Qusais' / 'Al Qusais 1' are different districts
    wrong emirate  'Al Zahiyah' (Abu Dhabi) / 'Al Zahya' (Ajman) score 90
    a tie          'Al Meena' hits 'Al Mina' AND 'Al Muteena' at 88.9
Fuzzy score generates a candidate; it never decides the merge.

`tax_city` - which emirates each taxonomy area has actually been seen in - is
read from the CLEANED file already delivered to Tech, so the emirate guard keeps
the evidence base the bulk clean built rather than starting empty.

THREE CANDIDATE SOURCES per record, in order of how directly each states the
area - the first two are asserted by the listing, the third is inferred:

    1 location.area   parsed from the Google address, or Talabat's area
    2 area_name       Talabat's own area for that restaurant - literally drawn
                      from the same zone vocabulary as area_list.csv, so it maps
                      when a parsed address fragment does not
    3 url slug        RECOVERY tier. '/restaurant/753762/baby-camel-mubzara'
                      carries the area after the name. Free, and on September it
                      resolved 72 rows the other two could not - every one an
                      EXACT taxonomy hit, because it is Talabat's own vocabulary.
                      It emptied the null bucket completely (10 -> 0).

WHAT HAPPENS TO A VALUE THAT MAPS TO NONE OF THEM - the same split the bulk
clean settled on, because "unmapped" is not one thing:

    the value is a CITY      -> NULL. 'Abu Dhabi' is not an area, in either
                                script. With the slug tier in place September
                                has none of these left, but the rule stands:
                                a listing with no recoverable area gets null,
                                never the emirate.
    a guard blocked it       -> keep the ORIGINAL, flag off_taxonomy. A tie
                                between 'Al Mina' and 'Al Muteena' means we do
                                not know which; the raw value is data, a guess
                                is not.
    a real area, no zone     -> keep it, flag off_taxonomy, report separately.
                                September has 25 such rows - Al Muroor, Jabal
                                Hafeet, Bain Al Jesrain. The bulk clean shipped
                                534 of these and reported them in
                                offtaxonomy_areas_for_tech.csv; nulling them
                                would delete correct data to satisfy a list.

Talabat's own `area_name` is preferred as the surviving off-taxonomy value: it
is Talabat's vocabulary, which is what the rest of the dataset speaks
('Rughaylat Rd - Alhawame Suburb' -> 'Al Nakheel Road').

ONLY `location.area` IS WRITTEN. Every other field is asserted byte-identical.

    python sanitize_areas.py --dry-run
    python sanitize_areas.py
"""
import argparse
import csv
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
AC = ROOT / "area_classification"
sys.path.insert(0, str(AC))
from tax_map import TaxMapper                                    # noqa: E402
from taxonomy import key, load                                   # noqa: E402
from config import CITY_TOKENS                                   # noqa: E402

EXPORT = HERE / "data" / "September_export.json"
LISTING = HERE / "data" / "sept_restaurants_for_classification.jsonl"
TAXCSV = AC / "area_list.csv"
CLEANED = AC / "ri-db.restaurants_full.cleaned.json"
OUT_MAP = HERE / "data" / "september_area_resolution_map.csv"
OUT_NULL = HERE / "data" / "september_area_unresolved.csv"
sys.stdout.reconfigure(encoding="utf-8")

IMMUTABLE = ("source_name", "source_id", "name", "cuisine", "key_cuisines",
             "restaurant_type", "outlet_type", "chain_type", "chain_id",
             "chain_locations_count", "currency", "geo", "contact_phone",
             "website", "maps_url")

# CITY NAMES IN ARABIC. CITY_TOKENS is Latin-only, so 'أبو ظبي' - Abu Dhabi,
# written in Arabic - sailed past the city check and was kept as an "area".
# Talabat writes the emirate into the area field on some listings in either
# script; both are cities and both resolve to null.
ARABIC_CITY = {
    "أبو ظبي", "ابو ظبي", "أبوظبي", "ابوظبي",      # Abu Dhabi
    "دبي",                                          # Dubai
    "الشارقة", "الشارقه",                          # Sharjah
    "عجمان",                                        # Ajman
    "الفجيرة", "الفجيره",                          # Fujairah
    "رأس الخيمة", "راس الخيمة",                    # Ras Al Khaimah
    "أم القيوين", "ام القيوين",                    # Umm Al Quwain
    "العين",                                        # Al Ain
    "الإمارات العربية المتحدة",                    # United Arab Emirates
}

# EXPLICIT SPELLING FAMILIES, applied before the mapper and each confirmed by
# hand. NOT a threshold change: 'Khaladiya' scores 72.7 token_sort against
# 'Al Khalidiyah', and a threshold low enough to catch it would also merge
# 'Al Barsha 1' into 'Al Barsha 3'. Similarity finds candidates; it never
# decides the merge. The result is still validated against the taxonomy below,
# so an entry here cannot introduce a value that is not one of the 686.
SPELLING = {
    "khaladiya": "Al Khalidiyah",
}

# OFF-TAXONOMY CANONICAL FORMS. These values are real areas with no Talabat
# zone, so they survive as-is - but they still have to agree with each other,
# or Tech ends up with two spellings of one place. Reviewed by hand:
#   'Baniyas'                  one row, 'Bani Yas' has five
#   'Mussafah Industrial city' the frequency collapse cannot fix a lone
#                              lowercase variant once its twins resolve
#                              elsewhere, so the casing is stated here
OFF_TAX_CANON = {
    "baniyas": "Bani Yas",
    "mussafah industrial city": "Mussafah Industrial City",
}


_SLUG = re.compile(r"/restaurant/\d+/([^?/#]+)")


def slug_area(url, name):
    """Talabat's URL carries the area AFTER the restaurant name:

        /restaurant/753762/baby-camel-mubzara        -> mubzara
        /restaurant/710610/torath-alain-al-maqam     -> al maqam

    The free recovery tier from the bulk clean, where it resolved 86.3% of
    nulls with no API call. It is Talabat's OWN area for that branch, drawn from
    the same vocabulary as area_list.csv, which is why it hits `exact` so often.

    Only the tail is returned - the leading restaurant name is stripped, and if
    the slug does not start with the name nothing is returned rather than
    guessing where the name ends. Whatever comes back is still mapped against
    the taxonomy, so a slug tail that is a branch code and not an area simply
    fails to match.
    """
    m = _SLUG.search(url or "")
    if not m:
        return None
    slug = m.group(1).replace("-", " ").strip()
    nm = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())).strip()
    if not nm or not slug.lower().startswith(nm):
        return None
    tail = slug[len(nm):].strip()
    return tail or None


def canon_case(values):
    """Collapse pure-casing variants of an off-taxonomy value onto one form.

    'Mussafah Industrial City' (5 rows) and 'Mussafah Industrial city' (2) are
    one place. The survivor is the MOST FREQUENT surface form, never .title(),
    which would mangle real acronyms (JVC, DIP, ICAD) - the same rule the bulk
    clean needed.
    """
    by_fold = defaultdict(Counter)
    for v, n in Counter(values).items():
        by_fold[v.casefold()][v] += n
    return {v: c.most_common(1)[0][0]
            for c in by_fold.values() for v in c}


def main(args):
    print("=" * 78)
    print("  SANITIZE location.area  ->  area_list.csv")
    print("=" * 78)

    areas, TAX = load(TAXCSV)
    print(f"  taxonomy : {len(areas)} canonical areas ({TAXCSV.name})")

    tax_city = defaultdict(set)
    if CLEANED.exists():
        for r in json.loads(CLEANED.read_text(encoding="utf-8")):
            L = r.get("location") or {}
            a = L.get("area")
            if a and a in TAX:
                tax_city[a].add(L.get("city") or "?")
        print(f"  emirate evidence from the shipped clean: "
              f"{len(tax_city):,} areas")
    else:
        print("  WARNING: cleaned file absent - emirate guard runs blind")
    mapper = TaxMapper(areas, TAX, tax_city)

    talabat_area, listing = {}, {}
    for line in open(LISTING, encoding="utf-8"):
        r = json.loads(line)
        talabat_area[str(r["branch_id"])] = r.get("area_name")
        listing[str(r["branch_id"])] = r

    recs = json.loads(EXPORT.read_text(encoding="utf-8"))
    print(f"  records  : {len(recs):,}")

    how = Counter()
    changes, unresolved, rows = [], [], []
    for r in recs:
        L = r["location"]
        before = L.get("area")
        city = L.get("city") or ""
        got, tag, src = None, None, None

        # URL slug is a RECOVERY tier, tried last: the first two are the
        # restaurant's stated area, the slug is inferred from a string.
        b = listing.get(r["source_id"], {})
        for label, cand in (
                ("location.area", before),
                ("talabat area_name", talabat_area.get(r["source_id"])),
                ("url slug", slug_area(b.get("url"), r["name"]))):
            if not cand:
                continue
            fam = SPELLING.get(cand.strip().lower())
            if fam and fam in TAX:
                got, tag, src = fam, "spelling family", label
                break
            hit, t = mapper.map(cand, city)
            if hit:
                got, tag, src = hit, t, label
                break
            if t and not tag:
                tag = t          # remember why the first one was blocked

        if got:
            how[("exact" if tag == "exact" else tag.split()[0]) + f" <- {src}"] += 1
            if got != before:
                changes.append((r["source_id"], before, got, tag, src))
        else:
            # not on the taxonomy - decide between NULL and keep-as-off-taxonomy
            ta = talabat_area.get(r["source_id"])
            keep = ta or before
            if (not keep or keep.strip().lower() in CITY_TOKENS
                    or keep.strip() in ARABIC_CITY):
                got, tag, src = None, tag or "value is a city", "null"
                how["NULL (area field holds a city)"] += 1
            else:
                keep = OFF_TAX_CANON.get(keep.strip().lower(), keep)
                got, src = keep, "off_taxonomy"
                how[f"off-taxonomy kept ({tag or 'no taxonomy match'})"] += 1
                if got != before:
                    changes.append((r["source_id"], before, got,
                                    tag or "off_taxonomy", src))
            unresolved.append((r["source_id"], r["name"], before, city, ta,
                               got or "", tag or "no taxonomy match"))
        rows.append((r, before, got, tag, src))

    # casing collapse on the OFF-TAXONOMY survivors only - taxonomy values are
    # already canonical. Done after the pass, when the frequency count is
    # complete, since the survivor is elected by frequency.
    case_map = canon_case([g for _, _, g, _, s in rows
                           if g and s == "off_taxonomy"])
    folded = 0
    for i, (r, b, g, t, s) in enumerate(rows):
        if g and s == "off_taxonomy" and case_map.get(g, g) != g:
            rows[i] = (r, b, case_map[g], t, s)
            folded += 1
            if (r["source_id"], b, g) not in {(c[0], c[1], c[2]) for c in changes}:
                changes.append((r["source_id"], b, case_map[g],
                                "casing collapse", s))
    if folded:
        print(f"\n  off-taxonomy casing variants collapsed : {folded}")

    onlist = sum(1 for _, _, g, _, s in rows if g and s != "off_taxonomy")
    offlist = sum(1 for _, _, g, _, s in rows if g and s == "off_taxonomy")
    print(f"\n  mapped onto the taxonomy : {onlist:,} "
          f"({onlist/len(recs)*100:.1f}%)")
    print(f"  off-taxonomy, kept       : {offlist:,}   (real areas, no Talabat zone)")
    print(f"  set to NULL              : {len(recs)-onlist-offlist:,}   (area field held a city)")
    print(f"  value CHANGED            : {len(changes):,}")
    print(f"\n  how each row resolved:")
    for k, n in how.most_common(14):
        print(f"     {n:>6}  {k}")
    print(f"\n  sample changes:")
    for sid, b, a, t, s in changes[:12]:
        print(f"     {sid:<9} {str(b)[:32]:34} -> {a[:28]:30} {t[:22]}")
    if unresolved:
        print(f"\n  sample not on the taxonomy:")
        for u in unresolved[:10]:
            print(f"     {u[0]:<9} {str(u[2])[:30]:32} -> "
                  f"{(u[5] or 'NULL')[:26]:28} {u[6][:20]}")

    after = Counter(g for _, _, g, _, s_ in rows if g and s_ != "off_taxonomy")
    off = Counter(g for _, _, g, _, s_ in rows if g and s_ == "off_taxonomy")
    print(f"\n  distinct taxonomy areas used : {len(after)} of {len(areas)}")
    assert all(a in TAX for a in after), "an area escaped the taxonomy"
    print(f"    top: {[a for a, _ in after.most_common(6)]}")
    print(f"  distinct off-taxonomy areas  : {len(off)}")
    print(f"    top: {[a for a, _ in off.most_common(6)]}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    # ONLY location.area changes - prove it before writing
    original = json.loads(EXPORT.read_text(encoding="utf-8"))
    for o, (r, _, got, _, _) in zip(original, rows):
        assert o["source_id"] == r["source_id"], "record order shifted"
        for f in IMMUTABLE:
            assert json.dumps(o.get(f), sort_keys=True) == \
                   json.dumps(r.get(f), sort_keys=True), f"{f} mutated"
        assert len(o["menu_items"]) == len(r["menu_items"]), "menu_items changed"
        for f in ("raw", "country", "city", "sublocality"):
            assert o["location"].get(f) == r["location"].get(f), \
                f"location.{f} mutated"
        r["location"]["area"] = got
    print("  ASSERT only location.area written : PASS")

    shutil.copy2(EXPORT, EXPORT.with_suffix(".json.pre_area.bak"))
    EXPORT.write_text(json.dumps(recs, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    with open(OUT_MAP, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_id", "area_before", "area_after", "method",
                    "source_field"])
        w.writerows(changes)
    with open(OUT_NULL, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_id", "name", "area_before", "city",
                    "talabat_area_name", "area_after", "blocked_by"])
        w.writerows(unresolved)
    print(f"\n  -> {EXPORT.name}  (location.area cleaned in place)")
    print(f"  -> {OUT_MAP.name}   ({len(changes):,} changes)")
    print(f"  -> {OUT_NULL.name}  ({len(unresolved):,} rows "
          f"- Tech's off-taxonomy + null report)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
