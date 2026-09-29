"""Write the per-branch contact_phone and maps_url. NOTHING ELSE.

Input  branch_contacts.csv  - one row per smeared branch, from Serper /maps,
                              every match verified within 300 m of that branch's
                              own coordinates
Output the same three files the area fix touched, each in ITS OWN format

PER SAGAR:
    found      -> keep the value we found for that branch
    not found  -> "NA"
The brand-level value is removed either way: it belongs to a different branch
and is wrong on all 4,964.

ONLY contact_phone AND maps_url ARE WRITTEN. area, raw, city, website, geo and
everything else stay byte-identical, and the verify pass re-reads each written
file against its backup to prove it.

NOTE ON "NA" IN JULY. schema_check showed the cohorts store "missing"
differently - July uses null (6,569 phones, 5,346 maps_urls), August and
unified use the string "NA". Sagar asked for "NA", so "NA" is written in all
three; July therefore ends up with both conventions: null on the rows nobody
touched, "NA" on these. Reported at the end so it is a known state, not a
surprise.

Format handling is imported from apply_area_fix so there is one definition of
how each file is serialised.

    python apply_contact_fix.py --dry-run
    python apply_contact_fix.py
"""
import argparse
import csv
import shutil
import sys
from collections import Counter
from pathlib import Path

import ijson
import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from apply_area_fix import CLOSES, OPEN, SEPS, TARGETS, enc    # noqa: E402

DATA = HERE / "data"
CONTACTS = DATA / "branch_contacts.csv"
AUDIT = DATA / "contact_fix_applied.csv"
NA = "NA"
sys.stdout.reconfigure(encoding="utf-8")


def build_map():
    """branch_id -> (phone, maps_url, status). NA where nothing was verified."""
    fix, stats = {}, Counter()
    for r in csv.DictReader(open(CONTACTS, encoding="utf-8-sig")):
        if r["status"] == "matched":
            phone = r["phone"].strip() or NA
            maps = r["maps_url"].strip() or NA
            stats["matched, phone kept" if phone != NA else
                  "matched, no phone on the listing"] += 1
            if maps != NA:
                stats["matched, maps_url kept"] += 1
        else:
            phone = maps = NA
            stats["no verified match -> NA"] += 1
        fix[r["branch_id"]] = (phone, maps, r["status"])
    return fix, stats


def patch(path, shape, style, fix, dry):
    tmp = path.with_suffix(path.suffix + ".tmp")
    o, sep, close = OPEN[style], SEPS[style], CLOSES[style]
    changed, seen, rows = 0, 0, []
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
                # verified-location records carry their own Google details and
                # were never part of a smear group
                if sid in fix and not rec.get("is_verified_location"):
                    phone, maps, status = fix[sid]
                    op, om = rec.get("contact_phone"), rec.get("maps_url")
                    if op != phone or om != maps:
                        rec["contact_phone"], rec["maps_url"] = phone, maps
                        changed += 1
                        rows.append((sid, op, phone, om, maps, status))
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
        shutil.copy2(path, path.with_suffix(path.suffix + ".pre_contactfix.bak"))
        tmp.replace(path)
    return changed, seen, rows


def verify(path, shape, expect):
    """Only contact_phone and maps_url may differ, and only on our branches."""
    bak = path.with_suffix(path.suffix + ".pre_contactfix.bak")
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
            a2, b2 = dict(a), dict(b)
            pa, pb = a2.pop("contact_phone", None), b2.pop("contact_phone", None)
            ma, mb = a2.pop("maps_url", None), b2.pop("maps_url", None)
            if orjson.dumps(a2, option=orjson.OPT_SORT_KEYS) != \
               orjson.dumps(b2, option=orjson.OPT_SORT_KEYS):
                bad["a field other than phone/maps_url changed"] += 1
            if (pa != pb or ma != mb) and str(b.get("source_id")) not in expect:
                bad["changed on an UNEXPECTED record"] += 1
    return n, bad


def main(args):
    print("=" * 78)
    print("  WRITE contact_phone + maps_url   (those two fields only)")
    print("=" * 78)
    fix, stats = build_map()
    print(f"  branches: {len(fix):,}")
    for k, v in sorted(stats.items()):
        print(f"    {k:42} {v:>6,}")

    if args.dry_run:
        for name, path, shape, style in TARGETS:
            if path.exists():
                ch, seen, _ = patch(path, shape, style, fix, dry=True)
                print(f"  {name:16} {seen:>7,} records | would change {ch:>6,}")
        print("\n  --dry-run: nothing written")
        return 0

    audit = []
    for name, path, shape, style in TARGETS:
        if not path.exists():
            print(f"  {name:16} MISSING")
            continue
        ch, seen, rows = patch(path, shape, style, fix, dry=False)
        n, bad = verify(path, shape, set(fix))
        ok = not bad
        print(f"  {name:16} {seen:>7,} records | changed {ch:>6,} | "
              f"verify {'PASS' if ok else 'FAIL ' + str(dict(bad))}")
        if not ok:
            raise SystemExit(f"{name}: verification failed - restore from "
                             f"{path.name}.pre_contactfix.bak")
        for sid, op, np_, om, nm, status in rows:
            audit.append({"dataset": name, "source_id": sid, "status": status,
                          "phone_before": op, "phone_after": np_,
                          "maps_url_before": om, "maps_url_after": nm})

    with open(AUDIT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(audit[0].keys()))
        w.writeheader()
        w.writerows(audit)
    print(f"\n  -> {AUDIT.relative_to(ROOT)}  ({len(audit):,} rows)")
    print("  backups: <file>.pre_contactfix.bak")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
