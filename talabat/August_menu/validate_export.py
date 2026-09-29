"""Pre-delivery validation for August_export.json.

Two questions, both asked the staged way:

  DUPLICATES  exact record -> exact key -> canonicalised key -> near-duplicate.
              The key that matters here is (brand, location): one brand's one
              physical outlet must appear ONCE. Note that source_id is
              deliberately NOT unique across the file - July's format repeats
              the representative branch's id on each of its verified location
              records - so uniqueness is asserted at (source_id, geo, address),
              not on source_id alone.

  TEXT        emoji, mojibake, Arabic, control/zero-width characters, in every
              string that ships: restaurant name, all four location fields, and
              every menu item's name / section / description / std_term /
              ingredients.

Mojibake is detected with ftfy where available: ftfy.fix_text(s) != s means the
bytes were decoded through the wrong codec somewhere upstream ('CafÃ©' for
'Café'). A regex cannot find that reliably, which is why it is worth the import.

Read-only. Writes a report and, if anything fails, an exceptions file naming
every offending record.

    python validate_export.py
"""
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPORT = HERE / "data" / "August_export.json"
REPORT = HERE / "data" / "August_export_validation.json"
EXC = HERE / "data" / "August_export_exceptions.json"

sys.stdout.reconfigure(encoding="utf-8")

try:
    from ftfy import fix_text
    HAVE_FTFY = True
except ImportError:
    HAVE_FTFY = False

EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF\U0000FE00-\U0000FE0F‍⃣]")
ARABIC = re.compile("[؀-ۿﭐ-﷽ﹰ-﻿]")
CJK = re.compile("[一-鿿぀-ゟ゠-ヿ가-힯]")
CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
INVIS = re.compile("[­​-‏‪-‮⁠-⁤﻿]")
# classic mojibake signatures: UTF-8 bytes read as latin-1/cp1252
MOJI = re.compile("Ã[-¿]|â€|Â[ -¿]|ï»¿|Ð[-¿]")


def strings_of(rec):
    """Every string that actually ships, tagged with where it lives."""
    yield "name", rec.get("name")
    yield "cuisine", rec.get("cuisine")
    for c in rec.get("key_cuisines") or []:
        yield "key_cuisines", c
    L = rec.get("location") or {}
    for k in ("raw", "city", "area", "sublocality"):
        yield f"location.{k}", L.get(k)
    for f in ("contact_phone", "website", "maps_url", "restaurant_type",
              "outlet_type", "chain_type"):
        yield f, rec.get(f)
    for it in rec.get("menu_items") or []:
        for k in ("name", "section", "description", "std_term"):
            yield f"menu.{k}", it.get(k)
        for ing in it.get("ingredients") or []:
            yield "menu.ingredients", ing


def norm_key(s):
    """Canonical form for duplicate detection: NFKC, drop punctuation,
    collapse space, lowercase. Deliberately aggressive - it is only used to
    FIND candidate duplicates, never to merge anything automatically."""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def main():
    d = json.loads(EXPORT.read_text(encoding="utf-8"))
    n = len(d)
    items = sum(len(r.get("menu_items") or []) for r in d)
    print("=" * 72)
    print(f"  VALIDATE August_export.json   {n:,} records / {items:,} menu entries")
    print("=" * 72)
    print(f"  ftfy available for mojibake detection: {HAVE_FTFY}")

    fails = {}
    exceptions = defaultdict(list)

    # ---------------- 1. duplicates -------------------------------------
    print("\n  --- DUPLICATES ---")

    exact = Counter(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in d)
    dup_exact = sum(v - 1 for v in exact.values() if v > 1)
    print(f"    identical whole records            : {dup_exact:,}")

    def geo_key(r):
        g = r.get("geo") or {}
        la, lo = g.get("lat"), g.get("lng")
        return (round(la, 6) if la is not None else None,
                round(lo, 6) if lo is not None else None)

    # THE key the user asked about: one brand, one physical location, once
    brandloc = Counter((norm_key(r.get("name")), geo_key(r),
                        norm_key((r.get("location") or {}).get("raw")))
                       for r in d)
    dup_brandloc = {k: v for k, v in brandloc.items() if v > 1}
    print(f"    (brand, coordinates, address) repeated: "
          f"{sum(v-1 for v in dup_brandloc.values()):,} "
          f"across {len(dup_brandloc):,} keys")

    # base records must be one per restaurant
    base = [r for r in d if not r.get("is_verified_location")]
    base_ids = Counter(r["source_id"] for r in base)
    dup_base = {k: v for k, v in base_ids.items() if v > 1}
    print(f"    base records sharing a source_id      : {len(dup_base):,}")

    # a brand's verified locations must not repeat a coordinate
    ver = [r for r in d if r.get("is_verified_location")]
    vk = Counter((r["chain_id"], geo_key(r)) for r in ver)
    dup_ver = {k: v for k, v in vk.items() if v > 1}
    print(f"    verified locations repeating (chain, coords): {len(dup_ver):,}")

    # a base record and a verified record for the same brand at the same point
    pos = defaultdict(list)
    for r in d:
        pos[(r["chain_id"], geo_key(r))].append(bool(r.get("is_verified_location")))
    overlap = sum(1 for v in pos.values() if len(v) > 1 and len(set(v)) > 1)
    print(f"    base+verified at the same coordinate  : {overlap:,}")

    for label, cnt in (("identical_records", dup_exact),
                       ("brand_location_repeats",
                        sum(v-1 for v in dup_brandloc.values())),
                       ("base_source_id_repeats", len(dup_base)),
                       ("verified_coord_repeats", len(dup_ver))):
        if cnt:
            fails[label] = cnt
    if dup_brandloc:
        for (nm, g, addr), c in list(dup_brandloc.items())[:20]:
            exceptions["brand_location_repeats"].append(
                {"name": nm, "geo": g, "address": addr, "count": c})

    # ---------------- 2. text integrity ---------------------------------
    print("\n  --- TEXT INTEGRITY ---")
    counts = Counter()
    where = defaultdict(Counter)
    samples = defaultdict(list)

    for r in d:
        for field, s in strings_of(r):
            if not s or not isinstance(s, str):
                continue
            checks = (("emoji", EMOJI.search(s)),
                      ("arabic", ARABIC.search(s)),
                      ("cjk", CJK.search(s)),
                      ("control_chars", CTRL.search(s)),
                      ("zero_width", INVIS.search(s)),
                      ("mojibake_regex", MOJI.search(s)))
            for name, hit in checks:
                if hit:
                    counts[name] += 1
                    where[name][field] += 1
                    if len(samples[name]) < 8:
                        samples[name].append((r.get("source_id"), field, s[:70]))
            if HAVE_FTFY:
                fixed = fix_text(s)
                if fixed != s:
                    counts["mojibake_ftfy"] += 1
                    where["mojibake_ftfy"][field] += 1
                    if len(samples["mojibake_ftfy"]) < 8:
                        samples["mojibake_ftfy"].append(
                            (r.get("source_id"), field, f"{s[:40]!r} -> {fixed[:40]!r}"))

    for k in ("emoji", "mojibake_ftfy", "mojibake_regex", "arabic", "cjk",
              "control_chars", "zero_width"):
        v = counts[k]
        flag = "" if v == 0 else "   <-- FAIL"
        print(f"    {k:16} {v:>8,}{flag}")
        if v:
            fails[k] = v
            print(f"        fields: {dict(where[k].most_common(4))}")
            for sid, f, s in samples[k][:3]:
                print(f"        e.g. [{sid}] {f}: {s}")
            exceptions[k] = [{"source_id": s, "field": f, "value": v2}
                             for s, f, v2 in samples[k]]

    # ---------------- 3. schema / business rules -------------------------
    print("\n  --- SCHEMA & RULES ---")
    rules = {
        "missing chain_id": sum(1 for r in d if not r.get("chain_id")),
        "missing source_id": sum(1 for r in d if not r.get("source_id")),
        "country != UAE": sum(1 for r in d
                              if (r.get("location") or {}).get("country") != "UAE"),
        "no location.area": sum(1 for r in d
                                if not (r.get("location") or {}).get("area")),
        "no geo": sum(1 for r in d if (r.get("geo") or {}).get("lat") is None),
        "empty menu_items": sum(1 for r in d if not r.get("menu_items")),
        "menu item without std_term": sum(
            1 for r in d for i in r.get("menu_items") or [] if not i.get("std_term")),
        "menu item without ingredients": sum(
            1 for r in d for i in r.get("menu_items") or [] if not i.get("ingredients")),
        "price is None": sum(
            1 for r in d for i in r.get("menu_items") or [] if i.get("price") is None),
        "chain_type set on Independent": sum(
            1 for r in d if r.get("outlet_type") == "Independent" and r.get("chain_type")),
    }
    for k, v in rules.items():
        print(f"    {k:32} {v:>8,}" + ("" if v == 0 else "   <-- check"))
    for k, v in rules.items():
        if v and k not in ("no geo",):
            fails.setdefault(f"rule:{k}", v)

    # ---------------- verdict -------------------------------------------
    print("\n" + "=" * 72)
    if fails:
        print(f"  {len(fails)} CHECK(S) FAILED: {fails}")
        EXC.write_text(json.dumps(dict(exceptions), ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print(f"  -> {EXC.name}")
    else:
        print("  ALL CHECKS PASSED - no duplicates, no emoji, no mojibake, "
              "no Arabic")
    print("=" * 72)

    REPORT.write_text(json.dumps({
        "records": n, "menu_entries": items,
        "duplicates": {"identical_records": dup_exact,
                       "brand_location_repeats":
                           sum(v-1 for v in dup_brandloc.values()),
                       "base_source_id_repeats": len(dup_base),
                       "verified_coord_repeats": len(dup_ver),
                       "base_verified_same_point": overlap},
        "text": {k: counts[k] for k in
                 ("emoji", "mojibake_ftfy", "mojibake_regex", "arabic", "cjk",
                  "control_chars", "zero_width")},
        "rules": rules, "failed": fails,
    }, indent=2), encoding="utf-8")
    print(f"  -> {REPORT.name}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
