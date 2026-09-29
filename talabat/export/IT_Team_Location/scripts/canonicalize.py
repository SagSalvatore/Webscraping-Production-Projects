"""Post-pass: collapse casing/punctuation variants of the SAME area.

The rules and the LLM both emit whatever surface form the source used, so the
same place can appear as 'Al Barsha 1', 'Al barsha 1' and 'AL BARSHA 1'. This
groups by a case/punct-insensitive key and elects the most frequent surface
form as canonical - frequency, not a casing rule, so genuine acronyms (DIFC,
IMPZ, DAMAC) keep their real form instead of being title-cased into 'Difc'.

Run after run_clean.py:
    python canonicalize.py
"""
import csv
import re
import sys
from collections import Counter, defaultdict

import orjson
from loguru import logger

from config import LOG_DIR, OUTPUT_DIR

sys.stdout.reconfigure(encoding="utf-8")
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
logger.add(LOG_DIR / "canonicalize.log", level="DEBUG", encoding="utf-8")

SRC = OUTPUT_DIR / "area_cleaned.json"
OUT_J = OUTPUT_DIR / "area_cleaned_final.json"
OUT_C = OUTPUT_DIR / "area_cleaned_final.csv"
MAP_C = OUTPUT_DIR / "canonicalization_map.csv"


def norm_key(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def main():
    rows = orjson.loads(SRC.read_bytes())
    logger.info(f"loaded {len(rows):,} rows from {SRC.name}")

    counts = Counter(r["area_2"] for r in rows if r["area_2"])
    groups = defaultdict(list)
    for value, n in counts.items():
        groups[norm_key(value)].append((value, n))

    canon = {}
    collapsed = 0
    for key, variants in groups.items():
        # most frequent surface form wins (keeps DIFC / DAMAC / IMPZ intact)
        best = max(variants, key=lambda t: (t[1], t[0]))[0]
        for value, _ in variants:
            canon[value] = best
            if value != best:
                collapsed += 1

    changed = 0
    for r in rows:
        if r["area_2"]:
            new = canon[r["area_2"]]
            if new != r["area_2"]:
                r["area_2_raw"] = r["area_2"]
                r["area_2"] = new
                changed += 1

    OUT_J.write_bytes(orjson.dumps(rows, option=orjson.OPT_INDENT_2))
    fields = ["_id", "mordor_restaurant_id", "source_id", "name", "chain_id",
              "city", "sublocality", "area_original", "area_2", "method",
              "confidence"]
    with open(OUT_C, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    with open(MAP_C, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["variant", "canonical", "rows"])
        for value, n in sorted(counts.items(), key=lambda t: -t[1]):
            if canon[value] != value:
                w.writerow([value, canon[value], n])

    after = len({r["area_2"] for r in rows if r["area_2"]})
    logger.success(f"distinct area_2: {len(counts):,} -> {after:,} "
                   f"({collapsed} variants collapsed, {changed:,} rows updated)")
    logger.info(f"wrote {OUT_J.name}, {OUT_C.name}, {MAP_C.name}")

    filled = sum(1 for r in rows if r["area_2"])
    logger.info(f"coverage: {filled:,}/{len(rows):,} ({filled/len(rows)*100:.1f}%)")
    assert len(rows) == 17164, "ROW COUNT CHANGED - abort"
    assert len({r["_id"] for r in rows}) == len(rows), "DUPLICATE _id - abort"
    logger.success("integrity checks passed (row count + unique _id)")


if __name__ == "__main__":
    main()
