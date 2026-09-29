"""Last two defects before delivery.

1. SAME OUTLET, TWICE. 15 groups where a base record and a verified-location
   record share BOTH source_id and coordinates to full float precision - the
   same physical outlet. finalize_export.py missed them because its key included
   the normalised address, and the two rows carry different address TEXT: the
   base takes Google location[0]'s address while the verified record carries its
   own. Same point, same branch, so the base wins - it is the row Tech joins on
   and it holds Talabat's own geometry.

   One further group shares a coordinate across two DIFFERENT source_ids; that
   is two branches, not a duplicate, and is left alone.

2. THE LAST 4 ARABIC STRINGS. The translator correctly returned null for these
   two - they are not location text and not ingredients:
     'يقدم مع سلطة'          "served with salad" - a serving note sitting in an
                             ingredients array, alongside a stray '/'
     'للحلقة التانية بنات'    meaningless in a sublocality field
   Removing the note leaves 11 real ingredients on that item, and the
   sublocality is dropped to null with `area` already populated, so neither
   fix loses information.

    python final_cleanup_pass.py --dry-run
    python final_cleanup_pass.py
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPORT = HERE / "data" / "August_export.json"

ARABIC = re.compile("[؀-ۿﭐ-﷽ﹰ-﻿]")

sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    d = json.loads(EXPORT.read_text(encoding="utf-8"))
    n0 = len(d)
    base0 = sum(1 for r in d if not r.get("is_verified_location"))
    print("=" * 70)
    print(f"  FINAL CLEANUP  {n0:,} records")
    print("=" * 70)

    # ---- 1. same source_id + same coordinate -------------------------
    pos = defaultdict(list)
    for i, r in enumerate(d):
        g = r.get("geo") or {}
        pos[(r.get("source_id"), g.get("lat"), g.get("lng"))].append(i)

    drop = set()
    for k, idxs in pos.items():
        if len(idxs) < 2:
            continue
        bases = [i for i in idxs if not d[i].get("is_verified_location")]
        if bases:
            for i in idxs:
                if i != bases[0]:
                    drop.add(i)
        else:
            for i in idxs[1:]:
                drop.add(i)
    print(f"\n  duplicate (source_id, coordinates) records dropped: {len(drop):,}")

    out = [r for i, r in enumerate(d) if i not in drop]

    # ---- 2. residual Arabic ------------------------------------------
    ing_fixed = sub_fixed = 0
    for r in out:
        L = r.get("location") or {}
        v = L.get("sublocality")
        if isinstance(v, str) and ARABIC.search(v):
            # `area` carries the usable value; a meaningless sublocality is
            # better absent than present in a foreign script
            L["sublocality"] = None
            sub_fixed += 1
        for it in r.get("menu_items") or []:
            ings = it.get("ingredients") or []
            keep = [g for g in ings
                    if isinstance(g, str) and g.strip() and len(g.strip()) > 1
                    and not ARABIC.search(g)]
            if keep != ings:
                # never strip an item down to nothing
                if keep:
                    it["ingredients"] = keep
                    ing_fixed += 1
            for k in ("name", "section", "description", "std_term"):
                s = it.get(k)
                if isinstance(s, str) and ARABIC.search(s):
                    cleaned = re.sub(r"\s+", " ", ARABIC.sub(" ", s)).strip(" -,")
                    if cleaned:
                        it[k] = cleaned

    print(f"  ingredient arrays cleaned : {ing_fixed:,}")
    print(f"  sublocality nulled        : {sub_fixed:,}")

    base1 = sum(1 for r in out if not r.get("is_verified_location"))
    print(f"\n  records      {n0:,} -> {len(out):,}")
    print(f"  base records {base0:,} -> {base1:,}"
          + ("  <-- MUST NOT CHANGE" if base1 != base0 else "  (unchanged)"))
    assert base1 == base0, "base records lost - aborting"

    left = sum(1 for r in out for f in ("raw", "city", "area", "sublocality")
               if isinstance((r.get("location") or {}).get(f), str)
               and ARABIC.search(r["location"][f]))
    left += sum(1 for r in out for it in r.get("menu_items") or []
                for g in (it.get("ingredients") or [])
                if isinstance(g, str) and ARABIC.search(g))
    print(f"  Arabic strings remaining  : {left}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    tmp = EXPORT.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    tmp.replace(EXPORT)
    print(f"\n  -> {EXPORT.name} ({EXPORT.stat().st_size/1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
