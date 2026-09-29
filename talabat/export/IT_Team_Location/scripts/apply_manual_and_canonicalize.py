"""Final pass: apply Sagar's manual answers, then canonicalise area spellings.

1. MANUAL - read manual_review_final.csv and take any row whose `area_current`
   differs from the original as a human-supplied answer. Rows left untouched
   stay unresolved rather than being guessed at.

2. CANONICALISE - the same place is written several ways
   ('Al Barsha 1' / 'Al Barsha First'). Group by a key that folds ordinal words
   to digits and ignores case/punctuation, then elect ONE surviving form:
     * if any variant in the group uses DIGITS, the digit form wins
       (Sagar's rule: keep the numbered version)
     * otherwise the most frequent spelling wins - frequency, not title-case,
       so acronyms like JAFZA and DIFC survive intact
   When a group has no digit variant on record, one is generated from the most
   frequent spelling by swapping the ordinal word for its digit.

Writes ri-db.restaurants_id.final.json in place (after a .bak) and emits a full
mapping CSV so every rename is auditable.

Run: python apply_manual_and_canonicalize.py
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
logger.add(LOG_DIR / "final_pass.log", level="DEBUG", encoding="utf-8")

FINAL = OUTPUT_DIR / "ri-db.restaurants_id.final.json"
MANUAL_CSV = OUTPUT_DIR / "manual_review_final.csv"
MANUAL_SRC = OUTPUT_DIR / "ri-db.restaurants_id.manual.final.json"

ORDINALS = {"first": "1", "second": "2", "third": "3", "fourth": "4",
            "fifth": "5", "sixth": "6", "seventh": "7", "eighth": "8",
            "ninth": "9", "tenth": "10"}
ORD_RX = re.compile(r"\b(" + "|".join(ORDINALS) + r")\b", re.I)

CITIES = {"dubai", "abu dhabi", "sharjah", "ajman", "uae", "fujairah",
          "umm al quwain", "ras al khaimah", "al ain", "united arab emirates"}
JUNK = re.compile(r"^(unnamed road|capital mall|sector\s*-?\s*\w+|\d+\s*-\s*zone\s*\d+|"
                  r"[a-z0-9]{4}\+[a-z0-9]{2,4}.*|level \d+.*|balcony.*|ground floor.*|"
                  r"shop no.*|unit no.*|adnoc station.*|police college.*)$", re.I)


def group_key(area):
    s = ORD_RX.sub(lambda m: ORDINALS[m.group(1).lower()], area.lower())
    return re.sub(r"[^a-z0-9]", "", s)


def to_digits(area):
    return ORD_RX.sub(lambda m: ORDINALS[m.group(1).lower()], area)


def has_digit_token(area):
    return bool(re.search(r"\b\d+\b", area))


def main():
    records = orjson.loads(FINAL.read_bytes())
    logger.info(f"loaded {len(records):,} records")

    # ---------------- 1. manual answers ----------------
    original_area = {}
    for o in orjson.loads(MANUAL_SRC.read_bytes()):
        original_area[o["_id"]["$oid"]] = ((o.get("location") or {}).get("area") or "").strip()

    manual, skipped = {}, []
    with open(MANUAL_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rid = (row.get("_id") or "").strip()
            val = (row.get("area_current") or "").strip()
            was = original_area.get(rid, "")
            if not rid or val == was:
                skipped.append(row)
                continue
            if val.lower() in CITIES or JUNK.match(val) or len(val) < 3:
                skipped.append(row)
                logger.warning(f"  manual value rejected by validation: {val!r} "
                               f"({row.get('name','')[:24]})")
                continue
            manual[rid] = val

    logger.info(f"manual answers accepted {len(manual)} | left unresolved {len(skipped)}")

    applied = 0
    for rec in records:
        rid = (rec.get("_id") or {}).get("$oid")
        if rid in manual:
            rec["location"]["area"] = manual[rid]
            applied += 1
    logger.success(f"applied {applied} manual answers")

    # ---------------- 2. canonicalise ----------------
    counts = Counter((r["location"] or {}).get("area") or "" for r in records)
    counts.pop("", None)
    groups = defaultdict(list)
    for area, n in counts.items():
        groups[group_key(area)].append((area, n))

    canon = {}
    renames = []
    for key, variants in groups.items():
        if len(variants) == 1:
            canon[variants[0][0]] = variants[0][0]
            continue
        variants.sort(key=lambda t: -t[1])
        digit_forms = [v for v in variants if has_digit_token(v[0])]
        if digit_forms:
            target = digit_forms[0][0]          # most frequent digit spelling
        else:
            most = variants[0][0]
            target = to_digits(most)            # synthesise if only words exist
        for area, n in variants:
            canon[area] = target
            if area != target:
                renames.append({"from": area, "to": target, "rows": n})

    changed = 0
    for rec in records:
        loc = rec.get("location") or {}
        a = loc.get("area")
        if a and canon.get(a) and canon[a] != a:
            loc["area"] = canon[a]
            changed += 1

    # a lone ordinal with no numeric sibling still becomes a digit form
    extra = 0
    for rec in records:
        loc = rec.get("location") or {}
        a = loc.get("area")
        if a and ORD_RX.search(a):
            new = to_digits(a)
            if new != a:
                loc["area"] = new
                renames.append({"from": a, "to": new, "rows": 1})
                extra += 1

    shutil.copy2(FINAL, FINAL.with_suffix(".json.bak"))
    FINAL.write_bytes(orjson.dumps(records, option=orjson.OPT_INDENT_2))

    agg = defaultdict(int)
    for r in renames:
        agg[(r["from"], r["to"])] += r["rows"]
    with open(OUTPUT_DIR / "area_canonicalization_map.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["from", "to", "rows"])
        for (a, b), n in sorted(agg.items(), key=lambda t: -t[1]):
            w.writerow([a, b, n])

    after = Counter((r["location"] or {}).get("area") or "" for r in records)
    after.pop("", None)
    logger.success(f"canonicalised: {changed + extra:,} rows renamed, "
                   f"{len(counts):,} -> {len(after):,} distinct areas")

    # ---------------- integrity ----------------
    original = orjson.loads(INPUT_JSON.read_bytes())
    assert len(records) == len(original) == 17164, "ROW COUNT CHANGED"
    assert len({(r["_id"])["$oid"] for r in records}) == 17164, "DUPLICATE _id"
    for a, b in zip(original, records):
        assert a["_id"] == b["_id"], "ORDER CHANGED"
        assert list((a.get("location") or {}).keys()) == \
               list((b.get("location") or {}).keys()), "KEYS CHANGED"
    filled = sum(1 for r in records if (r["location"] or {}).get("area"))
    logger.success("integrity OK: 17,164 rows, unique _id, order + key set unchanged")
    logger.info(f"area populated: {filled:,}/{len(records):,} "
                f"({filled/len(records)*100:.2f}%)")

    # remaining unresolved list
    still_ids = {(r.get("_id") or "").strip() for r in skipped}
    rem = [r for r in records if (r["_id"]["$oid"]) in still_ids]
    with open(OUTPUT_DIR / "manual_review_remaining.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["source_id", "name", "city", "area_current", "sublocality", "_id"])
        for r in rem:
            l = r["location"]
            w.writerow([r["source_id"], r["name"], l.get("city"), l.get("area"),
                        l.get("sublocality"), r["_id"]["$oid"]])
    logger.info(f"still unresolved -> manual_review_remaining.csv ({len(rem)} rows)")


if __name__ == "__main__":
    main()
