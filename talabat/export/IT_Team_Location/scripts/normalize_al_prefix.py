"""Add the 'Al ' prefix wherever a bare form and an 'Al ' form denote the same area.

Sagar's rule: 'Al' must be present. Applied by MAJORITY-INDEPENDENT logic - even
where the bare form is more common ('Qusais' 47 vs 'Al Qusais' 20), the 'Al '
form wins, so the dataset ends up with one consistent convention.

IMPORTANT - only pairs where BOTH forms already exist in the data are merged.
A bare name with no 'Al ' twin is left alone: many UAE areas legitimately have
no article (Mirdif, Business Bay, Jumeirah, Barsha Heights, Dubai Marina), and
blindly prefixing those would invent names that do not exist.

Run: python normalize_al_prefix.py
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
logger.add(LOG_DIR / "al_prefix.log", level="DEBUG", encoding="utf-8")

FINAL = OUTPUT_DIR / "ri-db.restaurants_id.final.json"


def main():
    records = orjson.loads(FINAL.read_bytes())
    counts = Counter((r["location"] or {}).get("area") or "" for r in records)
    counts.pop("", None)
    logger.info(f"{len(records):,} records | {len(counts):,} distinct areas")

    # group by the name with any leading 'Al ' stripped
    groups = defaultdict(list)
    for area, n in counts.items():
        groups[re.sub(r"^al\s+", "", area.lower()).strip()].append((area, n))

    mapping = {}
    for key, variants in groups.items():
        if len(variants) < 2:
            continue
        al_forms = [v for v in variants if v[0].lower().startswith("al ")]
        bare = [v for v in variants if not v[0].lower().startswith("al ")]
        if not al_forms or not bare:
            continue
        # prefer the most frequent 'Al ' spelling as the target
        target = max(al_forms, key=lambda t: t[1])[0]
        for area, n in bare:
            mapping[area] = target

    changed = Counter()
    for rec in records:
        loc = rec.get("location") or {}
        a = loc.get("area")
        if a and a in mapping:
            loc["area"] = mapping[a]
            changed[(a, mapping[a])] += 1

    shutil.copy2(FINAL, FINAL.with_suffix(".json.bak4"))
    FINAL.write_bytes(orjson.dumps(records, option=orjson.OPT_INDENT_2))

    after = Counter((r["location"] or {}).get("area") or "" for r in records)
    after.pop("", None)

    with open(OUTPUT_DIR / "al_prefix_map.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["from", "to", "rows"])
        for (a, b), n in changed.most_common():
            w.writerow([a, b, n])

    logger.success(f"{sum(changed.values())} rows renamed across "
                   f"{len(changed)} variant pairs")
    for (a, b), n in changed.most_common(20):
        logger.info(f"    {a:26} -> {b:26} {n:>4}")
    logger.success(f"distinct areas {len(counts):,} -> {len(after):,}")

    # prove no bare/Al twins survive
    leftover = defaultdict(list)
    for area in after:
        leftover[re.sub(r"^al\s+", "", area.lower()).strip()].append(area)
    dupes = {k: v for k, v in leftover.items() if len(v) > 1}
    logger.info(f"remaining bare/Al twin groups: {len(dupes)}  {list(dupes.values())[:4]}")

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
