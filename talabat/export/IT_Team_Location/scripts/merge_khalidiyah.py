"""Sagar-confirmed merges + a scan for the same pattern elsewhere.

Confirmed by Sagar:
    Khalidiyah, Khalidiya, Al Khalidiyya, Al Khalidiyah District -> Al Khalidiyah
    Al Khalidiyah West stays separate (a genuinely different sub-area)

'District' is folded only for Khalidiyah because Sagar confirmed that specific
case; it is NOT applied as a blanket rule, since elsewhere a qualifier can mark
a real sub-area (as 'West' does here).

Run: python merge_khalidiyah.py
"""
import csv
import re
import shutil
import sys
from collections import Counter, defaultdict

import orjson
from loguru import logger

from config import INPUT_JSON, LOG_DIR, OUTPUT_DIR

sys.stdout.reconfigure(encoding="utf-8")
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
logger.add(LOG_DIR / "merge_khalidiyah.log", level="DEBUG", encoding="utf-8")

FINAL = OUTPUT_DIR / "ri-db.restaurants_id.final.json"

MERGES = {
    "khalidiyah": "Al Khalidiyah",
    "khalidiya": "Al Khalidiyah",
    "al khalidiyya": "Al Khalidiyah",
    "al khalidiyah district": "Al Khalidiyah",
    "al khalidiya district": "Al Khalidiyah",
}
KEEP_SEPARATE = {"al khalidiyah west"}


def main():
    records = orjson.loads(FINAL.read_bytes())
    before = Counter((r["location"] or {}).get("area") or "" for r in records)

    changed = Counter()
    for rec in records:
        loc = rec.get("location") or {}
        a = (loc.get("area") or "").strip()
        if not a:
            continue
        low = a.lower()
        if low in KEEP_SEPARATE:
            continue
        if low in MERGES:
            loc["area"] = MERGES[low]
            changed[(a, MERGES[low])] += 1

    shutil.copy2(FINAL, FINAL.with_suffix(".json.bak3"))
    FINAL.write_bytes(orjson.dumps(records, option=orjson.OPT_INDENT_2))

    after = Counter((r["location"] or {}).get("area") or "" for r in records)
    logger.success(f"{sum(changed.values())} rows merged")
    for (a, b), n in changed.most_common():
        logger.info(f"    {a:32} -> {b:20} {n:>4}")
    logger.info(f"distinct areas {len([a for a in before if a]):,} -> "
                f"{len([a for a in after if a]):,}")
    logger.info("Khalidiyah family now: " +
                str({a: n for a, n in after.items() if a and "khalidiy" in a.lower()}))

    with open(OUTPUT_DIR / "khalidiyah_merge_map.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["from", "to", "rows"])
        for (a, b), n in changed.most_common():
            w.writerow([a, b, n])

    # ---- scan: same "bare name vs Al-prefixed" pattern elsewhere ----
    logger.info("")
    logger.info("SCAN - other places where a bare name and an 'Al ' form coexist:")
    by_stripped = defaultdict(list)
    for a, n in after.items():
        if not a:
            continue
        key = re.sub(r"^al\s+", "", a.lower()).strip()
        by_stripped[key].append((a, n))
    found = 0
    for key, variants in sorted(by_stripped.items(), key=lambda t: -sum(n for _, n in t[1])):
        if len(variants) > 1 and len({v[0].lower() for v in variants}) > 1:
            logger.warning("    " + " | ".join(f"{a} ({n})" for a, n in
                                               sorted(variants, key=lambda t: -t[1])))
            found += 1
            if found >= 12:
                break
    if not found:
        logger.success("    none found")

    original = orjson.loads(INPUT_JSON.read_bytes())
    assert len(records) == len(original) == 17164, "ROW COUNT CHANGED"
    assert len({(r["_id"])["$oid"] for r in records}) == 17164, "DUPLICATE _id"
    for a, b in zip(original, records):
        assert a["_id"] == b["_id"], "ORDER CHANGED"
        assert list((a.get("location") or {}).keys()) == \
               list((b.get("location") or {}).keys()), "KEYS CHANGED"
    logger.success("integrity OK: 17,164 rows, unique _id, order + key set unchanged")


if __name__ == "__main__":
    main()
