"""Fill location.city in August_export.json - coordinate kNN, no API, no spend.

WHAT WON, AND WHY - all measured on held-out data, not argued:

    kNN k=3 on same-listing labels    99.45%   <- this
    gpt-4.1-mini with coordinates       47.2%
    kNN on July's labelled coords     69-79%
    kNN on brand-level google labels  72-81%
    geometric rule on coordinates        62%

THE LABELS ARE THE WHOLE STORY. Every earlier attempt trained on labels that
did not belong to the coordinate they were paired with:

  * July's `city` is parsed from address TEXT while `geo` comes from Talabat -
    they disagree badly (100% of July's "Al Ain" rows sit >40 km from Al Ain).
  * google_maps_details.jsonl is keyed by BRAND, so every branch of a chain
    inherits whichever outlet Google returned first.

The 3,628 verified-location records do not have that problem: their address AND
their lat/lng both come from the SAME Google listing. Labels and coordinates
agree by construction, and kNN on them is near-perfect.

WHY THE LLM LOST. It was given the coordinates and told to trust them, and still
answered from the NAME - it put "King Faisal St" (25.3884, 55.4521, plainly
Ajman) in Sharjah, and "Al Jimi - Slemi" (24.2452, 55.7530, Al Ain) in Sharjah.
Precise coordinate geometry is not what language models are good at. Kept here
as a measured result so it is not retried next month.

GUARD: if the nearest labelled point is further than MAX_KM, the city is left
NULL. A wrong emirate is worse than an absent one.

    python fill_cities.py --dry-run
    python fill_cities.py --apply
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
EXPORT = HERE / "data" / "August_export.json"

MAX_KM = 15.0
K = 3

# stay inside the vocabulary July shipped
CANON = {"Ras Al-Khaimah": "Ras Al Khaimah",
         "Kalba": "Sharjah"}        # Kalba is a Sharjah exclave

sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    d = json.loads(EXPORT.read_text(encoding="utf-8"))
    print("=" * 70)
    print("  FILL location.city  (coordinate kNN on same-listing labels)")
    print("=" * 70)

    X, y = [], []
    for r in d:
        if (r.get("is_verified_location") and r["location"].get("city")
                and r["geo"].get("lat") is not None):
            X.append((r["geo"]["lat"], r["geo"]["lng"]))
            y.append(CANON.get(r["location"]["city"], r["location"]["city"]))
    X, y = np.array(X), np.array(y)
    print(f"  labelled points (address+coords from one google listing): {len(X):,}")

    need = [r for r in d if not r["location"].get("city")
            and r["geo"].get("lat") is not None]
    nocoord = [r for r in d if not r["location"].get("city")
               and r["geo"].get("lat") is None]
    print(f"  records missing city : {len(need)+len(nocoord):,} "
          f"({len(nocoord):,} without coordinates)")

    tree = cKDTree(X)
    Q = np.array([(r["geo"]["lat"], r["geo"]["lng"]) for r in need])
    dist, idx = tree.query(Q, k=K)
    km = dist[:, 0] * 111
    preds = [Counter(row).most_common(1)[0][0] for row in y[idx]]

    filled = [(r, p) for r, p, k in zip(need, preds, km) if k <= MAX_KM]
    too_far = len(need) - len(filled)
    print(f"  within {MAX_KM:.0f} km of a labelled point: {len(filled):,}")
    print(f"  too far, left NULL                : {too_far:,}")
    print(f"  distance: median {np.median(km):.2f} km | "
          f"90th {np.percentile(km, 90):.2f} km | max {km.max():.1f} km")
    print(f"\n  predicted: {dict(Counter(p for _, p in filled).most_common())}")

    if not args.apply:
        print("\n  --dry-run: nothing written (use --apply)")
        return 0

    for r, p in filled:
        r["location"]["city"] = p
    # canonicalise the ones that were already there
    fixed = 0
    for r in d:
        c = r["location"].get("city")
        if c in CANON:
            r["location"]["city"] = CANON[c]
            fixed += 1
    with open(EXPORT, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    have = sum(1 for r in d if r["location"].get("city"))
    print(f"\n  filled {len(filled):,} | canonicalised {fixed:,}")
    print(f"  city coverage: {have:,}/{len(d):,} ({have/len(d)*100:.1f}%)")
    print(f"  cities: {dict(Counter(r['location']['city'] for r in d if r['location'].get('city')).most_common())}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--apply", action="store_true")
    sys.exit(main(p.parse_args()))
