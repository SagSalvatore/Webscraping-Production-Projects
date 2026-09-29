"""Step 6 - populate item_key_clean / menu_category_clean / description_clean
for this month's rows.

REUSES the proven cleaners from menu/clean_menu_data.py by importing them -
they are not copied here. Divergent copies of these rules would silently change
what "clean" means between months, and the whole point of the clean columns is
that they stay comparable.

  item_key        -> clean_item_key            (emoji, invisibles, Arabic, CJK)
  menu_category   -> clean_text                (emoji, invisibles, whitespace)
  description     -> extract_english_from_bilingual
                     ("English - Arabic" keeps the English half; otherwise
                      Arabic is stripped and, with --translate, the remaining
                      Arabic-only text goes to OpenAI)

Work is done on DISTINCT values, not on 1.25M rows - the same item name repeats
across thousands of branches.

    python sanitize_august.py --dry-run     # counts + translation cost
    python sanitize_august.py               # deterministic rules only
    python sanitize_august.py --translate   # also translate Arabic (costs $)
"""
import argparse
import asyncio
import io
import json
import re
import sys
from pathlib import Path

import psycopg2

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_PASSWORD  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "menu"))

from clean_menu_data import (          # noqa: E402  - proven rules, not re-implemented
    clean_item_key,
    clean_text,
    extract_english_from_bilingual,
    _ARABIC_DETECT,
)

DB = dict(host="localhost", port=5432, dbname="RestaurantIntelligence",
          user="postgres", password=PG_PASSWORD)

RUN_ID = "20260819_164725"
CACHE = HERE / "data" / "translation_cache.json"

sys.stdout.reconfigure(encoding="utf-8")

COLS = [
    ("item_key", "item_key_clean", clean_item_key),
    ("menu_category", "menu_category_clean", clean_text),
    ("description", "description_clean", extract_english_from_bilingual),
]


def write_back(cur, src_col, dst_col, mapping):
    """One UPDATE joined on the raw value - not 1.25M individual statements."""
    cur.execute(f"create temp table stg_{dst_col} (raw text, clean text) "
                "on commit drop")
    buf = io.StringIO()
    for raw, clean in mapping.items():
        if raw is None:
            continue
        r = raw.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", " ")
        c = (clean or "").replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", " ")
        buf.write(f"{r}\t{c if clean is not None else chr(92) + 'N'}\n")
    buf.seek(0)
    cur.copy_from(buf, f"stg_{dst_col}", null="\\N")
    cur.execute(f"create index on stg_{dst_col} (raw)")
    cur.execute(f"""
        update talabat_menu_items m set {dst_col} = s.clean
        from stg_{dst_col} s
        where m.run_id = %s and m.{src_col} = s.raw""", (RUN_ID,))
    return cur.rowcount


async def translate(texts, args):
    """Arabic descriptions -> English, cached on disk BY TEXT.

    Deliberately calls translate_batch directly instead of the higher-level
    translate_arabic_descriptions(): that one caches by integer ROW ID in
    menu/translations_cache.json, which already holds June's entries under those
    same ids. Passing our own 0..N indices would collide with them and return
    June's translation for unrelated text. Keying the cache on the text itself
    has no such failure mode and survives row-id churn between months.
    """
    import os
    from openai import AsyncOpenAI
    from clean_menu_data import TRANSLATE_BATCH, TRANSLATE_SEM, translate_batch

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    todo = [t for t in texts if t not in cache]
    print(f"    {len(texts):,} to translate, {len(todo):,} not cached")
    if not todo:
        return cache

    client = AsyncOpenAI(api_key=os.environ["OPEN_AI_API"])
    sem = asyncio.Semaphore(TRANSLATE_SEM)
    items = list(enumerate(todo))
    batches = [items[i:i + TRANSLATE_BATCH]
               for i in range(0, len(items), TRANSLATE_BATCH)]
    print(f"    {len(batches)} batches of {TRANSLATE_BATCH}")
    results = await asyncio.gather(*[translate_batch(client, sem, b)
                                     for b in batches])
    for batch, res in zip(batches, results):
        for idx, text in batch:
            if idx in res:
                cache[text] = res[idx]
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache


def main(args):
    cn = psycopg2.connect(**DB)
    cn.autocommit = False
    cur = cn.cursor()
    print("=" * 66)
    print(f"  SANITIZE  run_id={RUN_ID}")
    print("=" * 66)
    cur.execute("select count(*) from talabat_menu_items where run_id=%s", (RUN_ID,))
    print(f"  rows in this run: {cur.fetchone()[0]:,}")

    try:
        results = {}
        for src, dst, fn in COLS:
            cur.execute(f"select distinct {src} from talabat_menu_items "
                        "where run_id=%s", (RUN_ID,))
            vals = [r[0] for r in cur.fetchall()]
            mapping = {v: fn(v) for v in vals}
            changed = sum(1 for v in vals if (mapping[v] or "") != (v or ""))
            nulled = sum(1 for v in vals if v and not mapping[v])
            print(f"\n  {src:14} distinct {len(vals):>8,} | "
                  f"changed {changed:>7,} | emptied {nulled:,}")
            results[dst] = mapping

        # Which descriptions need the model?
        #
        # NOT just "Arabic survived the clean". The bigger group is the one the
        # deterministic rule EMPTIES: a pure-Arabic description has its Arabic
        # stripped and becomes NULL, so the content is destroyed rather than
        # translated. Those are the ones worth spending on - translate from the
        # RAW value, since the cleaned one is already empty.
        desc_map = results["description_clean"]
        needs_tx = sorted({
            raw for raw, clean in desc_map.items()
            if raw and _ARABIC_DETECT.search(raw)
            and (not clean or _ARABIC_DETECT.search(clean))
        })
        emptied = sum(1 for raw, clean in desc_map.items()
                      if raw and _ARABIC_DETECT.search(raw) and not clean)
        survived = len(needs_tx) - emptied
        print(f"\n  descriptions needing translation: {len(needs_tx):,}")
        print(f"    emptied by the strip (content lost) : {emptied:,}")
        print(f"    Arabic survived the clean           : {survived:,}")
        if needs_tx:
            toks = sum(len(t) for t in needs_tx) / 4
            print(f"    ~{toks:,.0f} input tokens -> roughly "
                  f"${toks/1e6*0.15 + toks/1e6*0.60:.2f} on gpt-4o-mini")

        if args.dry_run:
            cn.rollback()
            print("\n  --dry-run: nothing written")
            return 0

        if args.translate and needs_tx:
            print("\n  translating ...")
            cache = asyncio.run(translate(needs_tx, args))
            hit = 0
            for raw in needs_tx:
                got = cache.get(raw)
                if got and got != "[SKIP]":
                    desc_map[raw] = clean_text(got)
                    hit += 1
            print(f"    translated {hit:,}/{len(needs_tx):,}")

        print()
        for src, dst, _ in COLS:
            n = write_back(cur, src, dst, results[dst])
            print(f"  {dst:22} updated {n:>9,} rows")

        for _, dst, _ in COLS:
            cur.execute(f"select count(*) from talabat_menu_items "
                        f"where run_id=%s and {dst} is null", (RUN_ID,))
            print(f"    {dst:22} still NULL: {cur.fetchone()[0]:,}")

        cn.commit()
        print("\n  COMMITTED")
        return 0
    except Exception as exc:
        cn.rollback()
        print(f"\n  ERROR {type(exc).__name__}: {exc}\n  rolled back")
        return 1
    finally:
        cn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--translate", action="store_true")
    sys.exit(main(p.parse_args()))
