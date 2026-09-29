#!/usr/bin/env python3
"""
clean_menu_step2.py — Second-pass patch on talabat_menu_items.

1. Translate non-Latin item_key_clean values to English
   (async OpenAI, token-bucket RPM=4200, tenacity retries)
2. Slug-normalize item_key_clean and menu_category_clean
   (lowercase + strip + spaces -> underscores)
3. Bulk-write both columns back to PostgreSQL
4. Export unique menu_category_clean to CSV

Run:
    python talabat/menu/clean_menu_step2.py
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import asyncpg
import polars as pl
from dotenv import load_dotenv
from openai import AsyncOpenAI, RateLimitError, APITimeoutError, APIConnectionError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_PASSWORD  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
LOCAL_PG = dict(
    host="localhost", port=5432,
    database="RestaurantIntelligence",
    user="postgres", password=PG_PASSWORD,
)

OPENAI_MODEL    = "gpt-4o-mini"
TRANSLATE_BATCH = 30
TRANSLATE_RPM   = 4200          # requests per minute ceiling
TRANSLATE_SEM   = 25            # max concurrent in-flight requests
STEP2_CACHE     = Path(__file__).parent / "translations_step2_cache.json"
OUTPUT_CSV      = Path(__file__).parent / "unique_menu_categories.csv"
LOG_FILE        = Path(__file__).parent / "clean_menu_step2.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8", mode="w"),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patterns (raw strings only, no literal Unicode in source)
# ---------------------------------------------------------------------------

# Any non-Latin script character (Arabic, CJK, Hangul, Hiragana/Katakana, Thai, Devanagari)
_NEEDS_TRANSLATION = re.compile(
    r"[؀-ۿ"                  # Arabic block U+0600-U+06FF
    r"ݐ-ݿ"          # Arabic Supplement
    r"ࢠ-ࣿ"          # Arabic Extended-A
    r"一-鿿"          # CJK Unified Ideographs
    r"㐀-䶿"          # CJK Extension A
    r"぀-ヿ"          # Hiragana + Katakana
    r"가-힯"          # Hangul Syllables
    r"ऀ-ॿ"          # Devanagari
    r"฀-๿"          # Thai
    r"]"
)


def slugify(text: str | None) -> str | None:
    """Lowercase, strip, collapse whitespace/underscores into single underscore."""
    if text is None:
        return None
    s = text.lower().strip()
    s = re.sub(r"[\s]+", "_", s)          # spaces -> underscore
    s = re.sub(r"_+", "_", s)             # collapse consecutive underscores
    s = s.strip("_")
    return s if s else None


# ---------------------------------------------------------------------------
# Async token-bucket rate limiter
# ---------------------------------------------------------------------------

class AsyncTokenBucket:
    """Leaky-bucket rate limiter safe for asyncio concurrent use."""
    def __init__(self, rpm: int, burst: int = 0):
        self._rate     = rpm / 60.0                    # tokens per second
        self._capacity = burst if burst else max(20, rpm // 30)
        self._tokens   = float(self._capacity)
        self._last     = time.monotonic()
        self._lock     = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now     = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last   = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait = (1.0 - self._tokens) / self._rate
        await asyncio.sleep(wait)
        async with self._lock:
            self._tokens = max(0.0, self._tokens - 1.0)


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------

TRANSLATE_PROMPT = """\
You are a professional translator specializing in food and restaurant menu items.
Each line below is a menu item name in Arabic, Chinese, or another non-English language,
prefixed with a numeric INDEX|||.

For each item:
- Provide a short, natural English translation (1-6 words, title case is fine)
- If the item is purely a number, symbol, or truly untranslatable junk, return INDEX|||[SKIP]
- Return exactly one line per input line: INDEX|||EnglishName
- Do not add extra commentary or explanation
"""


def _build_translate_fn(
    client: AsyncOpenAI,
    bucket: AsyncTokenBucket,
    sem: asyncio.Semaphore,
):
    @retry(
        retry=retry_if_exception_type(
            (RateLimitError, APITimeoutError, APIConnectionError)
        ),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(6),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    async def _call(items: list[tuple[int, str]]) -> dict[int, str]:
        await bucket.acquire()
        payload = "\n".join(f"{idx}|||{text}" for idx, text in items)
        async with sem:
            resp = await client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": TRANSLATE_PROMPT},
                    {"role": "user",   "content": payload},
                ],
                temperature=0,
                max_tokens=1200,
            )
        raw = resp.choices[0].message.content or ""
        out: dict[int, str] = {}
        for line in raw.strip().splitlines():
            if "|||" not in line:
                continue
            idx_str, _, translated = line.partition("|||")
            try:
                idx = int(idx_str.strip())
                val = translated.strip()
                if val and val != "[SKIP]":
                    out[idx] = val
            except ValueError:
                pass
        return out

    return _call


async def translate_nonlatin(unique_vals: list[str]) -> dict[str, str]:
    """
    Translate a list of unique non-Latin strings to English.
    Returns {original_value: english_translation}.
    Saves/loads a local JSON cache so re-runs skip already-translated values.
    """
    cache: dict[str, str] = {}
    if STEP2_CACHE.exists():
        with open(STEP2_CACHE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        log.info("Step2 cache loaded: %d entries", len(cache))

    uncached = [v for v in unique_vals if v not in cache]
    log.info(
        "Non-Latin unique values: %d total | %d cached | %d to translate",
        len(unique_vals), len(unique_vals) - len(uncached), len(uncached),
    )

    if not uncached:
        return {v: cache[v] for v in unique_vals if v in cache}

    client = AsyncOpenAI(api_key=os.environ["OPEN_AI_API"])
    bucket = AsyncTokenBucket(rpm=TRANSLATE_RPM)
    sem    = asyncio.Semaphore(TRANSLATE_SEM)
    fn     = _build_translate_fn(client, bucket, sem)

    # Global enumeration -> batches
    indexed  = list(enumerate(uncached))          # [(0, "val"), (1, "val"), ...]
    batches  = [indexed[i:i + TRANSLATE_BATCH]
                for i in range(0, len(indexed), TRANSLATE_BATCH)]
    log.info(
        "Launching %d batches | RPM=%d | SEM=%d",
        len(batches), TRANSLATE_RPM, TRANSLATE_SEM,
    )
    t0 = time.time()

    results = await asyncio.gather(*[fn(b) for b in batches], return_exceptions=True)

    new_trans: dict[str, str] = {}
    errors = 0
    for batch_items, result in zip(batches, results):
        if isinstance(result, Exception):
            log.error("Batch failed permanently: %s", result)
            errors += 1
            continue
        idx_to_orig = {idx: text for idx, text in batch_items}
        for idx, translated in result.items():
            orig = idx_to_orig.get(idx)
            if orig is not None:
                new_trans[orig] = translated

    cache.update(new_trans)
    with open(STEP2_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - t0
    log.info(
        "Translated %d/%d in %.1fs | %d errors | cache total: %d",
        len(new_trans), len(uncached), elapsed, errors, len(cache),
    )
    return {v: cache[v] for v in unique_vals if v in cache}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def load_clean_cols(pool: asyncpg.Pool) -> pl.DataFrame:
    log.info("Loading item_key_clean and menu_category_clean from DB...")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, item_key_clean, menu_category_clean FROM talabat_menu_items"
        )
    df = pl.DataFrame({
        "id":                  [r["id"]                  for r in rows],
        "item_key_clean":      [r["item_key_clean"]      for r in rows],
        "menu_category_clean": [r["menu_category_clean"] for r in rows],
    })
    log.info("Loaded %d rows", len(df))
    return df


async def bulk_update_step2(pool: asyncpg.Pool, rows: list[tuple]) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS public._step2_staging;")
        await conn.execute("""
            CREATE TABLE public._step2_staging (
                id                  BIGINT PRIMARY KEY,
                item_key_clean      TEXT,
                menu_category_clean TEXT
            );
        """)
    async with pool.acquire() as conn:
        await conn.copy_records_to_table(
            "_step2_staging",
            records=rows,
            columns=["id", "item_key_clean", "menu_category_clean"],
            schema_name="public",
        )
        log.info("Staging table populated: %d rows", len(rows))
        result = await conn.execute("""
            UPDATE talabat_menu_items t
            SET
                item_key_clean      = s.item_key_clean,
                menu_category_clean = s.menu_category_clean
            FROM public._step2_staging s
            WHERE t.id = s.id;
        """)
        log.info("UPDATE: %s", result)
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS public._step2_staging;")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    log.info("=" * 60)
    log.info("Step 2: Translate + Slugify + Export")
    log.info("=" * 60)

    pool = await asyncpg.create_pool(**LOCAL_PG, min_size=2, max_size=6)
    df   = await load_clean_cols(pool)
    total = len(df)

    # ------------------------------------------------------------------
    # 1. Detect non-Latin item_key_clean unique values
    # ------------------------------------------------------------------
    nonlatin_mask = df["item_key_clean"].map_elements(
        lambda x: bool(_NEEDS_TRANSLATION.search(x)) if x else False,
        return_dtype=pl.Boolean,
    )
    unique_nonlat = (
        df.filter(nonlatin_mask)["item_key_clean"]
          .drop_nulls()
          .unique()
          .to_list()
    )
    log.info(
        "Non-Latin item_key_clean: %d rows | %d unique",
        nonlatin_mask.sum(), len(unique_nonlat),
    )

    # ------------------------------------------------------------------
    # 2. Translate non-Latin values
    # ------------------------------------------------------------------
    translation_map: dict[str, str] = {}
    if unique_nonlat:
        translation_map = await translate_nonlatin(unique_nonlat)

    # ------------------------------------------------------------------
    # 3. Apply translations, then slugify both columns
    # ------------------------------------------------------------------
    log.info("Applying translations and slug normalization...")
    t0 = time.time()

    def resolve_item_key(x: str | None) -> str | None:
        if x is None:
            return None
        resolved = translation_map.get(x, x)   # replace if translated
        return slugify(resolved)

    df = df.with_columns([
        pl.col("item_key_clean").map_elements(
            resolve_item_key, return_dtype=pl.Utf8
        ).alias("item_key_clean"),
        pl.col("menu_category_clean").map_elements(
            slugify, return_dtype=pl.Utf8
        ).alias("menu_category_clean"),
    ])
    log.info("Slug normalization done in %.1fs", time.time() - t0)
    log.info(
        "Nulls after slug -- item_key: %d | category: %d",
        df["item_key_clean"].null_count(),
        df["menu_category_clean"].null_count(),
    )

    # Sample check
    sample = df.filter(pl.col("item_key_clean").is_not_null()).head(5)
    for row in sample.to_dicts():
        log.info("  sample item_key_clean: %r", row["item_key_clean"])

    # ------------------------------------------------------------------
    # 4. Write back to DB
    # ------------------------------------------------------------------
    log.info("Writing %d rows to PostgreSQL...", total)
    t0 = time.time()
    rows_tuples = [
        (r["id"], r["item_key_clean"], r["menu_category_clean"])
        for r in df.select(["id", "item_key_clean", "menu_category_clean"]).to_dicts()
    ]
    await bulk_update_step2(pool, rows_tuples)
    log.info("Wrote %d rows in %.1fs", total, time.time() - t0)

    # ------------------------------------------------------------------
    # 5. Export unique menu_category_clean to CSV
    # ------------------------------------------------------------------
    log.info("Exporting unique menu_category_clean to %s ...", OUTPUT_CSV)
    unique_cats = (
        df.filter(pl.col("menu_category_clean").is_not_null())
          .select("menu_category_clean")
          .unique()
          .sort("menu_category_clean")
    )
    unique_cats.write_csv(OUTPUT_CSV)
    log.info("Exported %d unique categories", len(unique_cats))

    await pool.close()
    log.info("=" * 60)
    log.info("Step 2 complete.")
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
