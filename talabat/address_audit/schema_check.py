"""Report the SCHEMA of a records file, and diff two of them.

Standing instruction (Sagar): check the schema before running anything, so
everything stays consistent. A patch that rewrites a 545 MB deliverable must
know the exact shape going in - which keys exist, on what share of records,
with which types, and whether any record is missing the nested object the patch
writes into. Finding that out afterwards means finding it out from Tech.

WHAT IT REPORTS, per top-level key and per key of a named nested object:
    presence   how many records carry the key at all
    types      every python type seen, with counts - a field that is str on
               99% of rows and list on the rest is exactly the shape that
               breaks a downstream loop
    nulls      present but null, which is NOT the same as absent
    samples    a couple of real values

--compare runs the same pass over two files and prints only the differences,
which is how a patched file is proved to still match its original schema.

ijson for a single nested JSON array, orjson per line for JSON Lines - the
file's shape decides the tool, not its size.

    python schema_check.py <file> [--nested location] [--limit N]
    python schema_check.py <file> --compare <other>
"""
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ijson
import orjson

sys.stdout.reconfigure(encoding="utf-8")


def stream(path, limit=None):
    # SNIFF THE CONTENT, don't trust the extension. A backup named
    # "...jsonl.pre_areafix.bak" has suffix .bak, and guessing "nested array"
    # from that made ijson die on "trailing garbage" at the second record.
    # '[' opens an array, '{' means one object per line.
    with open(path, "rb") as f:
        first = f.read(2048).lstrip()[:1]
    shape = "json" if first == b"[" else "jsonl"
    with open(path, "rb") as f:
        it = (ijson.items(f, "item", use_float=True) if shape == "json"
              else (orjson.loads(l) for l in f if l.strip()))
        for i, rec in enumerate(it):
            if limit and i >= limit:
                return
            yield rec


def profile(path, nested, limit=None):
    n = 0
    present, types, nulls, samples = Counter(), defaultdict(Counter), Counter(), {}
    sub_present, sub_types, sub_nulls = Counter(), defaultdict(Counter), Counter()
    missing_nested = 0
    order = None
    for rec in stream(path, limit):
        n += 1
        if order is None:
            order = list(rec.keys())
        for k, v in rec.items():
            present[k] += 1
            types[k][type(v).__name__] += 1
            if v is None:
                nulls[k] += 1
            elif k not in samples:
                samples[k] = v
        if nested:
            sub = rec.get(nested)
            if not isinstance(sub, dict):
                missing_nested += 1
            else:
                for k, v in sub.items():
                    sub_present[k] += 1
                    sub_types[k][type(v).__name__] += 1
                    if v is None:
                        sub_nulls[k] += 1
    return {"n": n, "order": order, "present": present, "types": types,
            "nulls": nulls, "samples": samples, "sub_present": sub_present,
            "sub_types": sub_types, "sub_nulls": sub_nulls,
            "missing_nested": missing_nested}


def show(p, path, nested):
    n = p["n"]
    print(f"\n  {path.name}   {n:,} records   ({path.stat().st_size/1e6:.0f} MB)")
    print(f"  key order: {p['order']}")
    print(f"  {'KEY':26}{'PRESENT':>12}  {'NULL':>7}  TYPES")
    for k in p["order"]:
        t = ", ".join(f"{a} {b:,}" for a, b in p["types"][k].most_common())
        print(f"    {k:24}{p['present'][k]:>10,} "
              f"{p['present'][k]/n*100:>5.1f}% {p['nulls'][k]:>7,}  {t[:46]}")
    extra = [k for k in p["present"] if k not in p["order"]]
    if extra:
        print(f"    KEYS NOT IN THE FIRST RECORD: {extra}")
    if nested:
        print(f"  nested '{nested}' missing or not an object on: "
              f"{p['missing_nested']:,} records")
        for k, c in p["sub_present"].most_common():
            t = ", ".join(f"{a} {b:,}" for a, b in p["sub_types"][k].most_common())
            print(f"    {nested}.{k:16}{c:>10,} {c/n*100:>5.1f}% "
                  f"{p['sub_nulls'][k]:>7,}  {t[:40]}")


def main(a):
    path = Path(a.file)
    p = profile(path, a.nested, a.limit)
    show(p, path, a.nested)
    if not a.compare:
        return 0

    other = Path(a.compare)
    q = profile(other, a.nested, a.limit)
    show(q, other, a.nested)
    print("\n  DIFFERENCES")
    diffs = []
    if p["n"] != q["n"]:
        diffs.append(f"record count {p['n']:,} -> {q['n']:,}")
    if p["order"] != q["order"]:
        diffs.append(f"key order changed\n      {p['order']}\n      {q['order']}")
    for k in set(p["present"]) | set(q["present"]):
        if p["present"][k] != q["present"][k]:
            diffs.append(f"{k}: present {p['present'][k]:,} -> {q['present'][k]:,}")
        if dict(p["types"][k]) != dict(q["types"][k]):
            diffs.append(f"{k}: types {dict(p['types'][k])} -> {dict(q['types'][k])}")
        if p["nulls"][k] != q["nulls"][k]:
            diffs.append(f"{k}: nulls {p['nulls'][k]:,} -> {q['nulls'][k]:,}")
    if a.nested:
        for k in set(p["sub_present"]) | set(q["sub_present"]):
            if p["sub_present"][k] != q["sub_present"][k]:
                diffs.append(f"{a.nested}.{k}: present {p['sub_present'][k]:,}"
                             f" -> {q['sub_present'][k]:,}")
            if p["sub_nulls"][k] != q["sub_nulls"][k]:
                diffs.append(f"{a.nested}.{k}: nulls {p['sub_nulls'][k]:,}"
                             f" -> {q['sub_nulls'][k]:,}")
    print("    IDENTICAL SCHEMA" if not diffs else
          "\n".join(f"    {d}" for d in diffs))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--nested", help="also profile this nested object, e.g. location")
    ap.add_argument("--compare", help="second file to diff the schema against")
    ap.add_argument("--limit", type=int)
    sys.exit(main(ap.parse_args()))
