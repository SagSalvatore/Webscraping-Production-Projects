"""Stage 3 - translate any Arabic (or other non-Latin) text to English.

Per Sagar: the shipped data must carry NO Arabic. This REVERSES the policy in
talabat/sanitization/text_cleaner.py, which deliberately preserves Arabic as
valid bilingual UAE content - so the original is never destroyed, only replaced,
and every replacement is written to translate_mapping.csv.

Reuses the approach proven in menu/clean_menu_step2.py:
  * gpt-4o-mini, temperature 0
  * batches of 30, `INDEX|||text` in, `INDEX|||English` out
  * [SKIP] sentinel for untranslatable junk
  * disk cache keyed by the SOURCE STRING, checkpointed mid-run

Cost control that matters: translate DISTINCT VALUES, not rows. The same
category ("المقبلات") repeats across thousands of rows; sending rows instead of
values would multiply the bill for identical work.

Bilingual strings ("Soup الشوربة") are collapsed to the English side rather
than doubled - the prompt asks for English only.

    python translate_arabic.py --dry-run    count + cost estimate, no API calls
    python translate_arabic.py --limit 50   small live test
    python translate_arabic.py              full run
"""
import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
load_dotenv(ROOT / ".env", override=True)

def _argv_value(flag, default=None):
    """Read --flag VALUE (or --flag=VALUE) straight from argv.

    Needed BEFORE argparse runs because DATA and everything derived from it are
    module-level constants. A cohort that cannot repoint DATA would read
    August's menu_items.jsonl and overwrite August's outputs - the same hazard
    run2_collector's --out and restaurant_identifier's --cycle already fix.
    """
    import sys as _s
    if flag in _s.argv:
        i = _s.argv.index(flag)
        if i + 1 < len(_s.argv):
            return _s.argv[i + 1]
    for a in _s.argv:
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return default

DATA = Path(_argv_value("--data") or (HERE / "data"))
SRC = DATA / "menu_items_clean.jsonl"          # output of sanitize_menus.py
OUT = DATA / "menu_items_final.jsonl"
CACHE = DATA / "translation_cache.json"
MAPPING = DATA / "translate_mapping.csv"

MODEL = "gpt-4.1-mini"
BATCH = 30
CONCURRENCY = 8
CHECKPOINT_EVERY = 10          # batches

# Arabic incl. supplement + presentation forms; plus any other non-Latin script
# we might meet on a UAE menu (Persian/Urdu share the Arabic block).
NONLATIN_RX = re.compile(
    r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿"   # Arabic
    r"֐-׿"                                                        # Hebrew
    r"฀-๿"                                                        # Thai
    r"一-鿿぀-ヿ"                                           # CJK/Kana
    r"Ѐ-ӿ]")                                                      # Cyrillic

PROMPT = """\
You are a professional translator specializing in food and restaurant menus.
Each line is a menu category, item name, or description, prefixed with INDEX|||.

For each line:
- Return a short, natural ENGLISH translation (title case is fine)
- If the line is already partly English (e.g. "Soup الشوربة"), return ONLY the
  English form once - never repeat the original script
- The output must contain NO Arabic or other non-Latin characters at all
- If it is purely a number, symbol, or untranslatable junk, return INDEX|||[SKIP]
- Return exactly one line per input line: INDEX|||English
- No commentary, no explanation
"""

# Second pass for whatever the first prompt refused. Measured: the model
# returned [SKIP] on 57 values that were plainly translatable - brand names and
# ordinary section headers ("اندومي" = Indomie, "قسم البيض" = Egg Section).
# [SKIP] is removed as an option and transliteration is required, because the
# requirement is that NO Arabic ships - an untranslated cell is not acceptable
# output here, unlike the usual "an honest null beats a guess" rule.
STRICT_PROMPT = """\
You are transliterating and translating UAE restaurant menu text to English.
Each line is prefixed with INDEX|||.

Rules:
- ALWAYS return an English rendering. [SKIP] is NOT allowed.
- Translate if it has a meaning ("قسم البيض" -> "Egg Section")
- If it is a brand or proper name, TRANSLITERATE it ("اندومي" -> "Indomie")
- If mixed script, return only the Latin/English part, cleaned
- Output must contain NO non-Latin characters whatsoever
- Exactly one line per input: INDEX|||English
- No commentary
"""

FIELDS = ("category", "item_name", "description")


def load_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("cache unreadable - starting fresh")
    return {}


def save_cache(c):
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE)


async def translate_batch(client, sem, items, cache, stats, strict=False):
    """items: list of (index, source_string)."""
    payload = "\n".join(f"{i}|||{t}" for i, t in items)
    for attempt in range(5):
        try:
            async with sem:
                resp = await client.chat.completions.create(
                    model=MODEL, temperature=0, max_tokens=1500,
                    messages=[{"role": "system", "content": STRICT_PROMPT if strict else PROMPT},
                              {"role": "user", "content": payload}])
            raw = resp.choices[0].message.content or ""
            by_idx = {i: t for i, t in items}
            got = 0
            for line in raw.strip().splitlines():
                if "|||" not in line:
                    continue
                idx_s, _, val = line.partition("|||")
                try:
                    idx = int(idx_s.strip())
                except ValueError:
                    continue
                val = val.strip()
                src = by_idx.get(idx)
                if src is None or not val:
                    continue
                if val == "[SKIP]":
                    stats["skipped"] += 1
                    continue
                # A prompt instruction is not a guarantee - verify the model
                # actually removed the non-Latin text before trusting it.
                if NONLATIN_RX.search(val):
                    stats["rejected_still_nonlatin"] += 1
                    continue
                cache[src] = val
                got += 1
            stats["translated"] += got
            return
        except Exception as exc:
            stats["api_error"] += 1
            logger.warning(f"batch failed ({type(exc).__name__}) attempt {attempt+1}")
            await asyncio.sleep(2 * (2 ** attempt))
    stats["failed_batches"] += 1


async def main(args):
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")

    if not SRC.exists():
        logger.error(f"missing {SRC.name} - run sanitize_menus.py first")
        return 1
    rows = [json.loads(l) for l in open(SRC, encoding="utf-8") if l.strip()]
    logger.info(f"rows: {len(rows):,}")

    # distinct values only - this is the whole cost story
    distinct, per_field = {}, Counter()
    for r in rows:
        for f in FIELDS:
            v = (r.get(f) or "").strip()
            if v and NONLATIN_RX.search(v):
                distinct.setdefault(v, set()).add(f)
                per_field[f] += 1
    logger.info(f"rows containing non-Latin text: {sum(per_field.values()):,} "
                f"({dict(per_field)})")
    logger.info(f"DISTINCT values to translate  : {len(distinct):,}")

    cache = load_cache()
    todo = [v for v in distinct if v not in cache]
    logger.info(f"cached {len(distinct)-len(todo):,} | to translate {len(todo):,}")

    if args.limit:
        todo = todo[: args.limit]
        logger.warning(f"TEST MODE - {len(todo)} values")

    n_batches = (len(todo) + BATCH - 1) // BATCH
    # ~700 tokens in + ~400 out per batch of 30, gpt-4o-mini pricing
    est = n_batches * (700 * 0.15 + 400 * 0.60) / 1_000_000
    logger.info(f"batches: {n_batches} | rough cost estimate ${est:.3f}")

    if args.dry_run:
        logger.warning("--dry-run: no API calls")
        for v in todo[:10]:
            logger.info(f"    would translate: {v[:60]!r}")
        return 0

    if todo:
        if not os.getenv("OPEN_AI_API"):
            logger.error("OPEN_AI_API not set in talabat/.env")
            return 1
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.environ["OPEN_AI_API"])
        sem = asyncio.Semaphore(CONCURRENCY)
        stats = Counter()
        indexed = list(enumerate(todo))
        batches = [indexed[i:i + BATCH] for i in range(0, len(indexed), BATCH)]
        t0 = time.time()
        try:
            for i in range(0, len(batches), CONCURRENCY):
                chunk = batches[i:i + CONCURRENCY]
                await asyncio.gather(*(translate_batch(client, sem, b, cache, stats,
                                                      strict=args.strict)
                                       for b in chunk))
                if (i // CONCURRENCY) % CHECKPOINT_EVERY == 0:
                    save_cache(cache)
                    done = min(i + CONCURRENCY, len(batches))
                    logger.info(f"  {done}/{len(batches)} batches | "
                                f"translated {stats['translated']:,}")
        finally:
            save_cache(cache)          # never lose paid work
        logger.success(f"API done in {(time.time()-t0)/60:.1f} min | {dict(stats)}")

    # ---------- apply ----------
    changes, unresolved = [], Counter()
    for r in rows:
        for f in FIELDS:
            v = (r.get(f) or "").strip()
            if not v or not NONLATIN_RX.search(v):
                continue
            new = cache.get(v)
            if not new:
                unresolved[f] += 1
                continue
            if new != v:
                changes.append({"branch_id": r["branch_id"], "item_id": r["item_id"],
                                "field": f, "from": v, "to": new})
            r[f] = new

    left = sum(1 for r in rows for f in FIELDS
               if NONLATIN_RX.search(r.get(f) or ""))
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if changes:
        with open(MAPPING, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["branch_id", "item_id", "field", "from", "to"])
            w.writeheader(); w.writerows(changes)

    logger.success(f"-> {OUT.name} ({len(rows):,} rows)")
    logger.info(f"  values replaced      : {len(changes):,}")
    logger.info(f"  still non-Latin      : {left:,}"
                + ("  <-- re-run to finish" if left else "  (clean)"))
    if unresolved:
        logger.warning(f"  untranslated by field: {dict(unresolved)}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--data", help="cohort data dir ""(default: August_menu/data). Repoints every input and output.")
    p.add_argument("--limit", type=int)
    p.add_argument("--strict", action="store_true",
                   help="second pass: forbid [SKIP], require transliteration")
    sys.exit(asyncio.run(main(p.parse_args())))
