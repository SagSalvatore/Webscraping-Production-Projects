"""Map Sagar's hand-reviewed findings.csv back onto row_index.

THE PROBLEM. findings.csv was reviewed by hand from an export that predates the
row_index column, so it carries no id - only name, city, raw_text, sublocality,
fragment and the hand-written area_name. 482 rows, deduplicated from the 584
nulls, so one verdict can legitimately cover several identical rows.

TIER 1 - EXACT COMPOSITE KEY  (name, city, raw_text, sublocality), normalised
for case, whitespace and non-breaking spaces. Matches 575 of 584. All 482 keys
are distinct and NO key carries two different verdicts, so the join is
unambiguous - that is checked, not assumed.

TIER 2 - GUARDED FUZZY NAME, for rows EXCEL CORRUPTED on the way through:

    '8 - 1'   -> '08-Jan'      parsed as a date
    '21 :'    -> '21:00'       parsed as a time
    'Healthy & Co | Macro-Counted Meals'   the '|' in the NAME shifted columns
    '...ADNOC Service Station | Seih Shuaib - 953...'   same, in the address

The verdicts are fine; the key drifted. So the residue on both sides is paired
by fuzzy name within the same city, and ONLY when the pairing is mutually best
and unique - never one-way. Anything ambiguous is left unmapped and reported.

THE VERDICTS ARE THE USER'S AND ARE NOT OVERRIDDEN. Values are canonicalised
onto the taxonomy where they map, and existing surface forms are reused so no
casing twin is created. Values that are a CITY, a ROAD or a MALL are applied as
given but flagged loudly - they are what the validation gate rejects.

    python apply_manual_findings.py --dry-run
    python apply_manual_findings.py
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict

from rapidfuzz import fuzz, process

import config as C
from taxonomy import build_index, key, load, match

FINDINGS = C.HERE / "findings.csv"
OUT = C.DATA / "manual_findings.json"
REVIEW = C.DATA / "manual_findings_review.csv"
sys.stdout.reconfigure(encoding="utf-8")

NA = {"NA", "N/A", "NULL", "NONE", ""}
FUZZ_MIN = 85

NUM = re.compile(r"\d")
ROAD = re.compile(r"\b(road|rd|street|st|highway|blvd)\b", re.I)
MALLISH = re.compile(r"\b(mall|airport|port|world trade cent|trade centre|"
                     r"trade center)\b", re.I)


def norm(s):
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def ck(name, city, raw, sub):
    return (norm(name).lower(), norm(city).lower(),
            norm(raw).lower(), norm(sub).lower())


def main(args):
    print("=" * 78)
    print("  APPLY MANUAL FINDINGS")
    print("=" * 78)

    areas, TAX = load(C.HERE / "area_list.csv")
    idx = build_index(areas)
    src = json.loads(C.INPUT.read_text(encoding="utf-8"))
    out = json.loads(C.OUTPUT.read_text(encoding="utf-8"))
    cities = {c.lower() for c in C.UAE_CITIES}

    # surface forms already shipped, so a manual value that differs only in
    # casing reuses the existing spelling instead of creating a twin
    shipped = {}
    for r in out:
        a = (r.get("location") or {}).get("area")
        if a:
            shipped.setdefault(key(a), a)

    findings = list(csv.DictReader(open(FINDINGS, encoding="utf-8-sig")))
    fk = defaultdict(list)
    for r in findings:
        fk[ck(r["name"], r["city"], r["raw_text"], r["sublocality"])].append(r)
    dup = {k: v for k, v in fk.items()
           if len({norm(x["area_name"]).lower() for x in v}) > 1}
    print(f"  findings {len(findings):,} | distinct keys {len(fk):,} | "
          f"keys with conflicting verdicts {len(dup):,}")
    if dup:
        print("  ABORT: the same input has two different hand verdicts")
        for k, v in list(dup.items())[:5]:
            print(f"    {k[0][:30]}: {[norm(x['area_name']) for x in v]}")
        return 1

    # the null rows, as shipped
    nulls = []
    for i, (a, b) in enumerate(zip(src, out)):
        if (b.get("location") or {}).get("area"):
            continue
        S, O = a.get("location") or {}, b.get("location") or {}
        nulls.append({"i": i, "name": a.get("name") or "",
                      "city": O.get("city") or "(no city)",
                      "raw": S.get("area") or "(empty)",
                      "sub": S.get("sublocality") or ""})
    print(f"  null rows in the deliverable: {len(nulls):,}")

    # ---- tier 1 ----------------------------------------------------------
    verdict, how = {}, {}
    for n in nulls:
        k = ck(n["name"], n["city"], n["raw"], n["sub"])
        if k in fk:
            verdict[n["i"]] = norm(fk[k][0]["area_name"])
            how[n["i"]] = "exact_key"
    print(f"    tier 1 exact composite key : {len(verdict):,}")

    # ---- tier 2 ----------------------------------------------------------
    used = {ck(n["name"], n["city"], n["raw"], n["sub"])
            for n in nulls if n["i"] in verdict}
    res_f = [r for k, v in fk.items() if k not in used for r in v[:1]]
    res_n = [n for n in nulls if n["i"] not in verdict]
    paired = []
    by_city = defaultdict(list)
    for r in res_f:
        by_city[norm(r["city"]).lower()].append(r)
    for n in res_n:
        cands = by_city.get(norm(n["city"]).lower(), [])
        if not cands:
            continue
        scored = sorted(((max(fuzz.ratio(norm(n["name"]).lower(),
                                         norm(r["name"]).lower()),
                              fuzz.partial_ratio(norm(n["name"]).lower(),
                                                 norm(r["name"]).lower())), r)
                         for r in cands), key=lambda x: -x[0])
        best, r = scored[0]
        # unique winner only - a tie means we cannot tell which row it is
        if best < FUZZ_MIN or (len(scored) > 1 and scored[1][0] == best):
            continue
        verdict[n["i"]] = norm(r["area_name"])
        how[n["i"]] = "fuzzy_name"
        paired.append((n, r, best))
    print(f"    tier 2 fuzzy name (excel)  : {len(paired):,}")
    for n, r, s in paired:
        print(f"       {s:5.1f}  {n['name'][:26]:28}{n['raw'][:30]:32}"
              f"-> {norm(r['area_name'])}")

    unmapped = [n for n in nulls if n["i"] not in verdict]
    print(f"    still unmapped             : {len(unmapped):,}")
    for n in unmapped:
        print(f"       idx {n['i']:>6}  {n['name'][:26]:28}{n['city'][:12]:14}"
              f"{n['raw'][:34]}")

    # ---- canonicalise + classify ----------------------------------------
    # Sagar's rule: EVERY manual value is compared against area_list.csv FIRST.
    # Only what cannot be mapped there is treated as anything else, and a value
    # that is a city, a road or a mall is left NULL rather than shipped.
    #
    # The fuzzy tier below is guarded three ways, because a bare score >= 88
    # produces real errors on this data:
    #   numeric difference  'Al Qusais' / 'Al Qusais 1' are different districts
    #   wrong emirate       'Al Zahiyah' (Abu Dhabi) / 'Al Zahya' (Ajman) 90.0
    #   a tie               'Al Meena' scores 88.9 against BOTH 'Al Mina' and
    #                       'Al Muteena' - picking either is a coin flip
    tax_city = defaultdict(set)
    for r in out:
        L = r.get("location") or {}
        if L.get("area") in TAX:
            tax_city[L["area"]].add(L.get("city") or "?")

    # Several taxonomy entries carry an alias in parentheses or after a dash:
    #   'Tourist Club Area (Al Zahiya)'   'Jumeirah Village Circle - JVC'
    #   'Jumeirah Beach Residence - JBR'  'Al Zahya - N'
    # token_sort_ratio scores a short manual value poorly against the long full
    # name, so 'Al Zahiyah' missed 'Tourist Club Area (Al Zahiya)' entirely and
    # fell through to 'Al Zahya' - an Ajman area, 8 rows wrong. Indexing the
    # alias parts as well makes the short form reachable. Canonical names are
    # added first so a real entry always beats another entry's alias.
    def alias_variants(a):
        out = {a}
        m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", a)
        if m:
            out |= {m.group(1), m.group(2)}
        if " - " in a:
            head, tail = a.rsplit(" - ", 1)
            out |= {head, tail}
        return {x.strip() for x in out if len(x.strip()) >= 4}

    alias_of = {}
    for a in areas:
        alias_of.setdefault(a, a)
    for a in areas:
        for var in alias_variants(a):
            alias_of.setdefault(var, a)
    pool = list(alias_of)

    def to_taxonomy(v, city):
        """Map onto area_list.csv, or None. Never guesses."""
        hit, _ = match(v, idx, TAX)
        if hit:
            return hit, "exact"
        scored = process.extract(v, pool, scorer=fuzz.token_sort_ratio, limit=4)
        if not scored or scored[0][1] < 88:
            return None, None
        s = scored[0][1]
        winners = {alias_of[x[0]] for x in scored if x[1] == s}
        if len(winners) > 1:
            return None, "tie"
        m = winners.pop()
        if NUM.sub("", v).strip().lower() == NUM.sub("", m).strip().lower() \
                and key(v) != key(m):
            return None, "numbered"
        seen = tax_city.get(m)
        if seen and city not in seen:
            return None, "wrong_emirate"
        return m, ("alias" if scored[0][0] != m else "") + f"fuzzy_{s:.0f}"

    final, flags = {}, Counter()
    rows = []
    city_of = {n["i"]: n["city"] for n in nulls}
    for i, v in verdict.items():
        why = ""
        if v.upper() in NA:
            final[i], cls = None, "NA"
            flags["NA_left_null"] += 1
        else:
            hit, why = to_taxonomy(v, city_of.get(i, ""))
            if hit:
                final[i], cls = hit, "on_taxonomy"
            elif v.lower() in cities:
                final[i], cls = None, "IS_A_CITY_nulled"
            elif ROAD.search(v):
                final[i], cls = None, "IS_A_ROAD_nulled"
            elif MALLISH.search(v):
                final[i], cls = None, "IS_A_MALL_nulled"
            elif key(v) in shipped:
                final[i], cls = shipped[key(v)], "offtax_existing_form"
            else:
                final[i], cls = v, "offtax_new_area"
            flags[cls] += 1
        rows.append({"row_index": i, "manual_value": v,
                     "area_name": final[i] or "", "classification": cls,
                     "taxonomy_match": why or "", "matched_by": how[i]})

    print(f"\n  {'CLASSIFICATION':26}{'ROWS':>6}")
    for k, v in flags.most_common():
        mark = "   <-- gate rejects this" if k in (
            "IS_A_CITY", "IS_A_ROAD", "IS_A_MALL_OR_AIRPORT") else ""
        print(f"    {k:26}{v:>6}{mark}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    OUT.write_text(json.dumps({str(k): v for k, v in final.items()},
                              ensure_ascii=False), encoding="utf-8")
    with open(REVIEW, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["row_index", "manual_value",
                                          "area_name", "classification",
                                          "taxonomy_match", "matched_by"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["row_index"]))
    print(f"\n  -> {OUT.name}  ({len(final):,} verdicts keyed by row_index)")
    print(f"  -> {REVIEW.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
