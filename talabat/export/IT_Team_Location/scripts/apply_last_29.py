"""Apply the final manual answers, then re-run every consistency rule so any
variant the new answers introduce is folded in too (e.g. 'Trade Centre 1'
arriving next to an existing 'Trade Center 2').

Run: python apply_last_29.py
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
logger.add(LOG_DIR / "apply_last_29.log", level="DEBUG", encoding="utf-8")

FINAL = OUTPUT_DIR / "ri-db.restaurants_id.final.json"
CSV_IN = OUTPUT_DIR / "manual_review_remaining.csv"

ORDINALS = {"first": "1", "second": "2", "third": "3", "fourth": "4",
            "fifth": "5", "sixth": "6", "seventh": "7", "eighth": "8"}
ORD_RX = re.compile(r"\b(" + "|".join(ORDINALS) + r")\b", re.I)
# UAE areas that correctly take NO article
NO_ARTICLE = {"saadiyat island", "meydan", "mirdif", "business bay", "jumeirah",
              "barsha heights", "dubai marina", "zayed sports city"}


def to_digits(s):
    return ORD_RX.sub(lambda m: ORDINALS[m.group(1).lower()], s)


def main():
    records = orjson.loads(FINAL.read_bytes())
    current = {r["_id"]["$oid"]: ((r["location"] or {}).get("area") or "").strip()
               for r in records}

    # ---- 1. apply manual answers ----
    manual, unchanged = {}, 0
    with open(CSV_IN, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rid = (row.get("_id") or "").strip()
            val = (row.get("area_current") or "").strip()
            if not rid or not val or val == current.get(rid, ""):
                unchanged += 1
                continue
            manual[rid] = val

    applied = 0
    for rec in records:
        rid = rec["_id"]["$oid"]
        if rid in manual:
            rec["location"]["area"] = manual[rid]
            applied += 1
    logger.success(f"applied {applied} manual answers ({unchanged} left as-is)")

    # ---- 2. re-run consistency rules over the whole file ----
    counts = Counter((r["location"] or {}).get("area") or "" for r in records)
    counts.pop("", None)
    before_n = len(counts)

    # 2a. ordinal words -> digits, grouped so the numeric form wins
    groups = defaultdict(list)
    for area, n in counts.items():
        groups[re.sub(r"[^a-z0-9]", "", to_digits(area).lower())].append((area, n))
    canon = {}
    for key, variants in groups.items():
        variants.sort(key=lambda t: -t[1])
        digit = [v for v in variants if re.search(r"\b\d+\b", v[0])]
        target = digit[0][0] if digit else to_digits(variants[0][0])
        for a, _ in variants:
            canon[a] = target

    # 2b. 'Al ' prefix where a bare/Al pair exists (never for NO_ARTICLE names)
    tmp = Counter(canon.get(a, a) for a in counts for _ in range(counts[a]))
    by_stripped = defaultdict(list)
    for area, n in tmp.items():
        by_stripped[re.sub(r"^al\s+", "", area.lower()).strip()].append((area, n))
    for key, variants in by_stripped.items():
        if len(variants) < 2 or key in NO_ARTICLE:
            continue
        al = [v for v in variants if v[0].lower().startswith("al ")]
        bare = [v for v in variants if not v[0].lower().startswith("al ")]
        if al and bare:
            target = max(al, key=lambda t: t[1])[0]
            for a, _ in bare:
                for src, dst in list(canon.items()):
                    if dst == a:
                        canon[src] = target
                canon[a] = target

    # 2c. case/punctuation twins -> most frequent surface form
    tmp2 = Counter()
    for a, n in counts.items():
        tmp2[canon.get(a, a)] += n
    keyed = {}
    for area, n in tmp2.most_common():
        keyed.setdefault(re.sub(r"[^a-z0-9]", "", area.lower()), area)
    for a in list(canon):
        t = canon[a]
        best = keyed.get(re.sub(r"[^a-z0-9]", "", t.lower()), t)
        canon[a] = best

    changed = Counter()
    for rec in records:
        loc = rec["location"]
        a = loc.get("area")
        if a and canon.get(a) and canon[a] != a:
            changed[(a, canon[a])] += 1
            loc["area"] = canon[a]

    shutil.copy2(FINAL, FINAL.with_suffix(".json.bak6"))
    FINAL.write_bytes(orjson.dumps(records, option=orjson.OPT_INDENT_2))

    after = Counter((r["location"] or {}).get("area") or "" for r in records)
    after.pop("", None)
    if changed:
        logger.info(f"consistency pass renamed {sum(changed.values())} rows:")
        for (a, b), n in changed.most_common(10):
            logger.info(f"    {a[:34]:36} -> {b[:30]:32} {n:>4}")
    logger.success(f"distinct areas {before_n:,} -> {len(after):,}")

    # ---- integrity ----
    original = orjson.loads(INPUT_JSON.read_bytes())
    assert len(records) == len(original) == 17164, "ROW COUNT CHANGED"
    assert len({r["_id"]["$oid"] for r in records}) == 17164, "DUPLICATE _id"
    for a, b in zip(original, records):
        assert a["_id"] == b["_id"], "ORDER CHANGED"
        assert list((a.get("location") or {}).keys()) == \
               list((b.get("location") or {}).keys()), "KEYS CHANGED"
    filled = sum(1 for r in records if (r["location"] or {}).get("area"))
    logger.success("integrity OK: 17,164 rows, unique _id, order + key set unchanged")
    logger.info(f"area populated: {filled:,}/{len(records):,}")


if __name__ == "__main__":
    main()
