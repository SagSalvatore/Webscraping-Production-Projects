"""Compile every cohort into ONE JSONL for Tech, in the existing schema.

WHAT GOES IN
  July   talabat_export.json      17,187 records, menus REPLACED by the refresh
  August August_export.json        8,268 records, as built
                                  --------
                                  25,455 records | ~2,094,513 menu items

WHY JSONL. One self-contained JSON object per line: Tech can stream it, split
it, or load it line by line without holding 700 MB of parsed objects. Written
with orjson, which is both faster than json and compact by default.

DETERMINISTIC BY DESIGN. Every judgement call was made upstream and frozen into
a sidecar (`fill_july_cities.py`, `fix_arabic_names.py`), so this script makes
no API calls, fits no model, and produces byte-identical output for identical
inputs. That is what makes a monthly deliverable diffable against last month.

THE FIVE DECISIONS IT APPLIES  (see docs/UNIFIED_DELIVERABLE_PLAN.md)
  D1  contact_phone / website / maps_url: null -> "NA", so one convention spans
      both cohorts. July's null meant "never enriched", August's "NA" meant
      "looked, found nothing" - the report says so rather than pretending the
      two were ever the same.
  D2  location.city filled from the coordinate kNN sidecar (98.80% held out).
  D3  null item names / sections restored from the translation sidecar.
  D4  location records take their PARENT's refreshed menu. They reuse the
      parent's source_id and chain_id, so one chain must not show two prices
      for one item.
  D5  source_id 710110 is in the refresh but in no export - nothing to attach
      its 2 items to, so it is reported, not invented.
  D6  no vintage field: in the July cohort the metadata is July's and the menus
      are August's, so one field could not honestly describe both.

FIELD ORDER IS PRESERVED by mutating the record ijson yields rather than
rebuilding it, so orjson emits keys in the source order and a diff against last
month shows real changes only.

    python build_unified_export.py --dry-run
    python build_unified_export.py
    python build_unified_export.py --gzip
"""
import argparse
import gzip
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import ijson
import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "export"))
sys.path.insert(0, str(ROOT / "menu"))
sys.path.insert(0, str(ROOT / "menu_refresh"))
from build_july_update import scrub                      # noqa: E402

# Restaurant NAMES and ADDRESSES were never scrubbed - only menu text was, in
# both cohorts. So the merged file carried 640 Arabic, 162 mojibake, 13 CJK,
# 12 zero-width and 1 emoji in record-level fields. Pre-existing in both
# exports, invisible until this validator looked at fields the older gates did
# not. Same scrub() the menu text went through, so one rule covers the file.
TEXT_FIELDS = ("name",)
LOCATION_FIELDS = ("raw", "city", "area", "sublocality")

# scrub()'s invisible-character class ends at U+FEFF and so misses the
# variation selectors U+FE00-FE0F. One of them sits inside "Chai <FE0F>mahal",
# invisible on screen but caught by the emoji check. Stripped here rather than
# widened inside scrub(), which the shipped July file already depends on.
VARSEL = re.compile("[︀-️]")


def clean_text(v):
    """scrub() plus the variation selectors it does not cover."""
    s = scrub(VARSEL.sub("", v))
    return s.strip() if isinstance(s, str) else s
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

MONTH = "202608"
OUT = DATA / f"talabat_unified_{MONTH}.jsonl"
REPORT = DATA / f"unified_build_report_{MONTH}.json"

CITY_FIXES = DATA / "july_city_fixes.json"
ARABIC_FIXES = DATA / "arabic_fixes.json"
LABEL_FIXES = DATA / "label_conflict_fixes.json"

# Adding September is one entry here. `refresh` is the file whose menus replace
# the export's; None means the export already carries current menus.
COHORTS = [
    {"name": "july",
     "export": ROOT / "export" / "talabat_export.json",
     "refresh": ROOT / "menu_refresh" / "data" / "July_menu_update.json",
     "city_fixes": True, "arabic_fixes": True},
    {"name": "august",
     "export": ROOT / "August_menu" / "data" / "August_export.json",
     "refresh": None,
     "city_fixes": False, "arabic_fixes": False},
]

# D1: normalise ONLY these. chain_type stays null - "not a chain" is a real
# value, not a missing one.
SOFT_FIELDS = ("contact_phone", "website", "maps_url")
NA = "NA"

RECORD_FIELDS = {
    "source_name", "source_id", "name", "cuisine", "sub_cuisines",
    "key_cuisines", "restaurant_type", "outlet_type", "chain_type", "chain_id",
    "chain_locations_count", "currency", "location", "geo", "contact_phone",
    "website", "maps_url", "menu_items", "is_verified_location",
}
ITEM_FIELDS = {"name", "section", "description", "std_term", "price",
               "ingredients", "is_popular"}

sys.stdout.reconfigure(encoding="utf-8")


def record_key(rec):
    """Must match fill_july_cities.record_key exactly."""
    loc = rec.get("location") or {}
    return (f"{rec.get('source_id')}|{loc.get('raw') or ''}"
            f"|{int(bool(rec.get('is_verified_location')))}")


def load_sidecar(path, label, required):
    if not path.exists():
        if required:
            print(f"  ABORT: {label} sidecar missing: {path.name}")
            print("         run its generator first, or pass --skip-fixes")
            sys.exit(2)
        return {}
    d = json.loads(path.read_text(encoding="utf-8"))
    return d.get("fixes", {})


def index_refresh(path):
    """source_id -> menu_items. The single largest structure in the run."""
    idx = {}
    n = 0
    for rec in ijson.items(open(path, "rb"), "item", use_float=True):
        idx[str(rec["source_id"])] = rec.get("menu_items") or []
        n += 1
        if n % 5000 == 0:
            print(f"      {n:,} ...", flush=True)
    return idx


def main(args):
    t_all = time.time()
    print("=" * 76)
    print(f"  BUILD UNIFIED EXPORT  {OUT.name}")
    print("=" * 76)

    city_fixes = {} if args.skip_fixes else load_sidecar(
        CITY_FIXES, "city", required=not args.dry_run)
    arabic_fixes = {} if args.skip_fixes else load_sidecar(
        ARABIC_FIXES, "arabic", required=not args.dry_run)
    # Label fixes are OPTIONAL: the first build had to exist before the
    # conflicts could be measured from it. Once present they always apply.
    label_fixes = {} if args.skip_fixes else load_sidecar(
        LABEL_FIXES, "label", required=False)
    print(f"  sidecars: city {len(city_fixes):,} | arabic "
          f"{len(arabic_fixes):,} | label {len(label_fixes):,}")
    if not label_fixes:
        print("  NOTE: no label sidecar - cross-cohort std_term conflicts will "
              "remain. Run resolve_label_conflicts.py first.")

    st = Counter()
    per_cohort = {}
    unused_refresh = {}
    tmp = OUT.with_suffix(".tmp")
    out_f = None if args.dry_run else open(tmp, "wb")

    try:
        for coh in COHORTS:
            name = coh["name"]
            print(f"\n  --- {name.upper()} ---")
            refresh = {}
            if coh["refresh"]:
                print(f"    indexing refresh {coh['refresh'].name} ...")
                refresh = index_refresh(coh["refresh"])
                print(f"    {len(refresh):,} restaurants indexed")
            used = set()

            c = Counter()
            for rec in ijson.items(open(coh["export"], "rb"), "item",
                                   use_float=True):
                c["records"] += 1
                sid = str(rec.get("source_id"))

                # --- schema guard: fail loudly on an unexpected field -------
                extra = set(rec.keys()) - RECORD_FIELDS
                if extra:
                    raise SystemExit(
                        f"unexpected record field(s) {extra} on {sid}")

                # --- D4: menus from the refresh, parents and locations alike
                if refresh:
                    mi = refresh.get(sid)
                    if mi is not None:
                        rec["menu_items"] = mi
                        used.add(sid)
                        c["menu_replaced"] += 1
                        if rec.get("is_verified_location"):
                            c["location_menu_from_parent"] += 1
                    else:
                        c["menu_kept_original"] += 1

                # --- D1: one missing-value convention ----------------------
                for f in SOFT_FIELDS:
                    if rec.get(f) is None:
                        rec[f] = NA
                        c[f"na_{f}"] += 1

                # The city sidecar is keyed on the RAW address, so the key must
                # be taken before scrubbing rewrites it - scrubbing first cost
                # 9 of the 4,687 city fixes their match.
                rkey = record_key(rec)

                # --- record-level text -------------------------------------
                # A name that scrubs to nothing keeps its original: for the 13
                # CJK outlets ('Wemart 温超') the non-Latin part may be the only
                # name there is, and an empty name is worse than an unclean one.
                for f in TEXT_FIELDS:
                    v = rec.get(f)
                    if isinstance(v, str) and v:
                        s = clean_text(v)
                        if s and s != v:
                            rec[f] = s
                            c[f"scrubbed_{f}"] += 1
                        elif not s:
                            c["scrub_would_empty_kept_original"] += 1
                loc = rec.get("location")
                if isinstance(loc, dict):
                    for f in LOCATION_FIELDS:
                        v = loc.get(f)
                        if isinstance(v, str) and v:
                            s = clean_text(v)
                            if s and s != v:
                                loc[f] = s
                                c["scrubbed_location"] += 1
                            elif not s:
                                loc[f] = None
                                c["location_emptied_to_null"] += 1

                # --- D2: city from the coordinate kNN ----------------------
                if coh["city_fixes"]:
                    fix = city_fixes.get(rkey)
                    if fix and not (rec.get("location") or {}).get("city"):
                        rec["location"]["city"] = fix
                        c["city_filled"] += 1

                # --- D3: restore names/sections scrub() emptied ------------
                items = rec.get("menu_items") or []
                if coh["arabic_fixes"] and arabic_fixes:
                    for i, it in enumerate(items):
                        fx = arabic_fixes.get(f"{sid}:{i}")
                        if not fx:
                            continue
                        for field, val in fx.items():
                            if it.get(field) is None:
                                it[field] = val
                                c[f"restored_{field}"] += 1

                # --- residual nulls: the agreed D3 fallback ----------------
                # ~40 of the 315 holes cannot be recovered - either several
                # different Arabic strings fit the item equally well, or no
                # scrape row matches it at all. Guessing would be worse than a
                # rule, so: an item with no NAME is unusable to Tech and is
                # dropped; a missing SECTION is only a grouping label and takes
                # the same "NA" the soft fields use. Both are counted, so a
                # silent drop is impossible.
                kept = []
                for it in items:
                    if set(it.keys()) - ITEM_FIELDS:
                        raise SystemExit(
                            f"unexpected item field on {sid}: "
                            f"{set(it.keys()) - ITEM_FIELDS}")
                    if it.get("name") is None:
                        c["item_dropped_no_name"] += 1
                        continue
                    if it.get("section") is None:
                        it["section"] = NA
                        c["item_section_na"] += 1
                    # --- one std_term + one ingredient list per name --------
                    lf = label_fixes.get(it["name"])
                    if lf:
                        if it.get("std_term") != lf["std_term"]:
                            c["std_term_harmonised"] += 1
                        if (it.get("ingredients") or []) != lf["ingredients"]:
                            c["ingredients_harmonised"] += 1
                        it["std_term"] = lf["std_term"]
                        it["ingredients"] = list(lf["ingredients"])
                    kept.append(it)
                    c["items"] += 1
                if len(kept) != len(items):
                    rec["menu_items"] = kept
                    items = kept
                if not items:
                    c["EMPTY_MENU"] += 1

                if out_f:
                    out_f.write(orjson.dumps(rec))
                    out_f.write(b"\n")
                if c["records"] % 5000 == 0:
                    print(f"      {c['records']:,} records | "
                          f"{c['items']:,} items", flush=True)

            if refresh:
                unused_refresh[name] = sorted(set(refresh) - used)
            per_cohort[name] = dict(c)
            st.update(c)
            print(f"    {c['records']:,} records | {c['items']:,} items")
            for k in ("menu_replaced", "location_menu_from_parent",
                      "menu_kept_original", "city_filled",
                      "restored_name", "restored_section"):
                if c[k]:
                    print(f"      {k:28} {c[k]:>9,}")
    finally:
        if out_f:
            out_f.close()

    print("\n" + "=" * 76)
    print(f"  records {st['records']:,} | menu items {st['items']:,}")
    for f in SOFT_FIELDS:
        print(f"    {f:16} -> 'NA' on {st[f'na_{f}']:>7,}")
    print(f"    city filled          {st['city_filled']:>7,}")
    print(f"    names restored       {st['restored_name']:>7,}")
    print(f"    sections restored    {st['restored_section']:>7,}")

    # --- D5 + integrity ---------------------------------------------------
    fails = {}
    for name, orphans in unused_refresh.items():
        if orphans:
            print(f"\n  {name}: {len(orphans):,} refresh source_id(s) with NO "
                  f"export record -> NOT in the output: {orphans[:5]}")
    if st["EMPTY_MENU"]:
        fails["records_with_empty_menu"] = st["EMPTY_MENU"]
    print(f"    items dropped (no name){st['item_dropped_no_name']:>10,}")
    print(f"    sections set to 'NA'   {st['item_section_na']:>10,}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        if fails:
            print(f"  WOULD FAIL: {fails}")
        return 0

    if fails:
        tmp.unlink(missing_ok=True)
        print(f"\n  BUILD REJECTED, .tmp discarded: {fails}")
        return 1

    tmp.replace(OUT)
    size = OUT.stat().st_size
    print(f"\n  -> {OUT.name}  ({size/1e6:.0f} MB)")

    if args.gzip:
        gz = OUT.with_suffix(".jsonl.gz")
        t0 = time.time()
        with open(OUT, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
            while chunk := src.read(1 << 22):
                dst.write(chunk)
        print(f"  -> {gz.name}  ({gz.stat().st_size/1e6:.0f} MB, "
              f"{size/gz.stat().st_size:.1f}x smaller, {time.time()-t0:.0f}s)")

    REPORT.write_text(json.dumps({
        "month": MONTH,
        "output": OUT.name,
        "bytes": size,
        "records": st["records"],
        "menu_items": st["items"],
        "per_cohort": per_cohort,
        "orphan_refresh_ids": unused_refresh,
        "decisions": {
            "D1": "contact_phone/website/maps_url null -> 'NA'. NOTE: July's "
                  "null meant NEVER ENRICHED (maps_url null on 35.2% of main "
                  "records vs 0.0% of google-sourced location records); "
                  "August's 'NA' meant looked-and-absent. The unified 'NA' "
                  "does NOT assert a lookup was performed for July.",
            "D2": "location.city filled by coordinate kNN k=3, 98.80% held-out, "
                  "MAX_KM=15 guard",
            "D3": "null item name/section restored by translating the original "
                  "Arabic that scrub() emptied",
            "D4": "location records take the parent's refreshed menu "
                  "(shared source_id and chain_id)",
            "D5": "refresh source_ids with no export record are excluded",
            "D6": "no vintage field - July metadata and August menus coexist",
        },
    }, indent=2), encoding="utf-8")
    print(f"  -> {REPORT.name}")
    print(f"  elapsed {(time.time()-t_all)/60:.1f} min")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--gzip", action="store_true")
    p.add_argument("--skip-fixes", action="store_true",
                   help="build without the sidecars (diagnostic only)")
    sys.exit(main(p.parse_args()))
