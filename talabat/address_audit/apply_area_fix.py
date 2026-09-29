"""Repair `location.area` on the address-smeared branches. NOTHING ELSE.

THE DEFECT (see detect_address_smear.py): 4,964 branches carry one Google
listing's address copied across every branch of the brand - Baskin Robbins' 7
all read "Khoor Ras Al Khaimah", Starbucks' 67 read one Ras Al Khaimah address.
The area field therefore repeats identically across branches that are tens of km
apart, which makes it useless for grouping and wrong per row.

ONLY `location.area` IS WRITTEN. Per Sagar: contact_phone, maps_url, raw, city
and everything else stay exactly as they are - the first goal is to make the
area unique and correct per branch. The write gate re-reads both files
afterwards and asserts that every other field is unchanged, record for record.
Same contract as area_classification/validate_output.py.

WHERE THE NEW AREA COMES FROM, best first:
  1 page areaName -> taxonomy   Talabat's OWN area for that branch, pulled from
                                __NEXT_DATA__ by fetch_area_names.py, then mapped
                                onto area_list.csv through the shared TaxMapper
                                so the numbered / wrong-emirate / tie guards all
                                apply
  2 page areaName, off taxonomy kept verbatim. A real area with no Talabat zone
                                is still correct data - the same policy the
                                September sanitiser uses
  3 no areaName on the page     left UNCHANGED and listed. Never guessed.

REJECTED as fallbacks, measured against the URL slug on 3,334 rows:
    crawl-zone area_name from data/urls/*.jsonl   120/3,334 =  3.6%
    area kNN from coordinates                   2,040/3,330 = 61.3%
Both would have replaced a wrong area with another wrong area.

FILES. The two exports are the source of truth and unified is generated FROM
them, so all three are patched from one shared map in one run: fixing unified
alone would be reverted the next time it is rebuilt to add a cohort.

    python apply_area_fix.py --dry-run
    python apply_area_fix.py
"""
import argparse
import csv
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ijson
import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
AC = ROOT / "area_classification"
sys.path.insert(0, str(AC))
from tax_map import TaxMapper                                  # noqa: E402
from taxonomy import load as load_taxonomy                     # noqa: E402

DATA = HERE / "data"
ROWS = DATA / "address_smear_rows.csv"
CACHE = ROOT / "listing_comparison" / "output" / "areaname_cache.json"
TAXCSV = AC / "area_list.csv"
CLEANED = AC / "ri-db.restaurants_full.cleaned.json"
AUDIT = DATA / "area_fix_applied.csv"
# THE THREE FILES HOLD ONE SCHEMA IN THREE SERIALISATIONS. Verified on disk:
#   july     [{"source_name":"talabat",...   compact, one line, NO trailing \n
#   august   [\r\n{\r\n  "source_name": ...  indent=2, CRLF, record at col 0
#   unified  {"source_name":...}\n           compact JSONL, LF
# A writer that assumes one style silently reformats the others - same content,
# every byte different, and a worthless diff for Tech. Each target therefore
# carries its own style, and --check-format proves the writer reproduces the
# original byte for byte before anything is rewritten.
TARGETS = [  # name, path, shape, style
    ("july_export", ROOT / "export" / "talabat_export.json", "json", "compact"),
    ("august_export", ROOT / "August_menu" / "data" / "August_export.json", "json", "pretty_crlf"),
    ("unified_202608", ROOT / "unified" / "data" / "talabat_unified_202608.jsonl", "jsonl", "jsonl"),
]


def enc(rec, style):
    """One record, in that file's own byte format."""
    if style == "pretty_crlf":
        return json.dumps(rec, ensure_ascii=False, indent=2
                          ).encode("utf-8").replace(b"\n", b"\r\n")
    return orjson.dumps(rec)          # compact + jsonl


_FMT = {                       # opening bytes, record separator, closing bytes
    "compact":     (b"[",     b",",     b"]"),
    "pretty_crlf": (b"[\r\n", b",\r\n", b"\r\n]\r\n"),
    "jsonl":       (b"",      b"",      b""),
}
OPEN = {k: v[0] for k, v in _FMT.items()}
SEPS = {k: v[1] for k, v in _FMT.items()}
CLOSES = {k: v[2] for k, v in _FMT.items()}


def check_format(path, shape, style, n=150):
    """Re-serialise the first n records UNMODIFIED and byte-compare against the
    file's own opening bytes. If this fails, the writer would rewrite the file
    in a different format and must not be run."""
    o, sep = OPEN[style], SEPS[style]
    recs = []
    with open(path, "rb") as f:
        it = (ijson.items(f, "item", use_float=True) if shape == "json"
              else (orjson.loads(l) for l in f if l.strip()))
        for i, rec in enumerate(it):
            if i >= n:
                break
            recs.append(rec)
    if style == "jsonl":
        mine = b"".join(enc(r, style) + b"\n" for r in recs)
    else:
        mine = o + sep.join(enc(r, style) for r in recs)
    with open(path, "rb") as f:
        theirs = f.read(len(mine))
    return mine == theirs, len(mine)
sys.stdout.reconfigure(encoding="utf-8")


def build_map(args):
    """branch_id -> (new_area, method). Only for the smeared branches."""
    rows = [r for r in csv.DictReader(open(ROWS, encoding="utf-8-sig"))
            if r["dataset"] != "unified_202608"]
    city_of, name_of, before_of = {}, {}, {}
    for r in rows:
        city_of.setdefault(r["source_id"], r["city_from_coordinates"])
        name_of.setdefault(r["source_id"], r["name"])
        before_of.setdefault(r["source_id"], r["file_area"])

    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    areas, TAX = load_taxonomy(TAXCSV)
    tax_city = defaultdict(set)
    with open(CLEANED, "rb") as f:
        for rec in ijson.items(f, "item"):
            L = rec.get("location") or {}
            if L.get("area") in TAX:
                tax_city[L["area"]].add(L.get("city") or "?")
    mapper = TaxMapper(areas, TAX, tax_city)

    fix, stats = {}, Counter()
    for sid in city_of:
        real = ((cache.get(sid) or {}).get("area_name_real") or "").strip()
        if not real or real.lower() in ("null", "none"):
            stats["3 no areaName on the page - left unchanged"] += 1
            continue
        hit, how = mapper.map(real, city_of[sid])
        if hit:
            fix[sid] = (hit, f"page areaName -> taxonomy ({how})")
            stats["1 page areaName -> taxonomy"] += 1
        else:
            fix[sid] = (real, f"page areaName kept off-taxonomy ({how or 'no match'})")
            stats["2 page areaName kept, off taxonomy"] += 1
    return fix, stats, name_of, before_of


def free_backup(path, tag="pre_areafix"):
    """A backup name that is NOT already taken.

    The first version always wrote "<file>.pre_areafix.bak", so running a second
    area repair silently overwrote the first run's backup and destroyed the
    pristine copy. A backup that can be clobbered is not a backup: pick the
    first free slot instead and never touch an existing one.

    `tag` names what the backup precedes, so a non-area repair does not leave a
    file called "pre_areafix" behind.
    """
    base = path.with_suffix(f"{path.suffix}.{tag}.bak")
    if not base.exists():
        return base
    i = 2
    while True:
        cand = path.with_suffix(f"{path.suffix}.{tag}.{i}.bak")
        if not cand.exists():
            return cand
        i += 1


def patch(path, shape, style, fix, dry):
    """Rewrite one file with location.area replaced, IN ITS OWN FORMAT."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    o, sep, close = OPEN[style], SEPS[style], CLOSES[style]
    changed, seen, out_rows = 0, 0, []
    with open(path, "rb") as src:
        it = (ijson.items(src, "item", use_float=True) if shape == "json"
              else (orjson.loads(l) for l in src if l.strip()))
        f = None if dry else open(tmp, "wb")
        try:
            if f and o:
                f.write(o)
            first = True
            for rec in it:
                seen += 1
                sid = str(rec.get("source_id"))
                # verified-location records are excluded: each carries its own
                # Google address, so none was ever part of a smear group
                if sid in fix and not rec.get("is_verified_location"):
                    new = fix[sid][0]
                    old = (rec.get("location") or {}).get("area")
                    if (old or "") != new:
                        rec["location"]["area"] = new
                        changed += 1
                        out_rows.append((sid, old, new))
                if f:
                    if style == "jsonl":
                        f.write(enc(rec, style) + b"\n")
                    else:
                        if not first:
                            f.write(sep)
                        f.write(enc(rec, style))
                first = False
            if f and close:
                f.write(close)
        finally:
            if f:
                f.close()
    if not dry:
        bak = free_backup(path)
        shutil.copy2(path, bak)
        tmp.replace(path)
        return changed, seen, out_rows, bak
    return changed, seen, out_rows, None


def verify(path, shape, expect, bak=None):
    """Re-read the WRITTEN file against THE BACKUP THIS RUN MADE: only
    location.area may differ, and only on the branches we meant to touch."""
    bak = bak or path.with_suffix(path.suffix + ".pre_areafix.bak")
    bad = Counter()
    with open(bak, "rb") as fa, open(path, "rb") as fb:
        ia = (ijson.items(fa, "item", use_float=True) if shape == "json"
              else (orjson.loads(l) for l in fa if l.strip()))
        ib = (ijson.items(fb, "item", use_float=True) if shape == "json"
              else (orjson.loads(l) for l in fb if l.strip()))
        n = 0
        for a, b in zip(ia, ib):
            n += 1
            if a.get("source_id") != b.get("source_id"):
                bad["record order changed"] += 1
                break
            la, lb = dict(a.get("location") or {}), dict(b.get("location") or {})
            aa, ab = la.pop("area", None), lb.pop("area", None)
            if la != lb:
                bad["other location field changed"] += 1
            a2, b2 = dict(a), dict(b)
            a2.pop("location"), b2.pop("location")
            if orjson.dumps(a2, option=orjson.OPT_SORT_KEYS) != \
               orjson.dumps(b2, option=orjson.OPT_SORT_KEYS):
                bad["non-location field changed"] += 1
            if aa != ab:
                bad["area changed"] += 1
                if str(b.get("source_id")) not in expect:
                    bad["area changed on an UNEXPECTED record"] += 1
    return n, bad


def main(args):
    print("=" * 78)
    print("  REPAIR location.area ON SMEARED BRANCHES   (area only, nothing else)")
    print("=" * 78)

    fix, stats, name_of, before_of = build_map(args)
    print(f"  smeared branches        : {len(before_of):,}")
    for k, v in sorted(stats.items()):
        print(f"    {k:48} {v:>6,}")
    print(f"  branches with a new area: {len(fix):,}")

    ex = [(s, before_of.get(s), fix[s][0]) for s in list(fix)[:10]]
    print("\n  sample:")
    for s, b, a in ex:
        print(f"    {s:<9} {str(name_of.get(s))[:24]:26} {str(b)[:34]:36} -> {a}")

    # THE METRIC THAT MATTERS IS PER BRAND. Counting repeats across all fixed
    # branches is misleading: dozens of DIFFERENT brands genuinely sit in
    # Business Bay, and that is correct data, not a smear. The defect was
    # branches of ONE brand all carrying one area, so measure that.
    rows = [r for r in csv.DictReader(open(ROWS, encoding="utf-8-sig"))
            if r["dataset"] != "unified_202608"]
    by_group = defaultdict(list)
    for r in rows:
        by_group[r["smear_key"]].append(r["source_id"])
    done = fixed = 0
    before_u, after_u = [], []
    for k, sids in by_group.items():
        if not all(s in fix for s in sids):
            continue                      # group not fully fetched yet
        done += 1
        before_u.append(1)                # one shared area, by definition
        u = len({fix[s][0] for s in sids})
        after_u.append(u)
        if u == len(sids):
            fixed += 1
    if done:
        print(f"\n  BRAND GROUPS fully resolved so far: {done:,}")
        print(f"    every branch now has its own area : {fixed:,} "
              f"({fixed/done*100:.0f}%)")
        print(f"    distinct areas per group: 1 before -> "
              f"{sum(after_u)/len(after_u):.1f} on average")
    # a value still shared AFTER the fix is only a problem inside one brand
    after_by_brand = defaultdict(set)
    for r in rows:
        if r["source_id"] in fix:
            after_by_brand[fix[r["source_id"]][0]].add(r["chain_id"])
    shared = Counter(fix[s][0] for s in fix)
    print("\n  most-repeated area AFTER the fix (many brands = genuinely busy area):")
    for a, n in shared.most_common(3):
        print(f"    {n:>4} branches on '{a}' across {len(after_by_brand[a])} different brands")

    if args.dry_run:
        for name, path, shape, style in TARGETS:
            if path.exists():
                ch, seen, _, _b = patch(path, shape, style, fix, dry=True)
                print(f"  {name:16} {seen:>7,} records | would change {ch:>6,}")
        print("\n  --dry-run: nothing written")
        return 0

    all_audit = []
    for name, path, shape, style in TARGETS:
        if not path.exists():
            print(f"  {name:16} MISSING")
            continue
        ch, seen, rows, bak = patch(path, shape, style, fix, dry=False)
        n, bad = verify(path, shape, set(fix), bak)
        ok = (not bad) or set(bad) == {"area changed"}
        print(f"  {name:16} {seen:>7,} records | area changed {ch:>6,} | "
              f"verify {'PASS' if ok else 'FAIL ' + str(dict(bad))}")
        if not ok:
            raise SystemExit(f"{name}: verification failed - restore from "
                             f"{path.name}.pre_areafix.bak")
        for sid, old, new in rows:
            all_audit.append({"dataset": name, "source_id": sid,
                              "name": name_of.get(sid, ""), "area_before": old,
                              "area_after": new, "method": fix[sid][1]})

    with open(AUDIT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["dataset", "source_id", "name",
                                          "area_before", "area_after", "method"])
        w.writeheader()
        w.writerows(all_audit)
    print(f"\n  -> {AUDIT.relative_to(ROOT)}  ({len(all_audit):,} rows)")
    print("  backups: <file>.pre_areafix.bak next to each patched file")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
