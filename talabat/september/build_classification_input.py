"""Build the September input for August_classification/.

The classification pipeline reads a JSONL of restaurants with these fields:
    branch_id · restaurant_id · name · area_name · cuisines (LIST)
September's listing file is a CSV whose area column is `area_name_real` and
whose cuisines are one comma-joined string, so it cannot be fed in directly.

city / emirate / url / lat / lon are carried through as well. They are not read
by rules.py, but the Serper enrichment stage writes a locations file that is
much more useful joined against them, and it costs nothing to keep them here.

    python build_classification_input.py
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "september_new_listings_202609_with_city.csv"
OUT = HERE / "data" / "sept_restaurants_for_classification.jsonl"
sys.stdout.reconfigure(encoding="utf-8")


def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
    out = []
    for r in rows:
        out.append({
            "branch_id": int(r["branch_id"]),
            "restaurant_id": r.get("restaurant_id"),
            "name": (r.get("name") or "").strip(),
            "area_name": (r.get("area_name_real") or "").strip(),
            "city": (r.get("city") or "").strip(),
            "emirate": (r.get("emirate") or "").strip(),
            "cuisines": [c.strip() for c in (r.get("cuisines") or "").split(",")
                         if c.strip()],
            "lat": r.get("lat"), "lon": r.get("lon"),
            "url": r.get("url"),
        })

    # branch_id must be the key rules.py joins the menu profile on, and the
    # menu file stores it as an int - a str would silently profile nothing.
    assert len({o["branch_id"] for o in out}) == len(out), "branch_id not unique"
    assert all(isinstance(o["branch_id"], int) for o in out)

    OUT.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n"
                           for o in out), encoding="utf-8")
    nc = sum(1 for o in out if o["cuisines"])
    print(f"  restaurants        : {len(out):,}")
    print(f"  distinct brand names: {len({o['name'].lower() for o in out}):,}")
    print(f"  with cuisines      : {nc:,} ({nc/len(out)*100:.1f}%)")
    print(f"  with an area       : {sum(1 for o in out if o['area_name']):,}")
    print(f"  by emirate         : "
          f"{dict(Counter(o['emirate'] or '-' for o in out).most_common())}")
    print(f"\n  -> {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
