"""D2 - derive `location.city` for the July records that have none, using the
coordinate kNN that measured 99.45% on August.

WHY THIS IS NEEDED. 4,690 of July's 17,187 records (27.3%) carry no city, and
95.9% of those show the area_name fallback signature - Talabat never supplied an
address, so there was no last segment to parse. The remaining 204 have damaged
addresses (truncated, or an Arabic city segment that scrub stripped). It is
missing source data, not a parser bug, so it cannot be fixed by re-parsing.

WHAT IT IS FIXED FROM. `geo` exists on 16,983 of 17,187 records. Deriving city
from coordinates we already hold is a legitimate derivation, and the method was
measured at 99.45% held-out on August (see reference-city-resolution-measured).

THE LABELS ARE THE WHOLE POINT - only verified-location records may train it.
Their address AND their lat/lng come from the SAME Google listing, so label and
position agree by construction. July's own `city` values are parsed from address
TEXT while `geo` comes from Talabat, and the two disagree badly - 100% of July's
"Al Ain" rows sit more than 40 km from Al Ain. Training on those would faithfully
reproduce that error, which is exactly how the 69-79% variants happened.

GUARD: further than MAX_KM from a labelled point and the city stays NULL. A
wrong emirate is worse than an absent one.

Writes a SIDECAR, never touching any input. The builder consumes it, so the
merge itself stays deterministic and API-free.

    python fill_july_cities.py --dry-run
    python fill_july_cities.py
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import ijson
import numpy as np
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

JULY_EXPORT = ROOT / "export" / "talabat_export.json"
AUG_EXPORT = ROOT / "August_menu" / "data" / "August_export.json"
OUT = DATA / "july_city_fixes.json"

MAX_KM = 15.0
K = 3
DEG_TO_KM = 111.0

# stay inside the vocabulary July shipped
CANON = {"Ras Al-Khaimah": "Ras Al Khaimah",
         "Kalba": "Sharjah"}          # Kalba is a Sharjah exclave

sys.stdout.reconfigure(encoding="utf-8")


def record_key(rec):
    """Identifies a RECORD, not a restaurant.

    source_id alone is not unique: the 1,989 location records reuse their
    parent's source_id at a different address, so keying by source_id would
    apply one branch's city to every branch of the chain.

    is_verified_location is the third component because source_id+raw ALSO
    collides - on 23 records where a chain's main entry sits at the same address
    as one of its own location entries (ALBAIK 668854, KFC 8410, Burger King
    1327: identical address, identical menu, one verified and one not).
    """
    loc = rec.get("location") or {}
    return (f"{rec.get('source_id')}|{loc.get('raw') or ''}"
            f"|{int(bool(rec.get('is_verified_location')))}")


def labelled_points():
    """(coords, cities) from verified-location records in BOTH exports."""
    X, y = [], []
    src = Counter()
    for path, tag in ((JULY_EXPORT, "july"), (AUG_EXPORT, "august")):
        with open(path, "rb") as f:
            for rec in ijson.items(f, "item", use_float=True):
                if not rec.get("is_verified_location"):
                    continue
                city = (rec.get("location") or {}).get("city")
                geo = rec.get("geo") or {}
                if not city or geo.get("lat") is None:
                    continue
                X.append((geo["lat"], geo["lng"]))
                y.append(CANON.get(city, city))
                src[tag] += 1
    return np.array(X), np.array(y), src


def main(args):
    print("=" * 74)
    print("  FILL JULY location.city  (coordinate kNN, verified labels only)")
    print("=" * 74)

    X, y, src = labelled_points()
    print(f"  labelled points : {len(X):,}  "
          f"({', '.join(f'{k}={v:,}' for k, v in src.items())})")
    if len(X) < 100:
        print("  ABORT: too few labelled points to fit a meaningful kNN")
        return 1
    print(f"  label vocabulary: {dict(Counter(y).most_common())}")

    need, keys, seen = [], [], Counter()
    no_geo = 0
    total = 0
    with open(JULY_EXPORT, "rb") as f:
        for rec in ijson.items(f, "item", use_float=True):
            total += 1
            if (rec.get("location") or {}).get("city"):
                continue
            geo = rec.get("geo") or {}
            if geo.get("lat") is None:
                no_geo += 1
                continue
            k = record_key(rec)
            seen[k] += 1          # only the records we will actually write
            need.append((geo["lat"], geo["lng"]))
            keys.append(k)

    # Uniqueness only has to hold across the records being FIXED. Requiring it
    # file-wide was too strong: it rejected the run over 23 collisions that all
    # already have a city and are therefore never touched.
    dupes = sum(1 for v in seen.values() if v > 1)
    print(f"\n  July records          : {total:,}")
    print(f"  keys among fixed rows not unique: {dupes:,}"
          + ("" if not dupes else "   <-- sidecar would be ambiguous"))
    if dupes:
        print("  ABORT: record_key must be unique across the rows being fixed")
        return 1
    print(f"  missing city          : {len(need)+no_geo:,}")
    print(f"    of which no geo.lat : {no_geo:,}  (stay NULL - nothing to infer from)")
    print(f"    resolvable by coords: {len(need):,}")

    tree = cKDTree(X)
    dist, idx = tree.query(np.array(need), k=K)
    km = dist[:, 0] * DEG_TO_KM
    preds = [Counter(row).most_common(1)[0][0] for row in y[idx]]

    fixes = {k: p for k, p, d in zip(keys, preds, km) if d <= MAX_KM}
    too_far = len(need) - len(fixes)
    print(f"\n  within {MAX_KM:.0f} km of a label : {len(fixes):,}")
    print(f"  too far, left NULL     : {too_far:,}")
    print(f"  distance: median {np.median(km):.2f} km | "
          f"90th {np.percentile(km, 90):.2f} km | max {km.max():.1f} km")
    print(f"\n  predicted: {dict(Counter(fixes.values()).most_common())}")

    still_null = no_geo + too_far
    print(f"\n  July city coverage after fix: "
          f"{(total-still_null)/total*100:.1f}%  ({still_null:,} still null)")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    OUT.write_text(json.dumps({
        "generated_for": "talabat_export.json",
        "method": "coordinate_knn_k3_verified_labels",
        "max_km": MAX_KM,
        "labelled_points": int(len(X)),
        "fixes": fixes,
        "left_null_no_geo": no_geo,
        "left_null_too_far": too_far,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {OUT.name}  ({len(fixes):,} fixes)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
