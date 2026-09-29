#!/usr/bin/env python3
"""
Data cleaning pipeline for talabat_menu_items.

Targets: item_key, menu_category, description
Writes to new columns: item_key_clean, menu_category_clean, description_clean

Stages:
  1. ADD columns if they don't exist
  2. Load all unique values for each column
  3. Apply deterministic text rules (vectorized with polars)
  4. For Arabic-bearing descriptions -> batch-translate with OpenAI gpt-4o-mini
  5. Bulk-write cleaned values back via asyncpg COPY + UPDATE JOIN

Run:
    python talabat/menu/clean_menu_data.py

Requirements: polars, asyncpg, openai, python-dotenv
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import asyncpg
import polars as pl
from dotenv import load_dotenv
from openai import AsyncOpenAI

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

OPENAI_MODEL       = "gpt-4o-mini"
TRANSLATE_BATCH    = 30
TRANSLATE_SEM      = 5
TRANSLATIONS_CACHE = Path(__file__).parent / "translations_cache.json"

LOG_FILE = Path(__file__).parent / "clean_menu_data.log"

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
# Patterns -- ALL raw strings with \uXXXX / \xNN escapes.
# NO literal Unicode characters in this source file.
# ---------------------------------------------------------------------------

# Emoji and symbol ranges
_EMOJI_RE = re.compile(
    r"[\U0001F000-\U0001FFFF"   # misc symbols + pictographs + transport + supplemental
    r"\U00002600-\U000027BF"    # misc symbols, dingbats
    r"\U0000FE00-\U0000FE0F"    # variation selectors
    r"‍"                   # zero-width joiner (used in multi-emoji sequences)
    r"⃣"                   # combining enclosing keycap
    r"⏩-⏳"            # clock/timer emojis
    r"⏸-⏺"
    r"▪-▫▶◀◻-◾"
    r"☔-☕♈-♓"
    r"♿⚓⚡⚪-⚫"
    r"⚽-⚾⛄-⛅⛎-⛏⛔⛪"
    r"⛲-⛳⛵⛺⛽"
    r"✂✅✈-✍✏"
    r"✒✔✖✝✡"
    r"✨✳-✴❄❇"
    r"❌❎❓-❕❗"
    r"❣-❤➕-➗"
    r"➡➰➿"
    r"⤴-⤵⬅-⬇"
    r"⬛-⬜⭐⭕"
    r"〰〽㊗㊙"
    r"⏏"
    r"]+"
)

# Invisible / directional / control characters
# \x01-\x08 = C0 controls (skipping \x00 NUL intentionally)
# \x0b\x0c  = VT, FF
# \x0e-\x1f = shift-out through US
# \x7f      = DEL
# \xad      = soft hyphen (U+00AD)
# ​-‏ = ZWSP, ZWNJ, ZWJ, LRM, RLM
# ‪-‮ = bidi overrides
# ⁠-⁤ = word joiner etc.
# ⁪-⁯ = deprecated format chars
# ﻿    = BOM / ZWNBSP
# ￹-￻ = interlinear annotation
#  -  = line / paragraph separator
_INVISIBLE_RE = re.compile(
    r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f\xad"
    r"​-‏"
    r"‪-‮"
    r"⁠-⁤"
    r"⁪-⁯"
    r"﻿"
    r"￹-￻"
    r"  "
    r"]+"
)

# Arabic punctuation -> ASCII (built from code points, no literal Arabic chars)
_ARABIC_PUNCT = str.maketrans({
    chr(0x061F): "?",   # Arabic question mark
    chr(0x060C): ",",   # Arabic comma
    chr(0x061B): ";",   # Arabic semicolon
})

# Arabic Unicode block U+0600-U+06FF
_ARABIC_DETECT = re.compile(r"[؀-ۿ]+")

# CJK detection (Chinese, Japanese, Korean)
_CJK_DETECT = re.compile(
    r"[一-鿿"    # CJK Unified Ideographs
    r"㐀-䶿"     # CJK Extension A
    r"぀-ゟ"     # Hiragana
    r"゠-ヿ"     # Katakana
    r"가-힯"     # Hangul
    r"]+"
)

_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Text cleaning functions
# ---------------------------------------------------------------------------

def clean_text(text: str | None) -> str | None:
    """Strip emojis, invisible chars, normalise whitespace. Preserve hyphens."""
    if text is None:
        return None
    s = unicodedata.normalize("NFKC", text)
    s = _INVISIBLE_RE.sub(" ", s)
    s = _EMOJI_RE.sub(" ", s)
    s = s.translate(_ARABIC_PUNCT)
    s = _WS_RE.sub(" ", s).strip()
    return s if s else None


def extract_english_from_bilingual(text: str | None) -> str | None:
    """
    If text is bilingual 'English - Arabic', returns the English half.
    Converts Arabic punctuation to ASCII before stripping Arabic chars.
    Accented Latin chars (cafe, acai, crepe etc.) are always preserved.
    """
    if text is None:
        return None
    # No Arabic at all -> standard clean
    if not _ARABIC_DETECT.search(text):
        return clean_text(text)
    # Try extracting English from bilingual separator pattern
    for sep in [" - ", " - ", "- ", " -"]:
        parts = text.split(sep, 1)
        if len(parts) == 2:
            eng, ara = parts
            if re.search(r"[a-zA-Z]", eng) and _ARABIC_DETECT.search(ara):
                return clean_text(eng)
    # Fallback: convert Arabic punctuation to ASCII, then strip Arabic chars
    s = text.translate(_ARABIC_PUNCT)
    s = re.sub(r"[؀-ۿ]+", "", s)
    return clean_text(s)


def extract_english_from_cjk(text: str | None) -> str | None:
    """Strip CJK blocks from item_key, keep English + accented Latin."""
    if text is None:
        return None
    if not _CJK_DETECT.search(text):
        return clean_text(text)
    s = _CJK_DETECT.sub(" ", text)
    return clean_text(s)


def clean_item_key(text: str | None) -> str | None:
    """Clean item_key: strip emojis, invisible chars, Arabic, CJK."""
    s = clean_text(text)
    if s is None:
        return None
    # Strip Arabic chars from item keys (bilingual extraction not relevant here)
    if _ARABIC_DETECT.search(s):
        # Try to extract English side if pattern is "english | arabic" or "english - arabic"
        for sep in [" | ", " - ", " - ", "| ", " |"]:
            parts = s.split(sep, 1)
            if len(parts) == 2:
                eng, ara = parts
                if re.search(r"[a-zA-Z]", eng) and _ARABIC_DETECT.search(ara):
                    s = clean_text(eng)
                    break
        else:
            s = re.sub(r"[؀-ۿ]+", "", s)
            s = clean_text(s)
    # Strip CJK
    if s and _CJK_DETECT.search(s):
        s = extract_english_from_cjk(s)
    return s if s else None


# ---------------------------------------------------------------------------
# OpenAI translation helpers
# ---------------------------------------------------------------------------

TRANSLATE_PROMPT = """\
You are a professional translator and food data specialist.
Below are food item descriptions that contain a mix of English and Arabic text.
For each description (one per line, prefixed with INDEX|||), extract the best
English-only version. Rules:
- Keep all English parts as-is (fix grammar only if clearly broken)
- Translate any Arabic segment into natural English
- Remove duplicate information (don't repeat the same info twice)
- Remove emojis and special characters
- Return only the cleaned English text, one per line, prefixed with the same INDEX|||
- If the text is purely junk or untranslatable, return INDEX|||[SKIP]
"""


async def translate_batch(
    client: AsyncOpenAI,
    sem: asyncio.Semaphore,
    items: list[tuple[int, str]],
) -> dict[int, str]:
    payload = "\n".join(f"{idx}|||{text}" for idx, text in items)
    async with sem:
        for attempt in range(3):
            try:
                resp = await client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": TRANSLATE_PROMPT},
                        {"role": "user", "content": payload},
                    ],
                    temperature=0,
                    max_tokens=4000,
                )
                raw = resp.choices[0].message.content or ""
                results: dict[int, str] = {}
                for line in raw.strip().splitlines():
                    line = line.strip()
                    if "|||" in line:
                        idx_str, _, translated = line.partition("|||")
                        try:
                            idx = int(idx_str.strip())
                            cleaned = translated.strip()
                            if cleaned and cleaned != "[SKIP]":
                                results[idx] = cleaned
                        except ValueError:
                            pass
                return results
            except Exception as e:
                if attempt == 2:
                    log.error("Translation batch failed: %s", e)
                    return {}
                await asyncio.sleep(2 ** attempt)
    return {}


async def translate_arabic_descriptions(
    desc_map: dict[int, str],
) -> dict[int, str]:
    """
    Translate descriptions with Arabic text using OpenAI.
    Loads/saves a local cache so re-runs skip already-translated rows.
    """
    cache: dict[int, str] = {}
    if TRANSLATIONS_CACHE.exists():
        with open(TRANSLATIONS_CACHE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            cache = {int(k): v for k, v in raw.items()}
        log.info("Loaded %d cached translations", len(cache))

    uncached = {k: v for k, v in desc_map.items() if k not in cache}

    if uncached:
        client = AsyncOpenAI(api_key=os.environ["OPEN_AI_API"])
        sem    = asyncio.Semaphore(TRANSLATE_SEM)
        items  = list(uncached.items())
        batches = [items[i:i + TRANSLATE_BATCH] for i in range(0, len(items), TRANSLATE_BATCH)]
        log.info("Translating %d uncached rows in %d batches...", len(items), len(batches))

        results = await asyncio.gather(*[translate_batch(client, sem, b) for b in batches])
        new_trans: dict[int, str] = {}
        for r in results:
            new_trans.update(r)

        cache.update(new_trans)
        with open(TRANSLATIONS_CACHE, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in cache.items()}, f, ensure_ascii=False, indent=2)
        log.info("Translated %d new rows | %d total in cache", len(new_trans), len(cache))
    else:
        log.info("All %d rows found in cache -- skipping OpenAI", len(desc_map))

    return {k: cache[k] for k in desc_map if k in cache}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

async def ensure_clean_columns(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            ALTER TABLE talabat_menu_items
            ADD COLUMN IF NOT EXISTS item_key_clean       TEXT,
            ADD COLUMN IF NOT EXISTS menu_category_clean  TEXT,
            ADD COLUMN IF NOT EXISTS description_clean    TEXT;
        """)
    log.info("Clean columns ensured")


async def load_table(pool: asyncpg.Pool) -> pl.DataFrame:
    log.info("Loading talabat_menu_items...")
    t0 = time.time()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, item_key, menu_category, description FROM talabat_menu_items"
        )
    df = pl.DataFrame({
        "id":            [r["id"] for r in rows],
        "item_key":      [r["item_key"] for r in rows],
        "menu_category": [r["menu_category"] for r in rows],
        "description":   [r["description"] for r in rows],
    })
    log.info("Loaded %d rows in %.1fs", len(df), time.time() - t0)
    return df


async def bulk_update(pool: asyncpg.Pool, all_rows: list[tuple]) -> None:
    """
    Single-shot bulk UPDATE via a real public staging table.
    Avoids pg_temp schema issues with asyncpg copy_records_to_table.
    """
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS public._clean_staging_tbl;")
        await conn.execute("""
            CREATE TABLE public._clean_staging_tbl (
                id                  BIGINT PRIMARY KEY,
                item_key_clean      TEXT,
                menu_category_clean TEXT,
                description_clean   TEXT
            );
        """)

    async with pool.acquire() as conn:
        await conn.copy_records_to_table(
            "_clean_staging_tbl",
            records=all_rows,
            columns=["id", "item_key_clean", "menu_category_clean", "description_clean"],
            schema_name="public",
        )
        log.info("Staging table populated with %d rows", len(all_rows))

        result = await conn.execute("""
            UPDATE talabat_menu_items t
            SET
                item_key_clean      = s.item_key_clean,
                menu_category_clean = s.menu_category_clean,
                description_clean   = s.description_clean
            FROM public._clean_staging_tbl s
            WHERE t.id = s.id;
        """)
        log.info("UPDATE done: %s", result)

    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS public._clean_staging_tbl;")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def main() -> None:
    log.info("=" * 60)
    log.info("Menu Data Cleaning Pipeline")
    log.info("=" * 60)

    pool = await asyncpg.create_pool(**LOCAL_PG, min_size=2, max_size=6)

    await ensure_clean_columns(pool)

    df = await load_table(pool)
    total = len(df)

    # ------------------------------------------------------------------
    # Step 3: Deterministic cleaning on unique values only (fast)
    # ------------------------------------------------------------------
    log.info("Applying deterministic cleaning rules...")
    t0 = time.time()

    unique_keys = df["item_key"].unique().to_list()
    key_map = {k: clean_item_key(k) for k in unique_keys}
    log.info("  item_key: %d unique cleaned", len(unique_keys))

    unique_cats = df["menu_category"].unique().to_list()
    cat_map = {c: extract_english_from_bilingual(c) for c in unique_cats}
    log.info("  menu_category: %d unique cleaned", len(unique_cats))

    df = df.with_columns([
        pl.col("item_key").map_elements(
            lambda x: key_map.get(x), return_dtype=pl.Utf8
        ).alias("item_key_clean"),
        pl.col("menu_category").map_elements(
            lambda x: cat_map.get(x) if x is not None else None, return_dtype=pl.Utf8
        ).alias("menu_category_clean"),
    ])
    log.info("  deterministic done in %.1fs", time.time() - t0)

    # ------------------------------------------------------------------
    # Step 4: Description cleaning
    # ------------------------------------------------------------------
    log.info("Cleaning descriptions...")
    t0 = time.time()

    df = df.with_columns([
        pl.col("description").map_elements(
            clean_text, return_dtype=pl.Utf8
        ).alias("description_clean"),
    ])
    log.info("  deterministic done in %.1fs", time.time() - t0)

    # Detect Arabic remaining after clean
    arabic_mask = df["description_clean"].map_elements(
        lambda x: bool(_ARABIC_DETECT.search(x)) if x else False,
        return_dtype=pl.Boolean,
    )
    arabic_df = df.filter(arabic_mask)
    log.info("  descriptions with Arabic: %d", len(arabic_df))

    if len(arabic_df) > 0:
        arabic_desc_map = {
            row["id"]: row["description_clean"]
            for row in arabic_df.select(["id", "description_clean"]).to_dicts()
        }
        translated = await translate_arabic_descriptions(arabic_desc_map)

        if translated:
            df = df.with_columns([
                pl.struct(["id", "description_clean"]).map_elements(
                    lambda x: translated.get(x["id"], x["description_clean"]),
                    return_dtype=pl.Utf8,
                ).alias("description_clean")
            ])
            log.info("  merged %d translations", len(translated))

    # ------------------------------------------------------------------
    # Step 5: Quality snapshot
    # ------------------------------------------------------------------
    log.info("=" * 50)
    log.info("Quality snapshot:")
    log.info("  item_key_clean      : %d nulls (%.2f%%)",
             df["item_key_clean"].null_count(), 100 * df["item_key_clean"].null_count() / total)
    log.info("  menu_category_clean : %d nulls (%.2f%%)",
             df["menu_category_clean"].null_count(), 100 * df["menu_category_clean"].null_count() / total)
    log.info("  description_clean   : %d nulls (%.2f%%)",
             df["description_clean"].null_count(), 100 * df["description_clean"].null_count() / total)
    log.info("=" * 50)

    # ------------------------------------------------------------------
    # Step 6: Write back
    # ------------------------------------------------------------------
    log.info("Writing to PostgreSQL...")
    t0 = time.time()

    rows_tuples = [
        (r["id"], r["item_key_clean"], r["menu_category_clean"], r["description_clean"])
        for r in df.select(["id", "item_key_clean", "menu_category_clean", "description_clean"]).to_dicts()
    ]
    await bulk_update(pool, rows_tuples)
    log.info("All %d rows written in %.1fs", total, time.time() - t0)

    # ------------------------------------------------------------------
    # Step 7: Verification (fresh connection)
    # ------------------------------------------------------------------
    log.info("Running verification...")
    await pool.close()
    # Open a fresh pool for verification to avoid protocol state issues
    vpool = await asyncpg.create_pool(**LOCAL_PG, min_size=1, max_size=2)
    async with vpool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT
              COUNT(*)                                                                AS total,
              COUNT(item_key_clean)                                                   AS ik_filled,
              COUNT(menu_category_clean)                                              AS cat_filled,
              COUNT(description_clean)                                                AS desc_filled,
              COUNT(*) FILTER (WHERE item_key_clean ~ '[^\x01-\x7e\x80-\xff]')       AS ik_nonlatin,
              COUNT(*) FILTER (WHERE menu_category_clean ~ '[^\x01-\x7e\x80-\xff]')  AS cat_nonlatin,
              COUNT(*) FILTER (WHERE description_clean ~ '[^\x01-\x7e\x80-\xff]')    AS desc_nonlatin
            FROM talabat_menu_items;
        """)
    await vpool.close()

    log.info("=" * 60)
    log.info("VERIFICATION RESULTS:")
    log.info("  total rows          : %s", stats["total"])
    log.info("  item_key_clean      : %s filled", stats["ik_filled"])
    log.info("  menu_category_clean : %s filled", stats["cat_filled"])
    log.info("  description_clean   : %s filled", stats["desc_filled"])
    log.info("  item_key non-latin  : %s", stats["ik_nonlatin"])
    log.info("  category non-latin  : %s", stats["cat_nonlatin"])
    log.info("  description non-lat : %s", stats["desc_nonlatin"])
    log.info("=" * 60)
    log.info("Pipeline complete.")


if __name__ == "__main__":
    asyncio.run(main())
