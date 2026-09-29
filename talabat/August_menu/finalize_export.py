"""Final pass on August_export.json: deduplicate, then normalise every shipped
string. Run after build_august_export.py + fill_cities.py.

DEDUPLICATION. The unit that must be unique is (brand, physical location) -
Sagar's requirement that a restaurant and its location never appear twice.
Measured on the pre-clean file, 369 keys repeated:

  357  a BASE record and a VERIFIED-LOCATION record for the same outlet.
       The Google listing that produced the verified record IS the base
       record's own outlet - identical coordinates, address, phone and menu.
       The base record wins: it carries Talabat's own geometry and is the row
       Tech joins on.
   11  two verified records at one coordinate - Google returned a place twice.
    1  a base plus two verified.

source_id is deliberately NOT the dedup key. July's format repeats the
representative branch's source_id on each of its verified location records
(17,187 entries over 15,198 ids), and August follows it. Deduping on source_id
would delete every genuine extra location.

TEXT NORMALISATION, in the order the cleaning skill prescribes:
  1. ftfy   - repairs real mojibake ("chefâ€TMs sauce" -> "chef's sauce") and
              folds typographic quotes to ASCII (2,310 curly apostrophes)
  2. invisible/zero-width and bidi marks removed
  3. Arabic stripped from bilingual strings, keeping the English side - these
     are google addresses like "Unnamed Road - اليَحَر - Al Rifaa - Abu Dhabi"
     where the English is complete on its own
  4. CJK stripped from restaurant names ("Yi Er Shan 一二汕" -> "Yi Er Shan")
  5. whitespace and orphaned separators collapsed

A string is only replaced if the cleaned form is non-empty - a name must never
be blanked to satisfy a cleanliness check.

July ships curly quotes, Arabic and mojibake too, so this makes August cleaner
than the file Tech already has. That is a deliberate divergence, not drift.

    python finalize_export.py --dry-run
    python finalize_export.py
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPORT = HERE / "data" / "August_export.json"
REPORT = HERE / "data" / "August_export_finalize_report.json"

sys.stdout.reconfigure(encoding="utf-8")

try:
    from ftfy import fix_text
except ImportError:
    def fix_text(s):
        return s

ARABIC = re.compile("[؀-ۿﭐ-﷽ﹰ-﻿]+")
CJK = re.compile("[一-鿿㐀-䶿぀-ゟ゠-ヿ가-힯]+")
INVIS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f­​-‏‪-‮⁠-⁤﻿￹-￻]+")
WS = re.compile(r"\s+")
# a separator left stranded once a script block is removed: " - - ", " -,", ", ,"
ORPHAN = re.compile(r"(\s*[-,]\s*){2,}")
EDGE = re.compile(r"^[\s\-,]+|[\s\-,]+$")


def clean(s, strip_arabic=True, strip_cjk=False):
    if not isinstance(s, str) or not s:
        return s
    out = fix_text(unicodedata.normalize("NFKC", s))
    out = INVIS.sub("", out)
    if strip_arabic and ARABIC.search(out):
        out = ARABIC.sub(" ", out)
    if strip_cjk and CJK.search(out):
        out = CJK.sub(" ", out)
    out = ORPHAN.sub(" - ", out)
    out = EDGE.sub("", WS.sub(" ", out)).strip()
    # never blank a value to satisfy a cleanliness rule
    return out if out else s


def nk(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    return WS.sub(" ", re.sub(r"[^\w\s]", " ", s)).strip().lower()


def geo_key(r):
    g = r.get("geo") or {}
    la, lo = g.get("lat"), g.get("lng")
    return (round(la, 6) if la is not None else None,
            round(lo, 6) if lo is not None else None)


def main(args):
    d = json.loads(EXPORT.read_text(encoding="utf-8"))
    n0 = len(d)
    items0 = sum(len(r.get("menu_items") or []) for r in d)
    base0 = sum(1 for r in d if not r.get("is_verified_location"))
    print("=" * 72)
    print(f"  FINALIZE  {n0:,} records / {items0:,} menu entries")
    print("=" * 72)

    # ---------------- dedup ---------------------------------------------
    groups = defaultdict(list)
    for i, r in enumerate(d):
        groups[(r.get("chain_id"), geo_key(r),
                nk((r.get("location") or {}).get("raw")))].append(i)

    drop = set()
    stats = Counter()
    for k, idxs in groups.items():
        if len(idxs) < 2:
            continue
        bases = [i for i in idxs if not d[i].get("is_verified_location")]
        vers = [i for i in idxs if d[i].get("is_verified_location")]
        if bases:
            # the base record is the one Tech joins on - keep it, drop the
            # verified copies of the same physical outlet
            keep = bases[0]
            for i in idxs:
                if i != keep:
                    drop.add(i)
                    stats["verified_dupe_of_base" if i in vers
                           else "extra_base"] += 1
        else:
            for i in vers[1:]:
                drop.add(i)
                stats["verified_dupe_of_verified"] += 1

    print(f"\n  --- DEDUPLICATION ---")
    print(f"    duplicate (brand, coords, address) groups : {sum(1 for v in groups.values() if len(v) > 1):,}")
    for k, v in stats.most_common():
        print(f"    {k:34} {v:>6,}")
    print(f"    records to drop                           : {len(drop):,}")

    kept = [r for i, r in enumerate(d) if i not in drop]

    # any byte-identical survivors
    seen = set()
    out = []
    exact = 0
    for r in kept:
        sig = json.dumps(r, sort_keys=True, ensure_ascii=False)
        if sig in seen:
            exact += 1
            continue
        seen.add(sig)
        out.append(r)
    print(f"    byte-identical survivors removed          : {exact:,}")

    # ---------------- text ----------------------------------------------
    changed = Counter()

    def fix_field(obj, key, **kw):
        v = obj.get(key)
        c = clean(v, **kw)
        if c != v:
            obj[key] = c
            changed[key] += 1

    for r in out:
        fix_field(r, "name", strip_cjk=True)
        fix_field(r, "cuisine")
        if r.get("key_cuisines"):
            kc = [clean(x) for x in r["key_cuisines"]]
            if kc != r["key_cuisines"]:
                r["key_cuisines"] = kc
                changed["key_cuisines"] += 1
        L = r.get("location") or {}
        for k in ("raw", "city", "area", "sublocality"):
            fix_field(L, k)
        for it in r.get("menu_items") or []:
            for k in ("name", "section", "description", "std_term"):
                fix_field(it, k)
            if it.get("ingredients"):
                ing = [clean(x) for x in it["ingredients"]]
                if ing != it["ingredients"]:
                    it["ingredients"] = ing
                    changed["ingredients"] += 1

    print(f"\n  --- TEXT NORMALISATION ---")
    for k, v in changed.most_common():
        print(f"    {k:24} {v:>8,} values changed")

    base1 = sum(1 for r in out if not r.get("is_verified_location"))
    items1 = sum(len(r.get("menu_items") or []) for r in out)
    print(f"\n  --- RESULT ---")
    print(f"    records     {n0:,} -> {len(out):,}   ({n0-len(out):,} removed)")
    print(f"    base records {base0:,} -> {base1:,}"
          + ("   <-- MUST NOT CHANGE" if base1 != base0 else "   (unchanged)"))
    print(f"    menu entries {items0:,} -> {items1:,}")

    assert base1 == base0, "base restaurant records were dropped - aborting"
    assert len({r["source_id"] for r in out
                if not r.get("is_verified_location")}) == base1, \
        "base source_id no longer unique"

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    tmp = EXPORT.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    tmp.replace(EXPORT)          # atomic: an interrupted run cannot truncate
    REPORT.write_text(json.dumps({
        "records_before": n0, "records_after": len(out),
        "base_records": base1, "menu_entries": items1,
        "dedup": dict(stats), "byte_identical_removed": exact,
        "text_fields_changed": dict(changed),
    }, indent=2), encoding="utf-8")
    print(f"\n  -> {EXPORT.name} ({EXPORT.stat().st_size/1e6:.0f} MB)")
    print(f"  -> {REPORT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
