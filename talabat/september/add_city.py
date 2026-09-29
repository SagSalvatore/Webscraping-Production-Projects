"""Add city / emirate to the September listing CSV, from coordinates.

WHY NOT TAKE IT FROM area_name. 90 of the 1,047 rows carry an EMIRATE in the
area field ("Abu Dhabi") and the other 957 carry a real area ("Bani Yas East"),
so the field answers two different questions and cannot be split reliably by
text. Nor is it in the menu payload - restaurant_status carries only the
area_name we passed in.

METHOD: coordinate kNN, the same one used for July. Measured 99.45% held-out on
August; a direct LLM on the same task scored 47.2%.

THE LABELS ARE THE POINT - only VERIFIED-LOCATION records may train it. Their
address and their lat/lng both come from the SAME Google listing, so label and
position agree by construction. Talabat-sourced city values are parsed from
address TEXT while geo comes from Talabat, and the two disagree badly: 100% of
July's "Al Ain" rows sit more than 40 km from Al Ain. Training on those
reproduces that error faithfully.

GUARD: beyond MAX_KM from any labelled point the city stays blank. A wrong
emirate is worse than an absent one.

    python add_city.py --dry-run
    python add_city.py
"""
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

import ijson
import numpy as np
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = HERE / "data" / "september_new_listings_202609_deduped.csv"
OUT = HERE / "data" / "september_new_listings_202609_with_city.csv"
EXPORTS = [ROOT / "export" / "talabat_export.json",
           ROOT / "August_menu" / "data" / "August_export.json"]
MAX_KM = 15.0
K = 5
sys.stdout.reconfigure(encoding="utf-8")

# every UAE city sits inside one emirate; Al Ain/Khor Fakkan/Kalba are cities
# whose parent emirate differs from their name, which is why both columns exist
EMIRATE = {
    "dubai": "Dubai", "abu dhabi": "Abu Dhabi", "al ain": "Abu Dhabi",
    "sharjah": "Sharjah", "khor fakkan": "Sharjah", "kalba": "Sharjah",
    "ajman": "Ajman", "fujairah": "Fujairah", "dibba al hisn": "Sharjah",
    "ras al khaimah": "Ras Al Khaimah", "umm al quwain": "Umm Al Quwain",
}


def labelled():
    """(lat, lng, city) from verified-location records only."""
    pts, lab = [], []
    for p in EXPORTS:
        if not p.exists():
            continue
        with open(p, "rb") as f:
            for rec in ijson.items(f, "item"):
                if not rec.get("is_verified_location"):
                    continue
                g = rec.get("geo") or {}
                c = ((rec.get("location") or {}).get("city") or "").strip()
                try:
                    la, ln = float(g.get("lat")), float(g.get("lng"))
                except (TypeError, ValueError):
                    continue
                if not c:
                    continue
                pts.append((la, ln))
                lab.append(c)
    return pts, lab


def main(args):
    print("=" * 74)
    print("  ADD CITY / EMIRATE  (coordinate kNN)")
    print("=" * 74)

    pts, lab = labelled()
    print(f"  labelled points (verified locations only) : {len(pts):,}")
    print(f"  distinct labels : {sorted(set(lab))}")
    if len(pts) < 50:
        raise SystemExit("too few labelled points to trust the kNN")

    tree = cKDTree(np.radians(np.array(pts)))
    rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
    print(f"  september rows  : {len(rows):,}")

    got = blank = 0
    for r in rows:
        try:
            q = np.radians([[float(r["lat"]), float(r["lon"])]])
        except (TypeError, ValueError):
            r["city"] = r["emirate"] = ""
            blank += 1
            continue
        d, i = tree.query(q, k=min(K, len(pts)))
        d, i = np.atleast_2d(d), np.atleast_2d(i)
        # great-circle: the tree is in radians, so scale by earth radius
        km = d[0] * 6371.0
        near = [lab[j] for j, dist in zip(i[0], km) if dist <= MAX_KM]
        if not near:
            r["city"] = r["emirate"] = ""
            blank += 1
            continue
        city = Counter(near).most_common(1)[0][0]
        r["city"] = city
        r["emirate"] = EMIRATE.get(city.strip().lower(), city)
        got += 1

    print(f"\n  city resolved : {got:,} ({got/len(rows)*100:.1f}%)")
    print(f"  left blank    : {blank}   (>{MAX_KM} km from any labelled point)")
    print(f"\n  {'CITY':22}{'ROWS':>6}   EMIRATE")
    for c, n in Counter(r["city"] for r in rows if r["city"]).most_common():
        print(f"    {c:22}{n:>6}   {EMIRATE.get(c.lower(), c)}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    cols = [c for c in rows[0].keys() if c not in ("city", "emirate")]
    # put city/emirate right after the area so the CSV reads left to right
    at = cols.index("area_name_real") + 1
    cols = cols[:at] + ["city", "emirate"] + cols[at:]
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"\n  -> {OUT.name}  ({len(rows):,} rows)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
