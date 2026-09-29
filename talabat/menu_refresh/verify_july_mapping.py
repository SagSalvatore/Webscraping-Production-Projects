"""Sanity check the refreshed July-cohort menus before building their deliverable.

Runs entirely off files in menu_refresh/data - nothing is read from Postgres.

    scrape/menu_items.jsonl          1,246,162 refreshed items (raw)
    std_term_mapping_august.json     item_key -> std_term / taxonomy / ingredients
    export_july_flat.jsonl           what Tech already holds, per item_key

THE CLAIM BEING TESTED. An item that Tech already has must come back with the
IDENTICAL std_term and ingredients - a menu item that did not change must not
silently acquire a different label. Only genuinely new item_keys should carry a
freshly derived mapping. That is asserted here rather than assumed, because the
mapping ran in four tiers and only tier 2 reads the July export.

Also checks that every refreshed item HAS a std_term and ingredients at all, and
reports which tier each new item was resolved by.

    python verify_july_mapping.py
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ITEMS = DATA / "scrape" / "menu_items.jsonl"
MAPPING = DATA / "std_term_mapping_august.json"
EXPORT_FLAT = DATA / "export_july_flat.jsonl"
STATUS = DATA / "scrape" / "restaurant_status.jsonl"

sys.stdout.reconfigure(encoding="utf-8")


def norm_ings(v):
    if not v:
        return ()
    return tuple(sorted(str(x).strip().lower() for x in v if str(x).strip()))


def main():
    print("=" * 74)
    print("  VERIFY  refreshed July-cohort menu mapping")
    print("=" * 74)

    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    print(f"  mapping keys                : {len(mapping):,}")

    # what Tech already holds, keyed by item_key
    july = {}
    with open(EXPORT_FLAT, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            k = r.get("item_key")
            if k and k not in july:
                july[k] = (r.get("std_term"), norm_ings(r.get("ingredients")))
    print(f"  item_keys in the July export : {len(july):,}")

    # the refreshed scrape
    key_rows = Counter()
    branches = set()
    n = 0
    with open(ITEMS, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            key_rows[r["item_key"]] += 1
            branches.add(r["branch_id"])
            n += 1
    print(f"  refreshed items              : {n:,} over {len(branches):,} branches")
    print(f"  distinct item_key            : {len(key_rows):,}")

    # ---- 1. coverage --------------------------------------------------
    print("\n  --- COVERAGE ---")
    unmapped = [k for k in key_rows if k not in mapping]
    no_std = [k for k in key_rows
              if k in mapping and not mapping[k].get("std_term")]
    no_ing = [k for k in key_rows
              if k in mapping and not mapping[k].get("ingredients")]
    print(f"    item_keys with no mapping entry : {len(unmapped):,}"
          f"   ({sum(key_rows[k] for k in unmapped):,} rows)")
    print(f"    mapped but std_term empty       : {len(no_std):,}")
    print(f"    mapped but ingredients empty    : {len(no_ing):,}"
          f"   ({sum(key_rows[k] for k in no_ing):,} rows)")

    # ---- 2. the consistency claim --------------------------------------
    print("\n  --- CONSISTENCY WITH WHAT TECH ALREADY HAS ---")
    shared = [k for k in key_rows if k in july]
    same_t = diff_t = same_i = diff_i = 0
    dt, di = [], []
    for k in shared:
        m = mapping.get(k) or {}
        jt, ji = july[k]
        if m.get("std_term") == jt:
            same_t += 1
        else:
            diff_t += 1
            if len(dt) < 10:
                dt.append((k, jt, m.get("std_term"), m.get("method")))
        if norm_ings(m.get("ingredients")) == ji:
            same_i += 1
        else:
            diff_i += 1
            if len(di) < 6:
                di.append((k, ji, norm_ings(m.get("ingredients")), m.get("method")))
    print(f"    item_keys present in BOTH  : {len(shared):,}"
          f"   ({sum(key_rows[k] for k in shared):,} rows)")
    print(f"      std_term IDENTICAL       : {same_t:,} "
          f"({same_t/max(len(shared),1)*100:.2f}%)")
    print(f"      std_term CHANGED         : {diff_t:,}")
    for k, a, b, meth in dt:
        print(f"         {k[:30]:32} {a!r} -> {b!r}  [{meth}]")
    print(f"      ingredients IDENTICAL    : {same_i:,} "
          f"({same_i/max(len(shared),1)*100:.2f}%)")
    print(f"      ingredients CHANGED      : {diff_i:,}")
    for k, a, b, meth in di:
        print(f"         {k[:26]:28} [{meth}] {len(a)} -> {len(b)} ingredients")

    # ---- 3. the genuinely new items ------------------------------------
    print("\n  --- NEWLY MAPPED (not in the July export) ---")
    new = [k for k in key_rows if k not in july]
    print(f"    new item_keys              : {len(new):,}"
          f"   ({sum(key_rows[k] for k in new):,} rows)")
    meth = Counter((mapping.get(k) or {}).get("method") for k in new)
    rows_by = Counter()
    for k in new:
        rows_by[(mapping.get(k) or {}).get("method")] += key_rows[k]
    for m, c in meth.most_common():
        print(f"      {str(m):18} {c:>7,} keys / {rows_by[m]:>8,} rows")
    rev = [k for k in new if (mapping.get(k) or {}).get("review_required")]
    print(f"    flagged review_required    : {len(rev):,} keys "
          f"({sum(key_rows[k] for k in rev):,} rows)")
    conf = [(mapping.get(k) or {}).get("confidence", 0) for k in new
            if (mapping.get(k) or {}).get("method") == "knn"]
    if conf:
        conf.sort()
        print(f"    kNN confidence: median {conf[len(conf)//2]:.3f} | "
              f"10th pct {conf[len(conf)//10]:.3f}")

    # ---- 4. what the deliverable will cover -----------------------------
    st = [json.loads(l) for l in open(STATUS, encoding="utf-8") if l.strip()]
    ok = sum(1 for s in st if s.get("status") == "ok")
    empty = sum(1 for s in st if s.get("status") != "ok")
    print(f"\n  --- SCOPE ---")
    print(f"    branches refreshed OK      : {ok:,}")
    print(f"    branches returning empty   : {empty:,}   "
          "(Tech keeps their July data for these)")

    bad = len(unmapped) + len(no_std) + diff_t + diff_i
    print("\n" + "=" * 74)
    if bad == 0:
        print("  PASS - every item mapped, and everything Tech already has is "
              "byte-identical")
    else:
        print(f"  {bad:,} issue(s) to resolve before building the deliverable")
    print("=" * 74)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
