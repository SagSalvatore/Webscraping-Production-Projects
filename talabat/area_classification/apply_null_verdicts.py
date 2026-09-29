"""Reconcile Sagar's second hand pass, typed into data/nulls_by_city.csv.

That export carries row_index, so the join is exact - no composite key, no
fuzzy pairing, no Excel-corrupted dates to repair. This is the whole point of
shipping the id: the first pass (findings.csv) had none and needed two tiers
and a diagnosis of what Excel had mangled.

Every value goes through tax_map.TaxMapper first, so 'Al Ain (Al Jimi)' resolves
to the taxonomy's 'Al Jimi' rather than being kept as free text.

WHAT IS NOT APPLIED, and why:
  NA / blank                stays null, as written
  a CITY name ('Al Ain')    the row already carries that city; as an AREA it
                            adds nothing and the gate rejects it
  a ROAD ('Al Dhaid Road')  runs through many districts
Both are Sagar's own standing rule - null beats a wrong or empty-information
area - and both are reported per row rather than silently dropped.

CONSISTENCY CHECK. Rows with identical (name, city, raw_text) must carry the
same verdict. Six rows reading 'Al Ain Mall' were split three/three between
'Al Ain (Al Jimi)' and 'Al Ain'; the check finds that automatically instead of
relying on someone spotting it.

    python apply_null_verdicts.py --dry-run
    python apply_null_verdicts.py
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict

import config as C
from tax_map import TaxMapper
from taxonomy import key, load

SRC = C.DATA / "nulls_by_city.csv"
MERGE_INTO = C.DATA / "manual_findings.json"
REVIEW = C.DATA / "null_verdicts_review.csv"
sys.stdout.reconfigure(encoding="utf-8")

NA = {"NA", "N/A", "NULL", "NONE", ""}
ROAD = re.compile(r"\b(road|rd|street|st|highway|blvd)\b", re.I)


def main(args):
    print("=" * 78)
    print("  APPLY NULL VERDICTS  (second hand pass)")
    print("=" * 78)

    areas, TAX = load(C.HERE / "area_list.csv")
    src = json.loads(C.INPUT.read_text(encoding="utf-8"))
    out = json.loads(C.OUTPUT.read_text(encoding="utf-8"))
    cities = {c.lower() for c in C.UAE_CITIES}

    tax_city = defaultdict(set)
    shipped = {}
    for r in out:
        L = r.get("location") or {}
        a = L.get("area")
        if not a:
            continue
        shipped.setdefault(key(a), a)
        if a in TAX:
            tax_city[a].add(L.get("city") or "?")
    mapper = TaxMapper(areas, TAX, tax_city)

    rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
    print(f"  rows in {SRC.name}: {len(rows):,}")

    # the id is the contract - prove it before relying on it
    idxs = [int(r["row_index"]) for r in rows]
    assert len(set(idxs)) == len(idxs), "row_index is not unique"
    still_null = {i for i, b in enumerate(out)
                  if not (b.get("location") or {}).get("area")}
    stale = [i for i in idxs if i not in still_null]
    print(f"  row_index unique: yes | all point at a null row: "
          f"{'yes' if not stale else f'NO ({len(stale)} stale)'}")
    if stale:
        print(f"    stale: {stale[:10]}")

    # ---- consistency: identical input, different verdict -----------------
    grp = defaultdict(set)
    for r in rows:
        grp[(r["name"].strip().lower(), r["city"].strip().lower(),
             r["raw_text"].strip().lower())].add(r["area_name"].strip())
    split = {k: v for k, v in grp.items() if len(v) > 1}
    same_text = defaultdict(set)
    for r in rows:
        if r["raw_text"].strip() not in ("", "(empty)"):
            same_text[(r["city"].strip().lower(),
                       r["raw_text"].strip().lower())].add(
                           r["area_name"].strip())
    text_split = {k: v for k, v in same_text.items() if len(v) > 1}
    print(f"\n  --- CONSISTENCY ---")
    print(f"    same (name, city, text), different verdict: {len(split)}")
    for k, v in list(split.items())[:6]:
        print(f"       {k[0][:24]:26}{k[2][:30]:32}{sorted(v)}")
    print(f"    same (city, text), different verdict      : {len(text_split)}")
    for k, v in list(text_split.items())[:6]:
        print(f"       {k[1][:38]:40}{sorted(v)}")

    # ---- map -------------------------------------------------------------
    verdict, st, audit = {}, Counter(), []
    for r in rows:
        i = int(r["row_index"])
        v = " ".join((r["area_name"] or "").split()).strip()
        city = (out[i].get("location") or {}).get("city") or ""
        if v.upper() in NA:
            verdict[i], cls, how = None, "NA_stays_null", ""
        else:
            hit, how = mapper.map(v, city)
            if hit:
                verdict[i], cls = hit, "on_taxonomy"
            elif v.lower() in cities:
                verdict[i], cls = None, "IS_A_CITY_nulled"
            elif ROAD.search(v):
                verdict[i], cls = None, "IS_A_ROAD_nulled"
            elif key(v) in shipped:
                verdict[i], cls = shipped[key(v)], "offtax_existing_form"
            else:
                verdict[i], cls = v, "offtax_new_area"
        st[cls] += 1
        audit.append({"row_index": i, "name": r["name"], "city": city,
                      "raw_text": r["raw_text"], "manual_value": v,
                      "area_name": verdict[i] or "", "classification": cls,
                      "taxonomy_match": how or ""})

    print(f"\n  {'CLASSIFICATION':24}{'ROWS':>6}")
    for k, v in st.most_common():
        print(f"    {k:24}{v:>6}")

    print(f"\n  --- what did NOT survive ---")
    for a in audit:
        if a["classification"] in ("IS_A_CITY_nulled", "IS_A_ROAD_nulled"):
            print(f"    idx {a['row_index']:>6}  {a['name'][:22]:24}"
                  f"{a['manual_value'][:28]:30}{a['raw_text'][:30]}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    # merge onto the first pass, never replace it
    prev = json.loads(MERGE_INTO.read_text(encoding="utf-8")) \
        if MERGE_INTO.exists() else {}
    merged = dict(prev)
    merged.update({str(k): v for k, v in verdict.items()})
    MERGE_INTO.write_text(json.dumps(merged, ensure_ascii=False),
                          encoding="utf-8")
    print(f"\n  merged onto {len(prev):,} first-pass verdicts "
          f"-> {len(merged):,} total")

    with open(REVIEW, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(audit[0].keys()))
        w.writeheader()
        w.writerows(audit)
    print(f"  -> {MERGE_INTO.name}")
    print(f"  -> {REVIEW.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
