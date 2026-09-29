"""Generate chain_id + chain_locations_count for the September cohort.

THE FORMULA IS IMPORTED, NEVER REIMPLEMENTED. chain_id must be identical to
what every earlier cohort produced, or the same brand would carry two different
ids across months and chain lineage would silently break. So `slugify`,
`chain_id_int`, `smart_title`, `strip_fused_address_suffix` and
`light_normalize_name` all come from export/export_to_json.py:

    key      = light_normalize_name(strip_fused_address_suffix(smart_title(name)))
    chain_id = int(sha256(slugify(key)).hexdigest()[:13], 16)

SHA-256, not Python's hash(), which is randomised per process and would give a
different id on every run. 52 bits keeps it under JS MAX_SAFE_INTEGER so the
client app parses it as a plain JSON number.

chain_locations_count IS THE TALABAT BRANCH COUNT, which is what the field
means in the data Tech already holds - it is NOT the Google Maps count that
decides `outlet_type`. The two answer different questions and the Maps count
lives in the classification output as `google_locations_uae`.

Getting that count right needs the PARKED DUPLICATES. September was deduplicated
by name for menu scraping, so the deliverable holds one row per brand - but the
100 rows in september_name_duplicates_removed.csv are real Talabat branches of
those same brands ("Low and slow" x11, "Slice Amigo" x9). Counting only the kept
rows would report every September chain as having exactly 1 branch.

    python build_chain_ids.py --dry-run
    python build_chain_ids.py
"""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ijson

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "export"))
from export_to_json import (chain_id_int, light_normalize_name,  # noqa: E402
                            slugify, smart_title,
                            strip_fused_address_suffix)

SRC = HERE / "data" / "sept_restaurants_for_classification.jsonl"
PARKED = HERE / "data" / "september_name_duplicates_removed.csv"
CLASSIFIED = ROOT / "August_classification" / "data_sep" / "restaurants_classified.jsonl"
EXPORTS = [ROOT / "export" / "talabat_export.json",
           ROOT / "August_menu" / "data" / "August_export.json"]
OUT = HERE / "data" / "sept_chain_ids.json"
OUT_CSV = HERE / "data" / "sept_chain_ids.csv"
sys.stdout.reconfigure(encoding="utf-8")


def chain_key(name):
    return light_normalize_name(strip_fused_address_suffix(smart_title(name)))


def main(args):
    print("=" * 78)
    print("  BUILD chain_id FOR SEPTEMBER")
    print("=" * 78)

    kept = [json.loads(l) for l in open(SRC, encoding="utf-8")]
    parked = list(csv.DictReader(open(PARKED, encoding="utf-8-sig")))
    print(f"  kept brands (deliverable) : {len(kept):,}")
    print(f"  parked name-duplicates    : {len(parked):,}  (real branches, "
          f"counted but not shipped)")

    # count over kept + parked, so a brand's branch count is the true one
    counts = Counter()
    for r in kept:
        counts[chain_key(r["name"])] += 1
    for r in parked:
        counts[chain_key(r["name"])] += 1

    out, by_key = {}, defaultdict(list)
    for r in kept:
        k = chain_key(r["name"])
        cid = chain_id_int(slugify(k))
        out[str(r["branch_id"])] = {
            "chain_id": cid,
            "chain_key": k,
            "chain_locations_count": counts[k],
        }
        by_key[k].append(cid)

    # ---- invariants ----------------------------------------------------
    ids = {v["chain_id"] for v in out.values()}
    keys = set(by_key)
    print(f"\n  distinct chain keys       : {len(keys):,}")
    print(f"  distinct chain_id         : {len(ids):,}")
    assert len(ids) == len(keys), "two keys collided onto one chain_id"
    assert all(len(set(v)) == 1 for v in by_key.values()), \
        "one key produced two chain_ids"
    assert all(0 < v["chain_id"] < 2 ** 53 for v in out.values()), \
        "chain_id outside JS MAX_SAFE_INTEGER"

    # collision against every chain_id already shipped - 52 bits is a lot of
    # room, but "a lot" is not "proven", and a collision would merge two
    # unrelated brands into one chain in Tech's data.
    prior_ids, prior_keys = set(), {}
    for p in EXPORTS:
        if not p.exists():
            continue
        with open(p, "rb") as f:
            for rec in ijson.items(f, "item"):
                if rec.get("chain_id") is not None:
                    prior_ids.add(int(rec["chain_id"]))
                if rec.get("name"):
                    prior_keys[chain_key(rec["name"])] = rec.get("chain_id")
    clash_id = ids & prior_ids
    shared_key = keys & set(prior_keys)
    print(f"  chain_id already shipped  : {len(prior_ids):,}")
    print(f"  ASSERT no id collision    : "
          f"{'PASS' if not clash_id else f'FAIL {sorted(clash_id)[:3]}'}")
    print(f"  brand names shared with a prior cohort : {len(shared_key)}"
          f"   (these SHOULD reuse the same id)")
    for k in sorted(shared_key)[:5]:
        mine = by_key[k][0]
        print(f"     {k[:34]:36} sept={mine}  prior={prior_keys[k]}  "
              f"{'MATCH' if mine == prior_keys[k] else 'MISMATCH'}")
    assert not clash_id, "chain_id collision with already-shipped data"

    multi = {k: c for k, c in counts.items() if c > 1}
    print(f"\n  brands with >1 Talabat branch : {len(multi)}")
    for k, c in Counter(multi).most_common(8):
        print(f"     {c:>3} branches  {k[:44]}")

    # cross-check against the Google-Maps verdict, which answers a different
    # question - reported, never reconciled
    cls = {}
    for line in open(CLASSIFIED, encoding="utf-8"):
        c = json.loads(line)
        cls[c["branch_id"]] = c
    talabat_multi = {int(b) for b, v in out.items()
                     if v["chain_locations_count"] > 1}
    maps_chain = {b for b, c in cls.items() if c["outlet_type"] == "Chain"}
    print(f"\n  Talabat says >1 branch : {len(talabat_multi)}")
    print(f"  Google Maps says Chain : {len(maps_chain)}")
    print(f"  agree on both          : {len(talabat_multi & maps_chain)}"
          f"   (they measure different things - Maps decides outlet_type)")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["branch_id", "restaurant_name", "chain_id", "chain_key",
                    "chain_locations_count", "outlet_type", "chain_type",
                    "google_locations_uae"])
        for r in kept:
            b = str(r["branch_id"])
            v, c = out[b], cls.get(r["branch_id"], {})
            w.writerow([b, r["name"], v["chain_id"], v["chain_key"],
                        v["chain_locations_count"], c.get("outlet_type", ""),
                        c.get("chained_outlet_type", ""),
                        c.get("google_locations_uae", "")])
    print(f"\n  -> {OUT.name}   ({len(out):,} branches)")
    print(f"  -> {OUT_CSV.name}   ({len(out):,} rows)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
