"""Stage 2 - resolve every row's area through an ordered ladder, cheapest first.

THE LADDER. Each tier only sees what the previous could not resolve, and the
tier that answered is recorded per row so a reviewer can triage by method.

  1  july_direct        source_id 1:1 against July's cleaned deliverable
  2  talabat_areaname   Talabat's OWN label, read from the restaurant page
  3  exact_vocab        the value already IS a canonical area
  4  raw_to_clean       July's caches/maps answer this exact raw string
  5  rules              deterministic extraction (Plus Codes, streets, dashes)
  6  candidate_new      area-shaped but outside the 716 - FLAGGED, not accepted
  7  unresolved         -> Serper + LLM stage, then null

WHY TIER 2 OUTRANKS COORDINATES. Talabat's areaName is their own vocabulary,
which is what the rest of the dataset uses. Reverse geocoding returns
OpenStreetMap's naming, which can disagree even when the point is right.

WHY NOT SERPER/APIFY FOR GEOCODING. Measured in July: Talabat's addresses appear
to originate from Google, so asking Google returns the same source that contains
the error (wrong in 2 of 3 spot-checks). Serper is used later ONLY to identify a
restaurant by NAME, never to reverse-geocode a point.

CITY GUARD. A candidate belonging to a different city than the row's own is
rejected. That is what stops 'Al Danah' (Abu Dhabi) landing on a Dubai row.

    python resolve_areas.py                 dry run - resolve, report, write nothing
    python resolve_areas.py --apply         write the cleaned file
"""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict

import config as C
from clean_rules import resolve as rule_resolve

sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    print("=" * 76)
    print("  RESOLVE AREAS")
    print("=" * 76)

    if not C.LOOKUP.exists():
        print("  ABORT: run build_lookup.py first")
        return 2

    lk = json.loads(C.LOOKUP.read_text(encoding="utf-8"))
    by_sid = lk["by_source_id"]
    by_raw = lk["by_raw_text"]
    by_an = lk["by_areaname"]
    vocab = set(lk["vocabulary"])
    area_city = lk["area_city"]
    records = json.loads(C.INPUT.read_text(encoding="utf-8"))

    # Approved new areas JOIN the vocabulary; rejected ones are struck out so a
    # later tier has to answer instead. Without this the resolver would keep
    # accepting values the approval stage already refused.
    approved_new, rejected_new = set(), set()
    ap = C.DATA / "new_areas_approved.csv"
    if ap.exists():
        with open(ap, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                (approved_new if r["verdict"] == "APPROVE"
                 else rejected_new).add(r["candidate_area"])
        vocab |= approved_new
        print(f"  approved new areas  +{len(approved_new):,}  "
              f"rejected {len(rejected_new):,}")

    # Whatever Serper+LLM recovered, keyed by record index.
    residue = {}
    rp = C.DATA / "residue_resolved.json"
    if rp.exists():
        residue = {int(k): v for k, v in
                   json.loads(rp.read_text(encoding="utf-8")).items() if v}
        print(f"  residue resolved    {len(residue):,}")

    print(f"  records {len(records):,} | vocabulary {len(vocab):,}")

    # A source_id with SEVERAL rows is a chain whose branches sit at different
    # addresses (verified: all 342 carry differing area text). Talabat's
    # areaName is keyed by source_id, so applying it to such a row would give
    # every branch the SAME area. Measured cost of not guarding this: 248 of the
    # 342 collapsed onto one value. Tier 2 is therefore restricted to source_ids
    # that appear exactly once, the same rule the July reuse map already uses.
    rows_per_sid = Counter(str(r.get("source_id")) for r in records)
    print(f"  multi-row source_ids: "
          f"{sum(1 for v in rows_per_sid.values() if v > 1):,} "
          f"(tier 2 is skipped for these)")

    st = Counter()
    resolution = []          # raw -> clean -> method, for audit
    new_areas = Counter()    # candidates outside the vocabulary (D6)
    new_area_rows = defaultdict(list)
    unresolved = []

    for i, r in enumerate(records):
        loc = r.get("location") or {}
        sid = str(r.get("source_id"))
        raw = (loc.get("area") or "").strip()
        city = loc.get("city")
        area, method = None, None

        # 0 - Serper+LLM already answered this row -----------------------
        if i in residue:
            area, method = residue[i], "serper_llm"
        # 1 -------------------------------------------------------------
        elif sid in by_sid:
            area, method = by_sid[sid], "july_direct"
        # 2 - single-row source_ids ONLY, see rows_per_sid above -----------
        elif sid in by_an and rows_per_sid[sid] == 1:
            cand = by_an[sid]
            if cand in vocab:
                area, method = cand, "talabat_areaname"
            elif cand in rejected_new:
                method = "unresolved:candidate_rejected"
            else:
                area, method = cand, "talabat_areaname_new"
        # 3 -------------------------------------------------------------
        elif raw and raw in vocab:
            area, method = raw, "exact_vocab"
        # 4 -------------------------------------------------------------
        elif raw and raw.lower() in by_raw:
            area, method = by_raw[raw.lower()], "raw_to_clean"
        # 5 / 6 ---------------------------------------------------------
        elif raw:
            got, why = rule_resolve(raw, vocab, city=city, area_city=area_city)
            if got and got in vocab:
                area, method = got, "rules"
            elif got:
                area, method = got, "candidate_new"
            else:
                method = f"unresolved:{why}"
        else:
            method = "unresolved:null_input"

        st[method] += 1
        if method.startswith("unresolved"):
            unresolved.append({"idx": i, "source_id": sid,
                               "name": r.get("name"), "city": city, "raw": raw})
        elif area and area not in vocab:
            new_areas[area] += 1
            if len(new_area_rows[area]) < 3:
                new_area_rows[area].append(
                    {"source_id": sid, "name": r.get("name"), "city": city,
                     "raw": raw})
        if area and raw and area != raw:
            resolution.append({"source_id": sid, "raw": raw, "clean": area,
                               "method": method})
        r["_resolved_area"] = area
        r["_resolution_method"] = method

    tot = len(records)
    print(f"\n  {'METHOD':30}{'ROWS':>9}{'':4}")
    for k, v in st.most_common():
        print(f"    {k:30}{v:>9,}   ({v/tot*100:5.1f}%)")

    resolved = sum(v for k, v in st.items() if not k.startswith("unresolved"))
    print(f"\n  RESOLVED     {resolved:>8,}  ({resolved/tot*100:5.1f}%)")
    print(f"  UNRESOLVED   {len(unresolved):>8,}  ({len(unresolved)/tot*100:5.1f}%)"
          f"   -> Serper + LLM stage")

    # ---- D6: new areas need approval, and must be UAE -------------------
    print(f"\n  NEW AREAS outside the {len(vocab)}-value vocabulary: "
          f"{len(new_areas):,} distinct  ({sum(new_areas.values()):,} rows)")
    for a, n in new_areas.most_common(12):
        ex = new_area_rows[a][0]
        print(f"     {n:>5}  {a[:36]:38} city={str(ex['city'])[:14]:16} "
              f"e.g. {str(ex['raw'])[:30]}")

    if new_areas:
        with open(C.NEW_AREAS, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["candidate_area", "rows", "city", "example_source_id",
                        "example_name", "example_raw", "APPROVE_Y_N"])
            for a, n in new_areas.most_common():
                ex = new_area_rows[a][0]
                w.writerow([a, n, ex["city"], ex["source_id"], ex["name"],
                            ex["raw"], ""])
        print(f"\n  -> {C.NEW_AREAS.name}  ({len(new_areas):,} for approval)")

    if unresolved:
        with open(C.REVIEW, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(unresolved[0].keys()))
            w.writeheader()
            w.writerows(unresolved)
        print(f"  -> {C.REVIEW.name}  ({len(unresolved):,} unresolved)")

    if resolution:
        with open(C.RESOLUTION_MAP, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["source_id", "raw", "clean",
                                              "method"])
            w.writeheader()
            w.writerows(resolution)
        print(f"  -> {C.RESOLUTION_MAP.name}  ({len(resolution):,} changes)")

    C.REPORT.write_text(json.dumps({
        "records": tot, "resolved": resolved, "unresolved": len(unresolved),
        "by_method": dict(st),
        "new_areas_pending_approval": len(new_areas),
        "new_area_rows": sum(new_areas.values()),
    }, indent=2), encoding="utf-8")
    print(f"  -> {C.REPORT.name}")

    if not args.apply:
        print("\n  DRY RUN - no cleaned file written. Use --apply once the new")
        print("  areas in the approval file have been reviewed.")
        return 0

    # ---- write, touching ONLY location.area -----------------------------
    src = json.loads(C.INPUT.read_text(encoding="utf-8"))
    assert len(src) == len(records), "record count drifted"
    written = kept = 0
    for s, r in zip(src, records):
        assert s["_id"] == r["_id"], "record order drifted"
        a = r["_resolved_area"]
        if a:
            s["location"]["area"] = a
            written += 1
        else:
            kept += 1          # null input stays null; unresolved keeps its text
    tmp = C.OUTPUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(src, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(C.OUTPUT)
    print(f"\n  area written on {written:,} rows | left as-is {kept:,}")
    print(f"  -> {C.OUTPUT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    sys.exit(main(p.parse_args()))
