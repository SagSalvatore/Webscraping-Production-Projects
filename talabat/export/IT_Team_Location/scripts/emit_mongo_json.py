"""Emit the cleaned data in the SAME shape as ri-db.restaurants_id.json.

The cleaned value OVERWRITES `location.area` - IT's backend reads that key
only, so no `area_2` is emitted. Key order, key names and the MongoDB
extended-JSON form ({"$oid": ...}) are otherwise untouched.

The source file ri-db.restaurants_id.json is never modified, so the original
values remain recoverable there; `*.enriched.json` also carries the original
alongside the cleaning provenance for auditing.

For the 644 rows with no confident result, `area` KEEPS ITS ORIGINAL VALUE
rather than being nulled - blanking it would destroy the only location signal
those records have. Flip KEEP_ORIGINAL_WHEN_UNRESOLVED to write null instead.

Outputs (all indent=2):
  ri-db.restaurants_id.cleaned.json   all 17,164 records, area overwritten
  ri-db.restaurants_id.manual.json    the subset with no confident area
  ri-db.restaurants_id.enriched.json  same + _cleaning{original, method, confidence}

Run:  python emit_mongo_json.py
"""

KEEP_ORIGINAL_WHEN_UNRESOLVED = True
import sys

import orjson
from loguru import logger

from config import INPUT_JSON, LOG_DIR, OUTPUT_DIR

sys.stdout.reconfigure(encoding="utf-8")
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
logger.add(LOG_DIR / "emit_mongo_json.log", level="DEBUG", encoding="utf-8")

CLEANED = OUTPUT_DIR / "area_cleaned_final.json"


def main():
    original = orjson.loads(INPUT_JSON.read_bytes())
    cleaned = orjson.loads(CLEANED.read_bytes())
    logger.info(f"original {len(original):,} records | cleaned {len(cleaned):,} rows")

    # key on _id - unique across all 17,164, verified earlier
    by_id = {r["_id"]: r for r in cleaned}
    if len(by_id) != len(cleaned):
        raise SystemExit("duplicate _id in the cleaned file - abort")

    out_all, out_manual, out_rich = [], [], []
    missing = 0

    for rec in original:
        oid = (rec.get("_id") or {}).get("$oid")
        c = by_id.get(oid)
        if c is None:
            missing += 1
            area_2, method, conf = None, "not_processed", 0.0
        else:
            area_2, method, conf = c["area_2"], c["method"], c["confidence"]

        # overwrite `area` in place - same key, same position, no new keys
        loc = rec.get("location") or {}
        original_area = loc.get("area")
        if area_2:
            final_area = area_2
        else:
            final_area = original_area if KEEP_ORIGINAL_WHEN_UNRESOLVED else None
        new_loc = {k: (final_area if k == "area" else v) for k, v in loc.items()}

        new_rec = {k: (new_loc if k == "location" else v) for k, v in rec.items()}
        out_all.append(new_rec)

        rich = dict(new_rec)
        rich["_cleaning"] = {"area_original": original_area,
                             "method": method, "confidence": conf,
                             "resolved": bool(area_2)}
        out_rich.append(rich)

        if not area_2:
            out_manual.append(new_rec)

    if missing:
        logger.warning(f"{missing} records had no cleaned counterpart")

    targets = [
        (OUTPUT_DIR / "ri-db.restaurants_id.cleaned.json", out_all),
        (OUTPUT_DIR / "ri-db.restaurants_id.manual.json", out_manual),
        (OUTPUT_DIR / "ri-db.restaurants_id.enriched.json", out_rich),
    ]
    for path, payload in targets:
        path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
        mb = path.stat().st_size / 1024 / 1024
        logger.success(f"{path.name:38} {len(payload):>6,} records  {mb:6.1f} MB")

    # integrity
    assert len(out_all) == len(original) == 17164, "ROW COUNT CHANGED"
    assert len({(r['_id'])['$oid'] for r in out_all}) == 17164, "DUPLICATE _id"
    for a, b in zip(original, out_all):
        assert a["_id"] == b["_id"] and a["source_id"] == b["source_id"], "ORDER CHANGED"
        assert list((a.get("location") or {}).keys()) == \
               list((b.get("location") or {}).keys()), "LOCATION KEYS CHANGED"
    assert not any("area_2" in (r.get("location") or {}) for r in out_all), \
        "area_2 leaked into the output"
    changed = sum(1 for a, b in zip(original, out_all)
                  if (a.get("location") or {}).get("area")
                  != (b.get("location") or {}).get("area"))
    logger.success("integrity OK: row count, _id uniqueness, record order, "
                   "identical key set, no area_2 key")
    logger.info(f"`area` rewritten on {changed:,}/{len(out_all):,} records "
                f"({changed/len(out_all)*100:.1f}%)")
    logger.info(f"unresolved kept as-is: {len(out_manual):,}"
                if KEEP_ORIGINAL_WHEN_UNRESOLVED else
                f"unresolved set to null: {len(out_manual):,}")


if __name__ == "__main__":
    main()
