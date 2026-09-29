"""Merge genuine SPELLING variants of the same area.

Deliberately conservative and explicit. Fuzzy similarity is NOT used to decide
merges here: 'Al Barsha 1' and 'Al Barsha 3' score 91% but are different
places, as are 'Jumeirah 1'/'Jumeirah 3' and 'Khalifa City'/'Khalifa City A'.
Only the rules below are applied, each one a spelling of the SAME place with
the more frequent form winning.

Run after apply_manual_and_canonicalize.py:
    python normalize_spellings.py
"""
import csv
import re
import shutil
import sys
from collections import Counter

import orjson
from loguru import logger

from config import INPUT_JSON, LOG_DIR, OUTPUT_DIR

sys.stdout.reconfigure(encoding="utf-8")
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
logger.add(LOG_DIR / "normalize_spellings.log", level="DEBUG", encoding="utf-8")

FINAL = OUTPUT_DIR / "ri-db.restaurants_id.final.json"

# (pattern, replacement, why) - order matters
RULES = [
    (r"\bAl\s+Qouz\b", "Al Quoz", "Qouz is a misspelling of Quoz"),
    (r"\bInd\.\s*(\d)", r"Industrial Area \1", "expand Ind. abbreviation"),
    (r"\bInd\.?\s+(\d)", r"Industrial Area \1", "expand Ind abbreviation"),
    (r"\bInd\.(?!\s*\d)", "Industrial Area", "expand trailing Ind."),
    (r"\bAl\s+Jadaf\b", "Al Jaddaf", "single-d spelling variant"),
    (r"\bAl\s+Khalidiya\b(?!h)", "Al Khalidiyah", "missing trailing h"),
    (r"\bAl\s+Barsha\s+Heights\b", "Barsha Heights", "Barsha Heights has no Al"),
    (r"\bZabeel\b", "Za'abeel", "apostrophe-less variant"),
    (r"\bAl\s+Nahdah\b", "Al Nahda", "trailing h variant"),
    (r"^Nahda$", "Al Nahda", "missing Al prefix"),
    (r"\bAl\s+Sufouh\b", "Al Sufouh", "canonical"),
    (r"\s{2,}", " ", "collapse double spaces"),
]


def normalize(area):
    out = area
    for pat, rep, _ in RULES:
        out = re.sub(pat, rep, out, flags=re.I if pat.startswith(r"\b") else 0)
    return out.strip(" ,-")


def main():
    records = orjson.loads(FINAL.read_bytes())
    before = Counter((r["location"] or {}).get("area") or "" for r in records)
    before.pop("", None)
    logger.info(f"{len(records):,} records | {len(before):,} distinct areas")

    changes = Counter()
    for rec in records:
        loc = rec.get("location") or {}
        a = loc.get("area")
        if not a:
            continue
        n = normalize(a)
        if n != a:
            loc["area"] = n
            changes[(a, n)] += 1

    # after rewriting, fold any case-only twins onto the most frequent form
    counts = Counter((r["location"] or {}).get("area") or "" for r in records)
    counts.pop("", None)
    by_key = {}
    for area, n in counts.most_common():
        by_key.setdefault(re.sub(r"[^a-z0-9]", "", area.lower()), area)
    case_fixes = 0
    for rec in records:
        loc = rec.get("location") or {}
        a = loc.get("area")
        if not a:
            continue
        target = by_key.get(re.sub(r"[^a-z0-9]", "", a.lower()))
        if target and target != a:
            loc["area"] = target
            changes[(a, target)] += 1
            case_fixes += 1

    shutil.copy2(FINAL, FINAL.with_suffix(".json.bak2"))
    FINAL.write_bytes(orjson.dumps(records, option=orjson.OPT_INDENT_2))

    after = Counter((r["location"] or {}).get("area") or "" for r in records)
    after.pop("", None)

    with open(OUTPUT_DIR / "spelling_normalization_map.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["from", "to", "rows"])
        for (a, b), n in sorted(changes.items(), key=lambda t: -t[1]):
            w.writerow([a, b, n])

    logger.success(f"{sum(changes.values()):,} rows renamed "
                   f"({case_fixes} of them case-only)")
    logger.success(f"distinct areas {len(before):,} -> {len(after):,}")
    for (a, b), n in sorted(changes.items(), key=lambda t: -t[1])[:14]:
        logger.info(f"    {a[:36]:38} -> {b[:32]:34} {n:>5}")

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
