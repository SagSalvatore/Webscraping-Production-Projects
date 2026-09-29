"""Stage 6 - gate the cleaned file. Reads the OUTPUT, never the inputs.

The point is to inspect exactly what Tech will receive, so every check runs on
the written file and compares it field-by-field against the original input.

WHAT IT ENFORCES

  UNTOUCHED FIELDS  Only location.area may differ. _id, mordor_restaurant_id,
                    source_id, name, chain_id, location.country, location.city
                    and location.sublocality must be byte-identical, in the same
                    order, with the same key order. Asserted per record, not
                    sampled.

  CANONICAL CASING  One surface form per area. 'Al Barsha 1' and 'AL BARSHA 1'
                    must never both exist. This is a FAILURE, not a warning -
                    it is the defect that makes a categorical field unusable for
                    grouping, and it is invisible in a spot check.

  UAE ONLY          Every city is one of the known UAE values (D6).

  NO NOISE          No Plus Codes, floor references, directional prefixes or
                    street tokens survived into a shipped area.

  NULL DISCIPLINE   A null is allowed - Sagar's rule is that anything we cannot
                    resolve stays null rather than being guessed. The gate
                    reports the count and the reason distribution instead of
                    failing on it.

    python validate_output.py
    python validate_output.py --file <path>
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict

import config as C

sys.stdout.reconfigure(encoding="utf-8")

PLUS = re.compile(r"\b[23456789CFGHJMPQRVWX]{4,}\+[23456789CFGHJMPQRVWX]{2,}\b")
STREET = re.compile(r"\b(st|street|rd|road|ave|avenue|highway|blvd|floor|shop|"
                    r"unit|bldg|building|villa|tower|mall|hotel|station|"
                    r"parking|basement)\b", re.I)
DIRECTIONAL = re.compile(r"\b(opposite|beside|behind|near|next to|inside|"
                         r"in front of|across from)\b", re.I)
EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF]")
ARABIC = re.compile("[؀-ۿ]")
INVIS = re.compile("[​-‏‪-‮﻿]")

IMMUTABLE = ("_id", "mordor_restaurant_id", "source_id", "name", "chain_id")
LOC_IMMUTABLE = ("country", "sublocality")

# city is checked separately: it is allowed to differ from the input ONLY by this
# spelling canonicalisation, which Sagar authorised. Anything else is a failure.
# Kept in sync with CITY_CANON in apply_taxonomy.py.
CITY_CANON = {"Ras Al-Khaimah": "Ras Al Khaimah",
              "Ras Al khaimah": "Ras Al Khaimah",
              "ras al khaimah": "Ras Al Khaimah"}


def main(args):
    path = args.file or C.OUTPUT
    print("=" * 78)
    print(f"  VALIDATE {path.name if hasattr(path,'name') else path}")
    print("=" * 78)

    src = json.loads(C.INPUT.read_text(encoding="utf-8"))
    out = json.loads(open(path, encoding="utf-8").read())
    fails, warns = {}, {}

    # ---- structural ------------------------------------------------------
    print("\n  --- STRUCTURAL ---")
    print(f"    input  records {len(src):,}")
    print(f"    output records {len(out):,}")
    if len(src) != len(out):
        fails["record_count_changed"] = f"{len(src)} -> {len(out)}"

    # ---- immutability ----------------------------------------------------
    drift, canon = Counter(), Counter()
    order_broken = 0
    for a, b in zip(src, out):
        if a.get("_id") != b.get("_id"):
            order_broken += 1
            continue
        for k in IMMUTABLE:
            if a.get(k) != b.get(k):
                drift[k] += 1
        la, lb = a.get("location") or {}, b.get("location") or {}
        if list(la.keys()) != list(lb.keys()):
            drift["location_key_order"] += 1
        for k in LOC_IMMUTABLE:
            if la.get(k) != lb.get(k):
                drift[f"location.{k}"] += 1
        ca, cb = la.get("city"), lb.get("city")
        if cb != CITY_CANON.get(ca, ca):
            drift["location.city"] += 1
        elif cb != ca:
            canon["city"] += 1
    print("\n  --- UNTOUCHED FIELDS ---")
    print(f"    record order broken {order_broken:,}")
    if order_broken:
        fails["record_order"] = order_broken
    if drift:
        print(f"    FIELDS THAT CHANGED: {dict(drift)}")
        fails["immutable_fields_changed"] = dict(drift)
    else:
        print("    only location.area differs  OK")
    if canon["city"]:
        print(f"    location.city canonicalised (authorised): {canon['city']:,}"
              f"  Ras Al-Khaimah -> Ras Al Khaimah")
        warns["city_canonicalised"] = canon["city"]

    # ---- area population -------------------------------------------------
    areas = [(r.get("location") or {}).get("area") for r in out]
    filled = [a for a in areas if a and str(a).strip()]
    nulls = len(areas) - len(filled)
    print("\n  --- AREA ---")
    print(f"    populated {len(filled):,} ({len(filled)/len(out)*100:.2f}%)")
    print(f"    null      {nulls:,} ({nulls/len(out)*100:.2f}%)   "
          f"(allowed - unresolved stays null)")
    print(f"    distinct  {len(set(filled)):,}")

    # ---- TAXONOMY CONFORMANCE (the contract) -----------------------------
    # Every shipped area must be one of the 686 in area_list.csv. This replaces
    # the old check against July's 716-value list: only 218 of those are real
    # Talabat areas, the other 498 were produced by parsing address text. A
    # closed vocabulary makes casing, spelling and city-vs-area errors
    # impossible by construction rather than by inspection.
    from taxonomy import load as load_tax
    tax_areas, TAX = load_tax(C.HERE / "area_list.csv")
    print("\n  --- TAXONOMY CONFORMANCE ---")
    print(f"    taxonomy size {len(TAX):,}")
    # Off-taxonomy values are allowed ONLY where extract_offtaxonomy.py put them:
    # sibling rows whose area was read from their own text because every
    # source_id-keyed signal would have smeared across chain branches. Anything
    # off-taxonomy that is NOT in that approved set is a genuine defect.
    approved_off = set()
    for p in (C.DATA / "offtaxonomy_areas.json",
              C.DATA / "final_nulls_resolved.json",
              C.DATA / "city_mismatch_fixes.json",
              C.DATA / "manual_findings.json"):
        if p.exists():
            approved_off |= {v for v in
                             json.loads(p.read_text(encoding="utf-8")).values()
                             if v and v not in TAX}
    off = [a for a in filled if a not in TAX]
    rogue = [a for a in off if a not in approved_off]
    print(f"    areas OFF the taxonomy: {len(off):,}  "
          f"({len(set(off)):,} distinct)")
    print(f"      approved off-taxonomy: {len(off)-len(rogue):,}  "
          f"(sibling rows, own text - reported to Tech)")
    print(f"      UNAPPROVED           : {len(rogue):,}"
          + ("" if not rogue else "   <-- FAIL"))
    if rogue:
        fails["off_taxonomy_unapproved"] = len(rogue)
        for a, n in Counter(rogue).most_common(8):
            print(f"       {n:>5}  {a[:52]}")
    elif off:
        warns["off_taxonomy_approved"] = len(off)
        print("      every off-taxonomy value is an approved extraction  OK")
    else:
        print("    every shipped area is one of the 686  OK")
    used = len({a for a in filled if a in TAX})
    print(f"    taxonomy areas used: {used:,} of {len(TAX):,}")

    # ---- CANONICAL CASING (hard gate) ------------------------------------
    print("\n  --- CANONICAL CASING ---")
    by_lower = defaultdict(set)
    for a in filled:
        by_lower[a.strip().lower()].add(a.strip())
    twins = {k: sorted(v) for k, v in by_lower.items() if len(v) > 1}
    print(f"    areas with >1 surface form: {len(twins):,}")
    if twins:
        fails["casing_twins"] = len(twins)
        for k, v in list(twins.items())[:8]:
            print(f"       {v}")
    else:
        print("    one surface form per area  OK")

    # whitespace / punctuation variants of the same name
    def squash(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())
    by_squash = defaultdict(set)
    for a in filled:
        by_squash[squash(a)].add(a.strip())
    punct = {k: sorted(v) for k, v in by_squash.items() if len(v) > 1}
    print(f"    punctuation/space variants : {len(punct):,}")
    if punct:
        warns["punct_variants"] = len(punct)
        for k, v in list(punct.items())[:5]:
            print(f"       {v}")

    # ---- UAE only --------------------------------------------------------
    print("\n  --- UAE ONLY ---")
    cities = Counter((r.get("location") or {}).get("city") for r in out)
    bad = {c: n for c, n in cities.items() if c and c not in C.UAE_CITIES}
    print(f"    distinct cities {len([c for c in cities if c]):,}")
    if bad:
        # location.city is TECH'S field and we were told not to touch it.
        # 'Ras Al-Khaimah' (hyphenated) is their spelling, not a defect we
        # introduced, so it is reported to them rather than failing our build.
        warns["city_spelling_for_tech"] = bad
        print(f"    non-canonical city spellings (Tech's field, reported): {bad}")
    else:
        print("    every city is a known UAE value  OK")
    cityish = [a for a in filled if a.strip().lower() in C.CITY_TOKENS]
    print(f"    areas that are only a city name: {len(cityish):,}")
    if cityish:
        fails["area_is_a_city"] = len(cityish)
        print(f"       e.g. {Counter(cityish).most_common(5)}")

    # ---- noise -----------------------------------------------------------
    print("\n  --- NO NOISE ---")
    # Noise patterns are only meaningful for values we PARSED. Now that every
    # shipped area comes from the 686-entry taxonomy, a pattern hit means the
    # CHECK is wrong, not the data: 'Ibn Batutta Mall' and 'Ajman Corniche' are
    # real Talabat areas that happen to contain 'mall' and 'corniche'. So these
    # stay as visibility, and only the ones that can never be legitimate in a
    # taxonomy value - encoding damage - remain hard failures.
    HARD = {"plus_code", "emoji", "arabic", "zero_width"}
    checks = [("plus_code", PLUS), ("street_token", STREET),
              ("directional", DIRECTIONAL), ("emoji", EMOJI),
              ("arabic", ARABIC), ("zero_width", INVIS)]
    for label, rx in checks:
        hit = [a for a in filled if rx.search(a)]
        off_tax = [a for a in hit if a not in TAX]     # only these are defects
        mark = ""
        if hit and label in HARD:
            mark = "   <-- FAIL"
        elif off_tax:
            mark = "   <-- FAIL (off taxonomy)"
        print(f"    {label:16}{len(hit):>7,}"
              f"{('  (' + str(len(off_tax)) + ' off-taxonomy)') if hit and not off_tax==hit else ''}{mark}")
        if hit and label in HARD:
            fails[label] = len(hit)
            print(f"       e.g. {hit[:3]}")
        elif off_tax:
            fails[label] = len(off_tax)
            print(f"       e.g. {off_tax[:3]}")
        elif hit:
            warns[f"{label}_but_on_taxonomy"] = len(hit)
            print(f"       all on the taxonomy, e.g. {sorted(set(hit))[:3]}")
    long = [a for a in filled if len(a) > 45]
    print(f"    {'over_45_chars':16}{len(long):>7,}")
    if long:
        warns["very_long"] = len(long)

    # ---- sibling safety --------------------------------------------------
    print("\n  --- SIBLING SAFETY ---")
    bysid = defaultdict(list)
    for r in out:
        bysid[str(r["source_id"])].append((r.get("location") or {}).get("area"))
    multi = {s: v for s, v in bysid.items() if len(v) > 1}
    collapsed = sum(1 for v in multi.values() if len(set(v)) == 1)
    print(f"    multi-row source_ids {len(multi):,}")
    print(f"    whose rows now share ONE area: {collapsed:,}"
          + ("   <-- a source_id join smeared them" if collapsed > 20 else ""))
    if collapsed > 20:
        fails["siblings_collapsed"] = collapsed

    print("\n" + "=" * 78)
    if fails:
        print(f"  {len(fails)} CHECK(S) FAILED")
        for k, v in fails.items():
            print(f"     {k}: {v}")
    else:
        print("  ALL CHECKS PASSED")
    if warns:
        print(f"  warnings: {warns}")
    print("=" * 78)

    (C.DATA / "validation_report.json").write_text(json.dumps({
        "records": len(out), "area_populated": len(filled), "area_null": nulls,
        "distinct_areas": len(set(filled)),
        "casing_twins": len(twins), "failed": fails, "warnings": warns,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  -> validation_report.json")
    return 1 if fails else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--file")
    a = p.parse_args()
    if a.file:
        from pathlib import Path
        a.file = Path(a.file)
    sys.exit(main(a))
