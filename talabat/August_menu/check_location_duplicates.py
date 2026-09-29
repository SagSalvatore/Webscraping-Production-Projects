"""Location-based duplicate check across the July catalogue and August.

THE RULE, per Sagar: a brand name repeating is NOT a duplicate - KFC has many
branches. A brand repeating AT THE SAME PLACE is. So every key below pairs the
name with a location, never the name on its own.

Four keys, from tightest to loosest, because each catches a different failure:

  name + coordinates   two records at one point. The hardest evidence - it
                       cannot be explained by spelling.
  name + address       same street address. Catches records whose coordinates
                       drifted between sources but describe one outlet.
  name + area + city   same neighbourhood. Loosest, and the one that flags
                       genuine same-area branches too, so it is reported
                       separately rather than merged into the total.

THREE SCOPES:
  1. inside August       - did we ship the same outlet twice?
  2. inside July         - what Tech already has (baseline, for comparison)
  3. ACROSS the two      - an "August new brand" that already exists in July.
                           This is the one that matters most: the August cohort
                           came from `genuinely_new_brand`, so an overlap here
                           means that classification let something through.

Areas and cities are compared case- and punctuation-insensitively. Coordinates
are rounded to 4 dp (~11 m) - tighter than that and a 2 m GPS difference hides
a real duplicate.

    python check_location_duplicates.py
"""
import csv
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import ijson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
AUG = HERE / "data" / "August_export.json"
JUL = ROOT / "export" / "talabat_export.json"
OUT = HERE / "data" / "location_duplicate_report.csv"

sys.stdout.reconfigure(encoding="utf-8")


def nm(s):
    """Case/punctuation-insensitive. Deliberately NOT a fuzzy match: 'Al Barsha
    1' and 'Al Barsha 3' score 91% on fuzzy and are different places."""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def geo(r, dp=4):
    g = r.get("geo") or {}
    la, lo = g.get("lat"), g.get("lng")
    if la is None or lo is None:
        return None
    return (round(float(la), dp), round(float(lo), dp))


def keys(r):
    n = nm(r.get("name"))
    L = r.get("location") or {}
    g = geo(r)
    return {
        "name+coords": (n, g) if g and n else None,
        "name+address": (n, nm(L.get("raw"))) if n and nm(L.get("raw")) else None,
        "name+area+city": (n, nm(L.get("area")), nm(L.get("city")))
                          if n and nm(L.get("area")) else None,
    }


def load_aug():
    recs = []
    d = json.loads(AUG.read_text(encoding="utf-8"))
    for r in d:
        recs.append({"src": "AUG", "source_id": r.get("source_id"),
                     "name": r.get("name"), "location": r.get("location"),
                     "geo": r.get("geo"),
                     "verified": bool(r.get("is_verified_location"))})
    return recs


def load_jul():
    recs = []
    with open(JUL, "rb") as f:
        for r in ijson.items(f, "item", use_float=True):
            recs.append({"src": "JUL", "source_id": r.get("source_id"),
                         "name": r.get("name"), "location": r.get("location"),
                         "geo": r.get("geo"),
                         "verified": bool(r.get("is_verified_location"))})
    return recs


def report(label, recs, kind, rows_out, base_only=False):
    pool = [r for r in recs if not (base_only and r["verified"])]
    idx = defaultdict(list)
    for r in pool:
        k = keys(r)[kind]
        if k:
            idx[k].append(r)
    dups = {k: v for k, v in idx.items() if len(v) > 1}
    extra = sum(len(v) - 1 for v in dups.values())
    print(f"    {kind:16} {len(dups):>6,} keys  {extra:>7,} extra records")
    for k, v in list(dups.items())[:400]:
        for r in v[1:]:
            rows_out.append({
                "scope": label, "match_on": kind,
                "name": r["name"], "source_id": r["source_id"],
                "dataset": r["src"],
                "area": (r["location"] or {}).get("area"),
                "city": (r["location"] or {}).get("city"),
                "address": (r["location"] or {}).get("raw"),
                "lat": (r["geo"] or {}).get("lat"),
                "lng": (r["geo"] or {}).get("lng"),
                "group_size": len(v),
                "matched_with": " ; ".join(
                    f"{x['src']}:{x['source_id']}" for x in v[:4]),
            })
    return dups, extra


def main():
    print("=" * 76)
    print("  LOCATION-BASED DUPLICATE CHECK")
    print("=" * 76)
    aug = load_aug()
    print(f"  August records : {len(aug):,} "
          f"({sum(1 for r in aug if not r['verified']):,} base)")
    jul = load_jul()
    print(f"  July records   : {len(jul):,}")

    rows = []
    print("\n  --- 1. WITHIN AUGUST (base records only) ---")
    for kind in ("name+coords", "name+address", "name+area+city"):
        report("within_august", aug, kind, rows, base_only=True)

    print("\n  --- 2. WITHIN AUGUST (all records, incl. verified locations) ---")
    for kind in ("name+coords", "name+address"):
        report("within_august_all", aug, kind, rows)

    print("\n  --- 3. WITHIN JULY (baseline for comparison) ---")
    for kind in ("name+coords", "name+address", "name+area+city"):
        report("within_july", jul, kind, rows)

    print("\n  --- 4. ACROSS AUGUST x JULY  <-- the one that matters ---")
    cross = [r for r in aug if not r["verified"]] + jul
    xdups = {}
    for kind in ("name+coords", "name+address", "name+area+city"):
        idx = defaultdict(list)
        for r in cross:
            k = keys(r)[kind]
            if k:
                idx[k].append(r)
        # only groups containing BOTH datasets are real cross-overlap
        mixed = {k: v for k, v in idx.items()
                 if len({x["src"] for x in v}) > 1}
        n_aug = sum(sum(1 for x in v if x["src"] == "AUG") for v in mixed.values())
        print(f"    {kind:16} {len(mixed):>6,} places  "
              f"{n_aug:>6,} August records also present in July")
        xdups[kind] = mixed
        for k, v in list(mixed.items())[:400]:
            for r in v:
                if r["src"] != "AUG":
                    continue
                rows.append({
                    "scope": "august_also_in_july", "match_on": kind,
                    "name": r["name"], "source_id": r["source_id"],
                    "dataset": "AUG",
                    "area": (r["location"] or {}).get("area"),
                    "city": (r["location"] or {}).get("city"),
                    "address": (r["location"] or {}).get("raw"),
                    "lat": (r["geo"] or {}).get("lat"),
                    "lng": (r["geo"] or {}).get("lng"),
                    "group_size": len(v),
                    "matched_with": " ; ".join(
                        f"{x['src']}:{x['source_id']}" for x in v[:4]),
                })

    if rows:
        with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n  -> {OUT.name}  ({len(rows):,} rows)")

    print("\n  examples of August records that already exist in July "
          "(matched on coordinates):")
    for k, v in list(xdups["name+coords"].items())[:8]:
        a = [x for x in v if x["src"] == "AUG"]
        j = [x for x in v if x["src"] == "JUL"]
        if a and j:
            print(f"     {a[0]['name'][:30]:32} AUG:{a[0]['source_id']:>9} "
                  f"== JUL:{j[0]['source_id']:>9}  "
                  f"{str((a[0]['location'] or {}).get('area'))[:26]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
