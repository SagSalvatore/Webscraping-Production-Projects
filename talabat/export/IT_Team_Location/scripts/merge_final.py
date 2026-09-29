"""Merge the manual-stage results back into the cleaned Mongo file.

Adds a deterministic validation gate over whatever the LLM returned, because a
prompt instruction is not a guarantee: bare zone codes ('W10', 'Zone 1'), bare
cities and obvious non-areas ('Police College') are rejected here and fall back
to null rather than being trusted.

Run: python merge_final.py
"""
import csv
import re
import sys

import orjson
from loguru import logger

from config import INPUT_JSON, LOG_DIR, OUTPUT_DIR

sys.stdout.reconfigure(encoding="utf-8")
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
logger.add(LOG_DIR / "merge_final.log", level="DEBUG", encoding="utf-8")

FINAL = OUTPUT_DIR / "ri-db.restaurants_id.final.json"
# merge onto the previous FINAL when it exists, so successive passes accumulate
# instead of discarding what an earlier pass already resolved
CLEANED = FINAL if FINAL.exists() else OUTPUT_DIR / "ri-db.restaurants_id.cleaned.json"
RESOLVED = OUTPUT_DIR / "manual_resolved.json"

CITIES = {"dubai", "abu dhabi", "sharjah", "ajman", "uae", "fujairah",
          "umm al quwain", "ras al khaimah", "ras al-khaimah", "al ain",
          "united arab emirates", "ae"}
ZONE = re.compile(r"^(?:(zone|sector|district|plot|block|phase)\s*\d+[a-z]?|"
                  r"[a-z]{1,3}\s?\d{1,3}[a-z]?|\d{1,4})$", re.I)
NON_AREA = re.compile(r"^(police college|food court|unnamed road|level \d+|"
                      r"outlet mall|town cent(re|er))$", re.I)


def valid_area(a):
    if not a or not isinstance(a, str):
        return None
    a = a.strip(" ,-")
    if not a or len(a) < 3:
        return None
    if a.lower() in CITIES or ZONE.match(a) or NON_AREA.match(a):
        return None
    return a


def main():
    records = orjson.loads(CLEANED.read_bytes())
    resolved = orjson.loads(RESOLVED.read_bytes())
    logger.info(f"cleaned {len(records):,} records | manual-stage results {len(resolved):,}")

    by_id, rejected = {}, []
    for r in resolved:
        v = valid_area(r.get("area_resolved"))
        if v:
            by_id[r["_id"]] = (v, r.get("method"), r.get("confidence"),
                               r.get("evidence_url"))
        elif r.get("area_resolved"):
            rejected.append(r)
    logger.info(f"accepted {len(by_id):,} | rejected by validation {len(rejected):,}")
    for r in rejected[:8]:
        logger.warning(f"  rejected {r['area_resolved']!r} ({r['name'][:26]})")

    applied = 0
    provenance = []
    for rec in records:
        rid = (rec.get("_id") or {}).get("$oid")
        if rid in by_id:
            area, method, conf, url = by_id[rid]
            loc = rec.get("location") or {}
            before = loc.get("area")
            loc["area"] = area
            applied += 1
            provenance.append({"_id": rid, "source_id": rec["source_id"],
                               "name": rec["name"], "area_before": before,
                               "area_after": area, "method": method,
                               "confidence": conf, "evidence_url": url})

    out = OUTPUT_DIR / "ri-db.restaurants_id.final.json"
    out.write_bytes(orjson.dumps(records, option=orjson.OPT_INDENT_2))

    prov_path = OUTPUT_DIR / "manual_stage_provenance.csv"
    existing = []
    if prov_path.exists():
        with open(prov_path, encoding="utf-8-sig", newline="") as f:
            existing = [r for r in csv.DictReader(f)]
    merged = {r["_id"]: r for r in existing}
    for r in provenance:
        merged[r["_id"]] = {k: ("" if v is None else v) for k, v in r.items()}
    with open(prov_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(provenance[0].keys()))
        w.writeheader()
        w.writerows(merged.values())

    # Remaining = the original manual set minus everything resolved across ALL
    # passes (from the accumulated provenance), not just this one - otherwise a
    # second pass reports earlier successes as still outstanding.
    all_resolved_ids = set(merged)
    manual = orjson.loads((OUTPUT_DIR / "ri-db.restaurants_id.manual.json").read_bytes())
    remaining = [m for m in manual
                 if (m.get("_id") or {}).get("$oid") not in all_resolved_ids]
    (OUTPUT_DIR / "ri-db.restaurants_id.manual.final.json").write_bytes(
        orjson.dumps(remaining, option=orjson.OPT_INDENT_2))
    with open(OUTPUT_DIR / "manual_review_final.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["source_id", "name", "chain_id", "city", "area_current",
                    "sublocality", "_id", "mordor_restaurant_id"])
        for m in remaining:
            l = m.get("location") or {}
            w.writerow([m["source_id"], m["name"], m.get("chain_id"), l.get("city"),
                        l.get("area"), l.get("sublocality"),
                        (m.get("_id") or {}).get("$oid"),
                        (m.get("mordor_restaurant_id") or {}).get("$oid")])

    # integrity
    original = orjson.loads(INPUT_JSON.read_bytes())
    assert len(records) == len(original) == 17164, "ROW COUNT CHANGED"
    assert len({(r["_id"])["$oid"] for r in records}) == 17164, "DUPLICATE _id"
    for a, b in zip(original, records):
        assert a["_id"] == b["_id"], "ORDER CHANGED"
        assert list((a.get("location") or {}).keys()) == \
               list((b.get("location") or {}).keys()), "KEYS CHANGED"

    logger.success(f"applied {applied:,} newly-resolved areas -> {out.name}")
    logger.info(f"still needing a human: {len(remaining):,}")
    logger.success("integrity OK: 17,164 rows, unique _id, order + key set unchanged")


if __name__ == "__main__":
    main()
