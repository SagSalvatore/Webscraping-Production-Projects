"""Review export of every menu item whose std_term did NOT come from the July
deliverable - the ones our own mapping produced this month.

PURPOSE. These are the only rows where our mapping could be wrong. Everything
else was copied verbatim from what Tech already holds (310,061 item_keys,
byte-identical). So an accuracy audit only has to look here. The manager wants
to re-run these through an LLM and compare against what kNN + TF-IDF chose.

WHAT IS IN SCOPE
  * 52,754 item_keys absent from the July export entirely
  * 113 item_keys July shipped with std_term = null
  Both are "not exactly matched from July".

review_id IS THE MAPPING KEY. Deterministic - sha256 of the item_key, prefixed
"MK-" so Excel treats it as text and cannot reformat or truncate it, and stable
across re-runs so a returned verdict always lands back on the right row. The
raw item_key travels alongside it, but the id is what to join on: item_keys
contain commas, quotes and non-Latin script that survive a spreadsheet round
trip badly.

One row per item_key, NOT per menu row - the same key repeats across branches
and the mapping is per key. row_count shows the blast radius of a wrong call.

Outputs
  new_mappings_review.csv     for the manager / Excel
  new_mappings_review.ndjson  one object per line, for an LLM pipeline

    python export_new_mappings_review.py
"""
import csv
import hashlib
import re
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_PASSWORD  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

ITEMS = DATA / "scrape" / "menu_items.jsonl"
MAPPING = DATA / "std_term_mapping_august.json"
JULY_FLAT = DATA / "export_july_flat.jsonl"
PROFILES = DATA / "std_term_canonical_ingredients.json"
OUT_CSV = DATA / "new_mappings_review.csv"
OUT_ND = DATA / "new_mappings_review.ndjson"

# The deliverable's ingredients are conformed to the controlled vocabulary
# (conform_ingredients.py), because July shipped 100% in-vocabulary. The review
# file MUST show the same values - a manager auditing "discarded" that never
# ships would be auditing a row that does not exist.
import psycopg2
_cn = psycopg2.connect(host="localhost", port=5432,
                       dbname="RestaurantIntelligence", user="postgres",
                       password=PG_PASSWORD)
_c = _cn.cursor()
_c.execute("select ingredient_name from ingredients_taxonomy")
VOCAB = {r[0].lower(): r[0] for r in _c.fetchall()}
_cn.close()


def conform(s):
    """Identical rule to conform_ingredients.recover()."""
    t = str(s).strip().lower()
    if t in VOCAB:
        return VOCAB[t]
    for cut in ("es", "s"):
        if t.endswith(cut) and t[: -len(cut)] in VOCAB:
            return VOCAB[t[: -len(cut)]]
    for part in re.split(r"[/&,]", t):
        p = part.strip()
        if p in VOCAB:
            return VOCAB[p]
        for cut in ("es", "s"):
            if p.endswith(cut) and p[: -len(cut)] in VOCAB:
                return VOCAB[p[: -len(cut)]]
    p = re.sub(r"\(.*?\)", "", t).strip()
    return VOCAB.get(p)

sys.stdout.reconfigure(encoding="utf-8")


def review_id(item_key: str) -> str:
    """Stable, Excel-safe. 'MK-' keeps it textual; sha256 keeps it deterministic
    so the manager's verdict file joins back cleanly next month too."""
    return "MK-" + hashlib.sha256(item_key.encode("utf-8")).hexdigest()[:12]


def main():
    print("=" * 72)
    print("  EXPORT: newly-mapped menu items for accuracy review")
    print("=" * 72)

    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    profiles = json.loads(PROFILES.read_text(encoding="utf-8"))

    july = {}
    with open(JULY_FLAT, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                k = r.get("item_key")
                if k and k not in july:
                    july[k] = r.get("std_term")
    print(f"  July item_keys        : {len(july):,}")

    rows = Counter()
    branches = defaultdict(set)
    names = defaultdict(Counter)
    cats = defaultdict(Counter)
    descs = {}
    prices = defaultdict(list)
    with open(ITEMS, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            k = r["item_key"]
            rows[k] += 1
            branches[k].add(r["branch_id"])
            if r.get("item_name"):
                names[k][r["item_name"]] += 1
            if r.get("category"):
                cats[k][r["category"]] += 1
            if r.get("description") and k not in descs:
                descs[k] = r["description"]
            if r.get("price_aed") is not None:
                prices[k].append(float(r["price_aed"]))
    print(f"  refreshed item_keys   : {len(rows):,}")

    # not exactly matched from July: absent entirely, or July had it as null
    targets = [k for k in rows if k not in july or july[k] is None]
    absent = sum(1 for k in targets if k not in july)
    was_null = len(targets) - absent
    print(f"\n  in scope              : {len(targets):,} item_keys "
          f"({sum(rows[k] for k in targets):,} menu rows)")
    print(f"    absent from July    : {absent:,}")
    print(f"    July had std_term=null: {was_null:,}")

    out = []
    for k in sorted(targets, key=lambda x: -rows[x]):
        m = mapping.get(k) or {}
        term = m.get("std_term")
        raw_ings = m.get("ingredients") or []
        ings, seen = [], set()
        for i in raw_ings:
            c = conform(i)
            if c and c not in seen:
                seen.add(c)
                ings.append(c)
        src = m.get("method")
        if not ings:
            ings = [VOCAB[p.lower()] for p in (profiles.get(term) or [])
                    if p.lower() in VOCAB]
            src = "class_profile"
        p = prices.get(k) or []
        out.append({
            "review_id": review_id(k),
            "item_key": k,
            "item_name": names[k].most_common(1)[0][0] if names[k] else "",
            "menu_category": cats[k].most_common(1)[0][0] if cats[k] else "",
            "description": (descs.get(k) or "")[:300],
            "std_term": term,
            "taxonomy": m.get("taxonomy"),
            "ingredients": " | ".join(str(i) for i in ings),
            "ingredient_count": len(ings),
            "mapping_method": m.get("method"),
            "ingredient_source": src,
            "confidence": m.get("confidence"),
            "vote_agreement": m.get("vote_agreement", ""),
            "review_required": "YES" if m.get("review_required") else "NO",
            "in_july": "NO" if k not in july else "NULL_STD_TERM",
            "menu_rows": rows[k],
            "restaurants": len(branches[k]),
            "price_median": round(sorted(p)[len(p) // 2], 2) if p else None,
        })

    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    with open(OUT_ND, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n  --- BREAKDOWN ---")
    for k, v in Counter(r["mapping_method"] for r in out).most_common():
        n = sum(r["menu_rows"] for r in out if r["mapping_method"] == k)
        print(f"    {str(k):18} {v:>7,} keys / {n:>8,} rows")
    print(f"    flagged review_required: "
          f"{sum(1 for r in out if r['review_required']=='YES'):,} keys")
    print(f"    ingredients from class profile: "
          f"{sum(1 for r in out if r['ingredient_source']=='class_profile'):,}")
    print(f"    distinct std_terms assigned   : "
          f"{len({r['std_term'] for r in out}):,}")
    ids = {r["review_id"] for r in out}
    print(f"\n    review_id unique: {len(ids) == len(out)} "
          f"({len(ids):,} of {len(out):,})")

    print(f"\n  -> {OUT_CSV.name}   ({len(out):,} rows)")
    print(f"  -> {OUT_ND.name}")
    print("\n  Highest-impact rows first (menu_rows desc), so a reviewer who "
          "stops early\n  has still covered the most consequential mappings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
