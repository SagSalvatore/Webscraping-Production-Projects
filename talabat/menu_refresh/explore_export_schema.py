"""Schema map of talabat_export.json - the contract Tech is already built against.

Full streaming pass (ijson, 388 MB, <1 GB RAM), so every coverage number here is
exact rather than sampled. Produces, for each field:
    presence, null count, python type(s), distinct-value count, sample values

Also verifies the chain_id contract directly: every branch sharing a brand must
carry the same chain_id, and chain_locations_count must equal the number of
branches on that chain.

    python explore_export_schema.py
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ijson

HERE = Path(__file__).resolve().parent
EXPORT = HERE.parent / "export" / "talabat_export.json"
OUT = HERE / "data" / "export_schema_map.json"

sys.stdout.reconfigure(encoding="utf-8")


class Field:
    __slots__ = ("present", "null", "types", "vals", "truthy")

    def __init__(self):
        self.present = 0
        self.null = 0
        self.types = Counter()
        self.vals = Counter()
        self.truthy = 0

    def add(self, v):
        self.present += 1
        if v is None:
            self.null += 1
            self.types["null"] += 1
            return
        if v == "" or v == [] or v == {}:
            self.types[f"empty {type(v).__name__}"] += 1
            return
        self.truthy += 1
        self.types[type(v).__name__] += 1
        if isinstance(v, (str, int, float, bool)) and len(self.vals) < 6000:
            self.vals[str(v)[:60]] += 1
        elif isinstance(v, list) and v and len(self.vals) < 6000:
            self.vals[f"[{len(v)} items] e.g. {str(v[0])[:40]}"] += 1


def main():
    rest = defaultdict(Field)
    loc = defaultdict(Field)
    geo = defaultdict(Field)
    item = defaultdict(Field)
    n_rest = n_item = 0
    chain_brands = defaultdict(set)     # chain_id -> {source_id}
    chain_names = defaultdict(set)      # chain_id -> {name}
    chain_count_field = {}              # chain_id -> chain_locations_count
    name_to_chain = defaultdict(set)    # name -> {chain_id}

    print(f"  streaming {EXPORT.name} ({EXPORT.stat().st_size/1e6:.0f} MB) ...")
    with open(EXPORT, "rb") as f:
        for r in ijson.items(f, "item", use_float=True):
            n_rest += 1
            for k, v in r.items():
                if k == "menu_items":
                    rest["menu_items"].add(v)
                    for it in v or []:
                        n_item += 1
                        for ik, iv in it.items():
                            item[ik].add(iv)
                elif k == "location" and isinstance(v, dict):
                    rest["location"].add(v)
                    for lk, lv in v.items():
                        loc[lk].add(lv)
                elif k == "geo" and isinstance(v, dict):
                    rest["geo"].add(v)
                    for gk, gv in v.items():
                        geo[gk].add(gv)
                else:
                    rest[k].add(v)

            cid = r.get("chain_id")
            if cid is not None:
                chain_brands[cid].add(r.get("source_id"))
                chain_names[cid].add(r.get("name"))
                chain_count_field[cid] = r.get("chain_locations_count")
                name_to_chain[r.get("name")].add(cid)
            if n_rest % 4000 == 0:
                print(f"    {n_rest:,} restaurants ...", flush=True)

    def dump(title, d, total):
        print(f"\n{'='*78}\n  {title}   (of {total:,})\n{'='*78}")
        for k, fl in sorted(d.items(), key=lambda x: -x[1].present):
            types = ", ".join(f"{t}" for t, _ in fl.types.most_common(3))
            pct = fl.truthy / total * 100 if total else 0
            print(f"  {k:24} present {fl.present:>9,}  non-empty {fl.truthy:>9,} "
                  f"({pct:5.1f}%)  [{types}]")
            if fl.vals:
                ex = "  |  ".join(f"{v}" for v, _ in fl.vals.most_common(3))
                print(f"      distinct~{len(fl.vals):<6} e.g. {ex[:120]}")

    dump("RESTAURANT LEVEL", rest, n_rest)
    dump("location{}", loc, n_rest)
    dump("geo{}", geo, n_rest)
    dump("menu_items[]", item, n_item)

    print(f"\n{'='*78}\n  CHAIN_ID CONTRACT\n{'='*78}")
    print(f"  restaurant entries      : {n_rest:,}")
    print(f"  menu item entries       : {n_item:,}")
    print(f"  distinct chain_id       : {len(chain_brands):,}")
    multi = {c: b for c, b in chain_brands.items() if len(b) > 1}
    print(f"  chains with >1 branch   : {len(multi):,}")
    singles = len(chain_brands) - len(multi)
    print(f"  single-branch chains    : {singles:,}  "
          "(independents still get an id, per instruction)")

    bad_name = {c: n for c, n in chain_names.items() if len(n) > 1}
    print(f"\n  chain_ids covering >1 distinct name: {len(bad_name):,}")
    for c, n in list(bad_name.items())[:5]:
        print(f"     {c} -> {sorted(n)[:4]}")
    split = {n: c for n, c in name_to_chain.items() if len(c) > 1}
    print(f"  names split across >1 chain_id     : {len(split):,}"
          "   <- must be 0; a brand must have ONE id")
    for n, c in list(split.items())[:5]:
        print(f"     {n!r} -> {sorted(c)}")

    mismatch = [(c, len(b), chain_count_field[c])
                for c, b in chain_brands.items()
                if chain_count_field.get(c) != len(b)]
    print(f"\n  chain_locations_count != actual branch count: {len(mismatch):,}")
    for c, actual, stated in mismatch[:5]:
        print(f"     {c}: actual {actual}, stated {stated}")

    top = sorted(chain_brands.items(), key=lambda x: -len(x[1]))[:8]
    print("\n  largest chains:")
    for c, b in top:
        print(f"     {c}  {len(b):>4} branches  {sorted(chain_names[c])[0][:38]}")

    OUT.write_text(json.dumps({
        "restaurant_entries": n_rest, "menu_item_entries": n_item,
        "restaurant_fields": {k: {"present": v.present, "non_empty": v.truthy,
                                  "types": dict(v.types)} for k, v in rest.items()},
        "location_fields": {k: {"present": v.present, "non_empty": v.truthy}
                            for k, v in loc.items()},
        "geo_fields": {k: {"present": v.present, "non_empty": v.truthy}
                       for k, v in geo.items()},
        "menu_item_fields": {k: {"present": v.present, "non_empty": v.truthy,
                                 "types": dict(v.types)} for k, v in item.items()},
        "distinct_chain_ids": len(chain_brands),
        "multi_branch_chains": len(multi),
        "names_split_across_chain_ids": len(split),
    }, indent=2), encoding="utf-8")
    print(f"\n  -> {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
