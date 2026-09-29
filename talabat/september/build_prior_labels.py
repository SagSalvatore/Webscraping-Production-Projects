"""Cache every std_term + ingredients Tech already holds, for tier-0 reuse.

SOURCE IS THE UNIFIED DELIVERABLE, not the raw exports. Two reasons, both
Sagar's:

  1 CONSISTENCY. talabat_unified_202608.jsonl is the file Tech has. A label
    reused from it is a label Tech already sees, which is the whole point of
    the tier. Measured July -> August: 310,061 shared item_keys, 100% identical
    std_term AND ingredients, precisely because the mapper read the prior
    deliverable and applied it verbatim.

  2 IT IS ALREADY SANITIZED. The unified build applied "NA" normalisation,
    Arabic fixes and label-conflict resolution. September's rows have been
    through the same sanitize step, so the two sides are comparable. The raw
    exports are pre-sanitization and would join less cleanly.

BOTH FIELDS TRAVEL TOGETHER. std_term and ingredients are taken from the same
row. Taking the term from one source and ingredients from another is how an
item ends up described as something it is not.

MEMORY. The file is 620 MB of JSON *Lines* - one object per line - so it is read
line by line with orjson and only the label dict is retained. ijson is for a
single large NESTED document (as the .json exports are) and would give nothing
on JSONL; the shape decides the tool, not the size.

    python build_prior_labels.py
    python build_prior_labels.py --source <path>   # override
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

import orjson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UNIFIED = ROOT / "unified" / "data" / "talabat_unified_202608.jsonl"
OUT = HERE / "data" / "prior_item_labels.json"
sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    src = Path(args.source) if args.source else UNIFIED
    print("=" * 74)
    print("  BUILD PRIOR LABEL CACHE  (from what Tech holds)")
    print("=" * 74)
    print(f"  source : {src.name}  ({src.stat().st_size/1e6:.0f} MB)")

    labels, casing = {}, Counter()
    recs = rows = usable = no_term = 0
    with open(src, "rb") as f:
        for line in f:
            recs += 1
            rec = orjson.loads(line)
            for mi in (rec.get("menu_items") or []):
                rows += 1
                name = (mi.get("name") or "").strip()
                term = mi.get("std_term")
                if not name:
                    continue
                if not term:
                    no_term += 1
                    continue
                labels[name.lower()] = {
                    "std_term": term,
                    "taxonomy": mi.get("taxonomy") or "",
                    "ingredients": mi.get("ingredients") or [],
                }
                casing[term] += 1
                usable += 1

    print(f"  records {recs:,} | menu rows {rows:,} | usable {usable:,} | "
          f"no std_term {no_term:,}")

    # one canonical surface form per term, chosen by frequency. NEVER .title(),
    # which mangles real acronyms - the same rule the area work needed.
    canon = {}
    for term, _ in casing.most_common():
        canon.setdefault(term.lower(), term)
    multi = sum(1 for t in canon
                if sum(1 for c in casing if c.lower() == t) > 1)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_bytes(orjson.dumps({"labels": labels, "canonical_casing": canon,
                                  "source": src.name}))
    print(f"\n  distinct item names labelled : {len(labels):,}")
    print(f"  distinct std_terms           : {len(canon):,}")
    print(f"  terms seen in >1 casing      : {multi}")
    with_ing = sum(1 for v in labels.values() if v["ingredients"])
    print(f"  names carrying ingredients   : {with_ing:,} "
          f"({with_ing/len(labels)*100:.1f}%)")
    print(f"\n  most common std_terms:")
    for t, n in casing.most_common(10):
        print(f"     {n:>9,}  {t}")
    print(f"\n  -> {OUT.name}  ({OUT.stat().st_size/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source")
    sys.exit(main(p.parse_args()))
