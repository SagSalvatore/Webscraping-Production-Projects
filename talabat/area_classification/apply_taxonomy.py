"""Write the cleaned file: location.area <- the taxonomy assignment, or null.

ONLY location.area IS TOUCHED. Every other field - _id, mordor_restaurant_id,
source_id, name, chain_id, location.country, location.city,
location.sublocality - is copied through byte-identical, asserted per record.

NULL RATHER THAN DIRTY TEXT. A row with no confident taxonomy match gets null,
not its original address string. Per Sagar: the deliverable holds clean,
classified areas; anything unresolved is null. Shipping '9GMX+JRC - Al Muntazi 1'
as an area would fail that. This DIFFERS from July's convention
(emit_mongo_json.py kept the original), and the difference is deliberate.

    python apply_taxonomy.py --dry-run
    python apply_taxonomy.py
"""
import argparse
import csv
import json
import sys
from collections import Counter

import config as C
from taxonomy import load

ASSIGN = C.DATA / "taxonomy_assignments.json"
sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    print("=" * 76)
    print("  APPLY TAXONOMY")
    print("=" * 76)

    if not ASSIGN.exists():
        print("  ABORT: run classify_to_taxonomy.py first")
        return 2
    areas, TAX = load(C.HERE / "area_list.csv")
    assign = {int(k): v for k, v in
              json.loads(ASSIGN.read_text(encoding="utf-8")).items()}

    # recover_nulls.py fills rows the main pass could not, from the URL slug,
    # the full address, and Serper+LLM. Merged here rather than overwriting the
    # main assignment file, so each stage's output stays separately inspectable.
    rec_path = C.DATA / "recovered_nulls.json"
    if rec_path.exists():
        rec = {int(k): v for k, v in
               json.loads(rec_path.read_text(encoding="utf-8")).items() if v}
        overlap = set(rec) & set(assign)
        assert not overlap, f"recovery overwrote {len(overlap)} main assignments"
        assign.update(rec)
        print(f"  recovered nulls merged: +{len(rec):,}")

    src = json.loads(C.INPUT.read_text(encoding="utf-8"))
    print(f"  records {len(src):,} | assignments {len(assign):,} | "
          f"taxonomy {len(TAX):,}")

    # Off-taxonomy areas, extracted from a sibling row's OWN text. These are the
    # rows where every source_id-keyed signal would smear across chain branches,
    # so the row's own text is the only per-branch evidence. area_list.csv is
    # Talabat's DELIVERY ZONES, not every UAE area - 'Rabdan', 'Wadi Al Safa 4'
    # and 'Dubai Production City' are real places that are not zones, and the
    # nearest zone is actively wrong (Wadi Al Safa 4 -> Al Safa 2). Approved by
    # Sagar as a deliberate exception, reported to Tech in its own CSV.
    off_path = C.DATA / "offtaxonomy_areas.json"
    offtax = set()
    if off_path.exists():
        d = {int(k): v for k, v in
             json.loads(off_path.read_text(encoding="utf-8")).items() if v}
        overlap = set(d) & set(assign)
        assert not overlap, f"off-taxonomy overwrote {len(overlap)} assignments"
        assign.update(d)
        offtax = {v for v in d.values() if v not in TAX}
        print(f"  off-taxonomy extracted: +{len(d):,} rows "
              f"({len(offtax):,} values outside area_list.csv)")

    # Last tier: Tavily web search + gpt-4.1-mini, for rows that survived every
    # earlier stage. May return a taxonomy area or a real UAE area named in the
    # retrieved text; both are acceptable, anything else was already rejected by
    # that script's gate.
    fin_path = C.DATA / "final_nulls_resolved.json"
    if fin_path.exists():
        d = {int(k): v for k, v in
             json.loads(fin_path.read_text(encoding="utf-8")).items() if v}
        overlap = set(d) & set(assign)
        assert not overlap, f"final tier overwrote {len(overlap)} assignments"
        assign.update(d)
        new_off = {v for v in d.values() if v not in TAX}
        offtax |= new_off
        print(f"  tavily+llm resolved: +{len(d):,} rows "
              f"({len(new_off):,} off-taxonomy)")

    # Cross-emirate repair (fix_city_mismatch.py). UNLIKE EVERY OTHER TIER THIS
    # ONE CORRECTS AND CAN CLEAR. The others only fill rows that are still empty,
    # so an absent key means "nothing to add"; here an explicit null means "the
    # value this row holds is wrong and no replacement could be justified", which
    # an absent key cannot express. Applied last so it overrides every source.
    fix_path = C.DATA / "city_mismatch_fixes.json"
    if fix_path.exists():
        d = {int(k): v for k, v in
             json.loads(fix_path.read_text(encoding="utf-8")).items()}
        repl = {i: v for i, v in d.items() if v}
        clear = {i for i, v in d.items() if not v}
        assign.update(repl)
        for i in clear:
            assign.pop(i, None)
        offtax |= {v for v in repl.values() if v not in TAX}
        print(f"  cross-emirate repair: {len(repl):,} corrected, "
              f"{len(clear):,} cleared to null")

    # Sagar's hand review (apply_manual_findings.py). HIGHEST PRIORITY - these
    # are human verdicts on rows every automated tier gave up on, so they are
    # applied after everything else and nothing may override them. Like the
    # cross-emirate tier this one can also CLEAR: a value that could not be
    # mapped onto area_list.csv and is a city, road or mall is stored as null
    # per Sagar's instruction, pending a second manual pass.
    man_path = C.DATA / "manual_findings.json"
    if man_path.exists():
        d = {int(k): v for k, v in
             json.loads(man_path.read_text(encoding="utf-8")).items()}
        repl = {i: v for i, v in d.items() if v}
        clear = {i for i, v in d.items() if not v}
        assign.update(repl)
        for i in clear:
            assign.pop(i, None)
        offtax |= {v for v in repl.values() if v not in TAX}
        print(f"  manual findings: {len(repl):,} assigned, "
              f"{len(clear):,} left null")

    # Split-entity repair. Fuzzy scoring against the 686 surfaced 30 off-taxonomy
    # values scoring >=85 against a taxonomy area. Only these 7 are the SAME
    # place: same city, and the difference is transliteration or a formatting
    # convention. Each was confirmed by hand - a score threshold is not a merge
    # rule, it is a candidate generator.
    #
    # The other 23 are deliberately NOT merged, and the reasons matter:
    #   Al Quoz / Al Quoz 1, Al Barsha / Al Barsha 1, Muhaisnah / Muhaisnah 3 ...
    #       parent vs numbered sub-area - a tiny edit distance, a real distinction
    #   Al Twar 4 / Al Twar 1, Al Nuaimia 3 / Al Nuaimia 2
    #       different number = different district. Never merge on a digit.
    #   Al Rashidiyah (Fujairah) / Al Rashidiya (Abu Dhabi), Al Muhallab
    #   (Fujairah) / Al Musalla (Dubai), Al Zahiyah (AD) / Al Zahia (Shj) /
    #   Al Zahya (Ajman)
    #       the CITY disagrees, so they cannot be the same place
    #   Al Marina / Al Mina - both Abu Dhabi, both real, both distinct
    ALIASES = {
        "Riggat Al Buteen": "Rigga Al Buteen",             # Dubai, spelling
        "Al Markaziya": "Al Markaziyah",                   # Abu Dhabi, spelling
        "Palm Jumeirah": "The Palm Jumeirah",              # Dubai, article
        "Jumeirah Village Circle": "Jumeirah Village Circle - JVC",
        "Al Jerf 1": "Al Jurf 1",                          # Ajman, spelling
        "Al Jerf 2": "Al Jurf 2",                          # Ajman, spelling
        "Al Sell": "Al Sall",                              # Ras Al Khaimah
        # from the hand-review passes - a canonical sweep over all 904 shipped
        # values found these five splitting one place across two surface forms
        "Barsha": "Al Barsha",                             # Dubai, article
        "The Villas": "The Villa",                         # Dubai, plural
        "Al Warqa'a First": "Al Warqa 1",                  # ordinal word
        "Al Warqa'A Third": "Al Warqa 3",                  # ordinal word
        "Al Wahda": "Al Wahdah",                           # Abu Dhabi, spelling
    }
    merged = 0
    for i, v in list(assign.items()):
        if v in ALIASES:
            assign[i] = ALIASES[v]
            merged += 1
    if merged:
        offtax -= set(ALIASES)
        print(f"  split entities merged onto taxonomy: {merged:,} rows "
              f"({len(ALIASES)} aliases)")

    # Everything else must be ON the taxonomy - anything else is an upstream bug
    bad = {v for v in assign.values() if v not in TAX and v not in offtax}
    if bad:
        print(f"  ABORT: {len(bad)} assignments are neither taxonomy areas nor "
              f"approved off-taxonomy: {sorted(bad)[:5]}")
        return 1

    # The ONLY city change we make, authorised by Sagar: canonicalise the
    # hyphenated spelling. 3 rows carried 'Ras Al-Khaimah' against 1,043 with
    # 'Ras Al Khaimah', so it is a spelling variant of an existing value, not new
    # information. Applied HERE rather than as a manual pass because this script
    # rebuilds the output from the original input every run - a hand-edit to the
    # cleaned file is silently reverted on the next build.
    CITY_CANON = {"Ras Al-Khaimah": "Ras Al Khaimah",
                  "Ras Al khaimah": "Ras Al Khaimah",
                  "ras al khaimah": "Ras Al Khaimah"}

    st = Counter()
    changed = []
    for i, rec in enumerate(src):
        loc = rec["location"]
        c = loc.get("city")
        if c in CITY_CANON and CITY_CANON[c] != c:
            loc["city"] = CITY_CANON[c]
            st["city_canonicalised"] += 1
        before = loc.get("area")
        after = assign.get(i)
        if after:
            st["assigned"] += 1
        elif before:
            st["nulled_unresolved"] += 1
        else:
            st["already_null"] += 1
        if before != after:
            changed.append({"source_id": rec.get("source_id"),
                            "name": rec.get("name"),
                            "city": loc.get("city"),
                            "before": before, "after": after})
        loc["area"] = after

    tot = len(src)
    print(f"\n  {'OUTCOME':24}{'ROWS':>9}")
    for k, v in st.most_common():
        print(f"    {k:24}{v:>9,}   ({v/tot*100:5.1f}%)")
    print(f"\n  rows whose area CHANGED: {len(changed):,}")
    print(f"  distinct areas shipped : "
          f"{len({v for v in assign.values()}):,} of {len(TAX):,}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    # integrity: order and immutable fields
    orig = json.loads(C.INPUT.read_text(encoding="utf-8"))
    assert len(orig) == len(src), "record count changed"
    for a, b in zip(orig, src):
        assert a["_id"] == b["_id"], "record order changed"
        for k in ("mordor_restaurant_id", "source_id", "name", "chain_id"):
            assert a.get(k) == b.get(k), f"{k} changed"
        for k in ("country", "sublocality"):
            assert a["location"].get(k) == b["location"].get(k), \
                f"location.{k} changed"
        # city may differ ONLY by the canonicalisation above - it must still map
        # from the original value, so no other city edit can slip through
        ca, cb = a["location"].get("city"), b["location"].get("city")
        assert cb == CITY_CANON.get(ca, ca), \
            f"location.city changed beyond canonicalisation: {ca!r} -> {cb!r}"
        assert list(a["location"].keys()) == list(b["location"].keys()), \
            "location key order changed"

    tmp = C.OUTPUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(src, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(C.OUTPUT)
    print(f"\n  -> {C.OUTPUT.name}  ({C.OUTPUT.stat().st_size/1e6:.1f} MB)")

    with open(C.RESOLUTION_MAP, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["source_id", "name", "city",
                                          "before", "after"])
        w.writeheader()
        w.writerows(changed)
    print(f"  -> {C.RESOLUTION_MAP.name}  ({len(changed):,} changes, auditable)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
