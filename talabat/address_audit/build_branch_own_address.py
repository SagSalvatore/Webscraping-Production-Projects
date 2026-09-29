"""One decision per branch: its OWN verified Google address, or NOT LISTED.

The durable result of three searches, so no branch is ever searched twice:

  1 text_search            serper_contact_fill.py - "{name} {area} uae", same
                           brand (strict) within 300 m          -> branch_contacts.csv
  2 text_search_name_match the SAME cached responses, re-read with a name match
                           that ignores a trailing generic word ('Il Forno
                           Restaurant' vs 'Il Forno, Mushrif Mall'); still
                           within 300 m. 92 of the blanks - our bug, not Google's.
  3 centred_search         serper_branch_ll_search.py - the brand with the map
                           centred on the branch (Serper ll, 17z). Validated on a
                           positive control: 28/30 known-listed branches found.
                           Two runs: the 1,388 blank addresses, and the 994
                           branches whose address named another emirate.
      found      -> own_address
      not found  -> not_listed  (Sagar: "if in real those branch address are not
                                 listed in google maps ... we will put NA")
      request failed -> left as it was: a failed request proves nothing

A branch with no text-search match that was never centred-searched stays
`unresolved` - it is outside the copied-address set the build fixes.

Output: data/branch_own_address.csv, keyed on branch_id, plus the listing's cid
and phone so the build can fill a still-"NA" maps_url / contact_phone from the
SAME verified listing it takes the address from.

    python build_branch_own_address.py
"""
import csv
import sys
from collections import Counter
from pathlib import Path

import orjson

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from serper_branch_ll_search import MATCH_M, brand_match, metres   # noqa: E402
from serper_contact_fill import outside_uae                         # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
DATA = HERE / "data"
CONTACTS = DATA / "branch_contacts.csv"
TEXT_CACHE = DATA / "serper_branch_cache.json"
ROWS = DATA / "address_smear_rows.csv"
CENTRED = (DATA / "blank_address_ll_results.csv", DATA / "wrong_emirate_ll_results.csv")
OUT = DATA / "branch_own_address.csv"


def main():
    geo = {}
    for r in csv.DictReader(open(ROWS, encoding="utf-8-sig")):
        if r.get("lat") and r.get("lng"):
            geo.setdefault(r["source_id"], (r["lat"], r["lng"]))
    text_cache = orjson.loads(TEXT_CACHE.read_bytes())
    out, st = {}, Counter()

    for r in csv.DictReader(open(CONTACTS, encoding="utf-8-sig")):
        sid = r["branch_id"]
        cid = r["maps_url"].split("cid=")[-1] if "cid=" in r["maps_url"] else ""
        if r["status"] == "matched" and r["address"].strip():
            out[sid] = {"branch_id": sid, "name": r["name"], "decision": "own_address",
                        "source": "text_search", "address": r["address"], "distance_m": r["distance_m"],
                        "cid": cid, "phone": r["phone"], "detail": r["matched_title"]}
            continue
        best = None
        pt = geo.get(sid)
        for p in (text_cache.get(sid) or {}).get("places") or []:
            if not pt or p.get("latitude") is None or outside_uae(p.get("latitude"), p.get("longitude")):
                continue
            d = metres(pt, (p["latitude"], p["longitude"]))
            if d <= MATCH_M and p.get("address") and brand_match(r["name"], p.get("title") or ""):
                if best is None or d < best[1]:
                    best = (p, d)
        if best:
            p, d = best
            out[sid] = {"branch_id": sid, "name": r["name"], "decision": "own_address",
                        "source": "text_search_name_match", "address": p["address"],
                        "distance_m": f"{d:.0f}", "cid": p.get("cid") or "",
                        "phone": p.get("phoneNumber") or "", "detail": p.get("title") or ""}
        else:
            out[sid] = {"branch_id": sid, "name": r["name"], "decision": "unresolved",
                        "source": "text_search_no_match", "address": "", "distance_m": "",
                        "cid": "", "phone": "", "detail": "not centred-searched"}

    failed = 0
    for r in (r for p in CENTRED if p.exists() for r in csv.DictReader(open(p, encoding="utf-8-sig"))):
        sid = r["branch_id"]
        if sid in out and out[sid]["decision"] == "own_address":
            continue                      # an earlier, stronger source already has it
        if r["verdict"].startswith("request failed"):
            failed += 1
            continue
        found = r["verdict"] == "found"
        out[sid] = {"branch_id": sid, "name": r["name"],
                    "decision": "own_address" if found else "not_listed",
                    "source": "centred_search", "address": r["address"] if found else "",
                    "distance_m": r["distance_m"] if found else "", "cid": r["cid"] if found else "",
                    "phone": r["phone"] if found else "",
                    "detail": r["matched_title"] if found else r["verdict"]}

    rows = sorted(out.values(), key=lambda x: (x["decision"], x["source"], x["branch_id"]))
    for x in rows:
        st[(x["decision"], x["source"])] += 1
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("=" * 70)
    print(f"  BRANCH OWN ADDRESS   {len(rows):,} branches")
    print("=" * 70)
    for (dec, src), n in sorted(st.items()):
        print(f"    {dec:12} {src:24} {n:>6,}")
    print(f"    centred requests that failed (decision left unchanged): {failed}")
    print(f"\n  -> {OUT.relative_to(HERE.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
