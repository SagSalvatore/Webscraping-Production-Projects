#!/usr/bin/env python3
"""
std_term_mapper.py
Map 1.1M talabat_menu_items to 572 curated std_terms. No OpenAI.

Pipeline (runs on 355K unique item_key_clean, joins back to 1.1M rows):
  1. Rule engine     — regex: combo/addon/marketing/non-food   (fast, high-confidence)
  2. Exact match     — dict lookup against 572 taxonomy
  3. KB name lookup  — Dubai KB names → mapped std_terms
  4. RapidFuzz       — token_set_ratio, cdist workers=-1 (C++ parallel)
  5. TF-IDF cosine   — sklearn sparse, 5K-row batches
  6. Embeddings      — intfloat/multilingual-e5-small (semantic fallback)
  7. Write           — asyncpg COPY + UPDATE into talabat_menu_items
  8. Cache           — master_mapping.parquet (skip re-running matched keys)

Run:
    python talabat/menu/std_term_mapper.py
"""

import asyncio
import logging
import re
import sys
import time
from pathlib import Path

import asyncpg
import numpy as np
import polars as pl
from dotenv import load_dotenv

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

TAXONOMY_CSV   = Path(__file__).parent / "final_std_terms_taxonomy.csv"
DUBAI_KB_CSV   = Path(__file__).parent / "Dubai_knwl.csv"
MASTER_PARQUET = Path(__file__).parent / "master_mapping.parquet"
LOG_FILE       = Path(__file__).parent / "std_term_mapper.log"

EMBED_MODEL  = "intfloat/multilingual-e5-small"
EMBED_BATCH  = 256

FUZZY_CUTOFF = 70    # minimum score to consider a fuzzy match (0-100)
FUZZY_ACCEPT = 88    # score >= this  → review_required = False
TFIDF_ACCEPT = 0.42  # cosine >= this → no review
TFIDF_MIN    = 0.28  # cosine >= this → accept with review flag
EMBED_ACCEPT = 0.80  # cosine >= this → no review
EMBED_MIN    = 0.65  # cosine >= this → accept with review flag

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
# Text helpers
# ---------------------------------------------------------------------------
_SPACE_RE = re.compile(r"[\s_]+")
_PUNCT_RE = re.compile(r"[^\w\s]")


def unslug(text: str | None) -> str:
    if not text:
        return ""
    return _SPACE_RE.sub(" ", text).strip()


def normalize(text: str) -> str:
    """Lowercase + strip punctuation + collapse spaces. Used for all matching."""
    t = text.lower()
    t = _PUNCT_RE.sub(" ", t)
    return _SPACE_RE.sub(" ", t).strip()


def build_combined(item_key: str | None, category: str | None) -> str:
    """item_key_text + category_text (unslugified). Description intentionally excluded."""
    parts = [unslug(item_key), unslug(category)]
    return " ".join(p for p in parts if p).strip()


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------
# (compiled_pattern, std_term, taxonomy, confidence)
# Patterns are matched against lowercased combined_text. First match wins.
_ITEM_RULES: list[tuple] = [
    # Non-food / operational items
    (re.compile(
        r"\bstationery\b|\bpackaging[\s_]fee\b|\bservice[\s_]charge\b"
        r"|\bdelivery[\s_]fee\b|\butensil\b|\bcutlery\b|\bplastic[\s_]bag\b"
    ), "marketing/non-standard menu", "marketing/non-standard menu", 0.99),
    # Pure marketing / promotional category items
    (re.compile(
        r"\bbest[\s_]?seller\b|\bmost[\s_]?ordered\b|\bpick[\s_]for[\s_]you\b"
        r"|\bspecial[\s_]offer\b|\bnew[\s_]arrival\b|\bchef.s[\s_]?pick\b"
        r"|\btoday.s[\s_]special\b|\bflash[\s_]sale\b|\brecommended[\s_]for[\s_]you\b"
    ), "marketing/non-standard menu", "marketing/non-standard menu", 0.99),
    # Pure add-ons — item key starts with "extra …" or "add-on …"
    (re.compile(
        r"^(extra[\s_]\w|add[\s\-]?on[\s_]\w|additional[\s_]\w"
        r"|dipping[\s_]sauce|side[\s_]sauce|sauce[\s_]on[\s_]side)"
    ), "addon", "addon", 0.95),
    # Pure combo / meal deal (key IS just a combo descriptor)
    (re.compile(
        r"^(combo|family[\s_]meal|value[\s_]meal|meal[\s_]deal"
        r"|party[\s_]pack|sharing[\s_]platter)\s*$"
    ), "combo", "combo", 0.95),
]

_CAT_MARKETING_RE = re.compile(
    r"best[\s_]?seller|most[\s_]?order|featured|recommended|special[\s_]?offer"
    r"|new[\s_]?arrival|popular|flash[\s_]?sale|pick[\s_]?for[\s_]?you"
    r"|deal[\s_]?of[\s_]?the[\s_]?day|promotion|\boffers\b|\bdeals\b",
    re.IGNORECASE,
)


def run_rule_engine(
    keys: list[str],
    combined_map: dict[str, str],
    cat_map: dict[str, str],
) -> dict[str, tuple]:
    results: dict[str, tuple] = {}
    for key in keys:
        ct  = combined_map.get(key, "").lower()
        cat = unslug(cat_map.get(key) or "").lower()
        if _CAT_MARKETING_RE.search(cat):
            results[key] = (
                "marketing/non-standard menu", "marketing/non-standard menu", 0.99, "rule", False
            )
            continue
        for pat, std, tax, conf in _ITEM_RULES:
            if pat.search(ct):
                results[key] = (std, tax, conf, "rule", False)
                break
    return results


# ---------------------------------------------------------------------------
# Exact match against 572 taxonomy
# ---------------------------------------------------------------------------

def run_exact_match(
    keys: list[str],
    combined_map: dict[str, str],
    std_norm_to_orig: dict[str, str],
    tax_map: dict[str, str],
) -> dict[str, tuple]:
    results: dict[str, tuple] = {}
    for key in keys:
        # Try: full combined text
        ct_norm = normalize(combined_map.get(key, ""))
        if ct_norm in std_norm_to_orig:
            std = std_norm_to_orig[ct_norm]
            results[key] = (std, tax_map[std], 1.0, "exact", False)
            continue
        # Try: just the item_key part
        item_norm = normalize(unslug(key))
        if item_norm in std_norm_to_orig:
            std = std_norm_to_orig[item_norm]
            results[key] = (std, tax_map[std], 1.0, "exact", False)
    return results


# ---------------------------------------------------------------------------
# Dubai KB name lookup
# ---------------------------------------------------------------------------

def build_kb_lookup(dubai_kb_path: Path, tax_map: dict[str, str]) -> dict[str, tuple]:
    """
    Builds {normalized_menu_item_name → (std_term, taxonomy)} from quality KB rows.
    Uses majority-vote per item name to suppress noisy/wrong single entries.
    Only uses rows where KB std_term is in our 572 taxonomy.
    """
    from collections import Counter
    log.info("Building Dubai KB name lookup (majority-vote)...")
    df = pl.read_csv(dubai_kb_path, ignore_errors=True)
    std_col, name_col = "Std terms", "Menu item(name)"

    our_stds_set = set(tax_map.keys())
    valid = df.filter(
        pl.col(std_col).is_not_null()
        & ~pl.col(std_col).str.strip_chars().str.to_lowercase().is_in(["n/a", "n/a ", ""])
        & pl.col(std_col).str.strip_chars().str.to_lowercase().is_in(list(our_stds_set))
    )

    # Accumulate all valid std_term assignments per normalized item name
    votes: dict[str, list[str]] = {}
    for row in valid.select([name_col, std_col]).to_dicts():
        name = row[name_col]
        std  = row[std_col].strip().lower() if row[std_col] else None
        if name and std and std in tax_map:
            key = normalize(name)
            votes.setdefault(key, []).append(std)

    # Majority vote: most common std_term per item name wins
    # Require at least 1 vote (single occurrence also accepted)
    lookup: dict[str, tuple] = {}
    for norm_name, std_list in votes.items():
        best_std = Counter(std_list).most_common(1)[0][0]
        lookup[norm_name] = (best_std, tax_map[best_std])

    log.info("KB lookup ready: %d unique item names (majority-vote)", len(lookup))
    return lookup


def run_kb_match(
    keys: list[str],
    combined_map: dict[str, str],
    kb_lookup: dict[str, tuple],
) -> dict[str, tuple]:
    results: dict[str, tuple] = {}
    for key in keys:
        item_norm = normalize(unslug(key))
        if item_norm in kb_lookup:
            std, tax = kb_lookup[item_norm]
            results[key] = (std, tax, 0.95, "kb_exact", False)
    return results


# ---------------------------------------------------------------------------
# RapidFuzz bulk matching
# ---------------------------------------------------------------------------

def run_fuzzy_match(
    keys: list[str],
    combined_map: dict[str, str],
    std_terms: list[str],
    std_terms_norm: list[str],
    tax_map: dict[str, str],
    chunk_size: int = 5_000,
) -> dict[str, tuple]:
    from rapidfuzz import fuzz, process as rfp

    queries  = [normalize(combined_map.get(k, "")) for k in keys]
    n_chunks = (len(keys) + chunk_size - 1) // chunk_size
    results: dict[str, tuple] = {}

    log.info("RapidFuzz: %d queries × %d targets | %d chunks", len(keys), len(std_terms), n_chunks)
    t0 = time.time()

    for ci in range(0, len(keys), chunk_size):
        chunk_keys    = keys[ci:ci + chunk_size]
        chunk_queries = queries[ci:ci + chunk_size]

        mat = rfp.cdist(
            chunk_queries, std_terms_norm,
            scorer=fuzz.token_set_ratio,
            workers=-1,
            score_cutoff=FUZZY_CUTOFF,
        )
        best_scores = mat.max(axis=1)
        best_idxs   = mat.argmax(axis=1)

        for j, key in enumerate(chunk_keys):
            score = float(best_scores[j])
            if score >= FUZZY_CUTOFF:
                std  = std_terms[int(best_idxs[j])]
                conf = score / 100.0
                rev  = score < FUZZY_ACCEPT
                results[key] = (std, tax_map[std], conf, "fuzzy", rev)

        chunk_n = ci // chunk_size + 1
        if chunk_n % 10 == 0 or chunk_n == n_chunks:
            log.info("  fuzzy chunk %d/%d done", chunk_n, n_chunks)

    log.info("RapidFuzz done in %.1fs | matched %d / %d", time.time() - t0, len(results), len(keys))
    return results


# ---------------------------------------------------------------------------
# TF-IDF cosine matching
# ---------------------------------------------------------------------------

def run_tfidf_match(
    keys: list[str],
    combined_map: dict[str, str],
    std_terms: list[str],
    std_terms_norm: list[str],
    tax_map: dict[str, str],
    chunk_size: int = 5_000,
) -> dict[str, tuple]:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    log.info("TF-IDF: fitting on %d items + %d targets...", len(keys), len(std_terms))
    t0 = time.time()

    queries    = [normalize(combined_map.get(k, "")) for k in keys]
    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True)
    vectorizer.fit(queries + std_terms_norm)
    std_vecs = vectorizer.transform(std_terms_norm)     # 572 × vocab (sparse)

    results: dict[str, tuple] = {}
    for ci in range(0, len(keys), chunk_size):
        chunk_keys    = keys[ci:ci + chunk_size]
        chunk_queries = queries[ci:ci + chunk_size]
        q_vecs  = vectorizer.transform(chunk_queries)   # chunk × vocab (sparse)
        sims    = cosine_similarity(q_vecs, std_vecs)   # chunk × 572 (dense, ~11MB ok)
        best_sc = sims.max(axis=1)
        best_ix = sims.argmax(axis=1)

        for j, key in enumerate(chunk_keys):
            score = float(best_sc[j])
            if score >= TFIDF_MIN:
                std = std_terms[int(best_ix[j])]
                rev = score < TFIDF_ACCEPT
                results[key] = (std, tax_map[std], score, "tfidf", rev)

    log.info("TF-IDF done in %.1fs | matched %d / %d", time.time() - t0, len(results), len(keys))
    return results


# ---------------------------------------------------------------------------
# Sentence-transformer embedding matching
# ---------------------------------------------------------------------------

def run_embed_match(
    keys: list[str],
    combined_map: dict[str, str],
    std_terms: list[str],
    tax_map: dict[str, str],
) -> dict[str, tuple]:
    from sentence_transformers import SentenceTransformer

    log.info("Loading %s...", EMBED_MODEL)
    model = SentenceTransformer(EMBED_MODEL)

    # Embed std_terms (tiny — 572 short strings)
    std_prefixed = [f"query: {s}" for s in std_terms]
    std_embs = model.encode(
        std_prefixed, batch_size=EMBED_BATCH,
        normalize_embeddings=True, show_progress_bar=False,
    )   # (572, 384)

    # Embed queries
    queries = [f"query: {normalize(combined_map.get(k, ''))}" for k in keys]
    log.info("Encoding %d queries with batch_size=%d...", len(queries), EMBED_BATCH)
    t0 = time.time()
    q_embs = model.encode(
        queries, batch_size=EMBED_BATCH,
        normalize_embeddings=True, show_progress_bar=True,
    )   # (N, 384)

    # Cosine sim = dot product on L2-normalized vectors
    sims      = q_embs @ std_embs.T          # (N, 572)
    best_sc   = sims.max(axis=1)
    best_ix   = sims.argmax(axis=1)

    results: dict[str, tuple] = {}
    for j, key in enumerate(keys):
        score = float(best_sc[j])
        if score >= EMBED_MIN:
            std = std_terms[int(best_ix[j])]
            rev = score < EMBED_ACCEPT
            results[key] = (std, tax_map[std], score, "embedding", rev)

    log.info("Embeddings done in %.1fs | matched %d / %d", time.time() - t0, len(results), len(keys))
    return results


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def ensure_output_columns(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            ALTER TABLE talabat_menu_items
            ADD COLUMN IF NOT EXISTS std_term        TEXT,
            ADD COLUMN IF NOT EXISTS taxonomy        TEXT,
            ADD COLUMN IF NOT EXISTS confidence      FLOAT,
            ADD COLUMN IF NOT EXISTS method          TEXT,
            ADD COLUMN IF NOT EXISTS review_required BOOLEAN DEFAULT FALSE;
        """)
    log.info("Output columns confirmed in talabat_menu_items")


async def load_unique_items(pool: asyncpg.Pool) -> pl.DataFrame:
    log.info("Loading unique item_key_clean values...")
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT ON (item_key_clean)
                item_key_clean, menu_category_clean
            FROM talabat_menu_items
            WHERE item_key_clean IS NOT NULL
            ORDER BY item_key_clean
        """)
    return pl.DataFrame({
        "item_key_clean":      [r["item_key_clean"]      for r in rows],
        "menu_category_clean": [r["menu_category_clean"] for r in rows],
    })


async def load_full_id_key(pool: asyncpg.Pool) -> pl.DataFrame:
    """Full 1.1M rows: just id + item_key_clean for the join-back."""
    log.info("Loading full id→item_key_clean map (1.1M rows)...")
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, item_key_clean FROM talabat_menu_items")
    return pl.DataFrame({
        "id":             [r["id"]             for r in rows],
        "item_key_clean": [r["item_key_clean"] for r in rows],
    })


async def bulk_write(pool: asyncpg.Pool, rows: list[tuple]) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS public._std_term_staging;")
        await conn.execute("""
            CREATE TABLE public._std_term_staging (
                id               BIGINT PRIMARY KEY,
                std_term         TEXT,
                taxonomy         TEXT,
                confidence       FLOAT,
                method           TEXT,
                review_required  BOOLEAN
            );
        """)
    async with pool.acquire() as conn:
        await conn.copy_records_to_table(
            "_std_term_staging", records=rows,
            columns=["id", "std_term", "taxonomy", "confidence", "method", "review_required"],
            schema_name="public",
        )
        log.info("Staging populated: %d rows", len(rows))
        result = await conn.execute("""
            UPDATE talabat_menu_items t
            SET
                std_term        = s.std_term,
                taxonomy        = s.taxonomy,
                confidence      = s.confidence,
                method          = s.method,
                review_required = s.review_required
            FROM public._std_term_staging s
            WHERE t.id = s.id;
        """)
        log.info("DB UPDATE: %s", result)
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS public._std_term_staging;")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    log.info("=" * 65)
    log.info("std_term_mapper — UAE Menu Items → 572 std_terms")
    log.info("=" * 65)
    t_start = time.time()

    pool = await asyncpg.create_pool(**LOCAL_PG, min_size=2, max_size=6)
    await ensure_output_columns(pool)

    # ── Load taxonomy ──────────────────────────────────────────────
    tax_df = pl.read_csv(TAXONOMY_CSV)
    std_terms      = [s.strip().lower() for s in tax_df["Std_terms"].to_list()]
    std_terms_norm = [normalize(s) for s in std_terms]
    tax_map        = {s: t.strip() for s, t in zip(std_terms, tax_df["taxonomy"].str.strip_chars().to_list())}
    std_norm_to_orig = {n: o for n, o in zip(std_terms_norm, std_terms)}
    log.info("Taxonomy: %d std_terms loaded", len(std_terms))

    # ── Load master_mapping cache ──────────────────────────────────
    mapping: dict[str, tuple] = {}
    if MASTER_PARQUET.exists():
        cache_df = pl.read_parquet(MASTER_PARQUET)
        for row in cache_df.to_dicts():
            mapping[row["item_key_clean"]] = (
                row["std_term"], row["taxonomy"],
                row["confidence"], row["method"], row["review_required"],
            )
        log.info("Cache loaded: %d entries from master_mapping.parquet", len(mapping))

    # ── Load unique items ──────────────────────────────────────────
    unique_df   = await load_unique_items(pool)
    all_keys    = unique_df["item_key_clean"].to_list()
    cat_map     = dict(zip(unique_df["item_key_clean"].to_list(), unique_df["menu_category_clean"].to_list()))
    combined_map = {k: build_combined(k, cat_map.get(k)) for k in all_keys}

    unmatched = [k for k in all_keys if k not in mapping]
    log.info("Unique keys total: %d | to process: %d | cached: %d",
             len(all_keys), len(unmatched), len(mapping))

    # ── Phase 1: Rule engine ───────────────────────────────────────
    log.info("--- Phase 1: Rule engine ---")
    r = run_rule_engine(unmatched, combined_map, cat_map)
    mapping.update(r); unmatched = [k for k in unmatched if k not in mapping]
    log.info("Rule: +%d matched | %d remaining", len(r), len(unmatched))

    # ── Phase 2: Exact match ───────────────────────────────────────
    log.info("--- Phase 2: Exact match ---")
    r = run_exact_match(unmatched, combined_map, std_norm_to_orig, tax_map)
    mapping.update(r); unmatched = [k for k in unmatched if k not in mapping]
    log.info("Exact: +%d matched | %d remaining", len(r), len(unmatched))

    # KB name lookup intentionally skipped for std_term matching.
    # The Dubai KB has systematic labeling noise (wrong std_term assignments).
    # KB is reserved for Phase 2 (ingredient inheritance by std_term aggregation).

    # ── Phase 3: RapidFuzz ────────────────────────────────────────
    log.info("--- Phase 3: RapidFuzz ---")
    r = run_fuzzy_match(unmatched, combined_map, std_terms, std_terms_norm, tax_map)
    mapping.update(r); unmatched = [k for k in unmatched if k not in mapping]
    log.info("Fuzzy: +%d matched | %d remaining", len(r), len(unmatched))

    # ── Phase 4: TF-IDF ───────────────────────────────────────────
    log.info("--- Phase 4: TF-IDF cosine ---")
    r = run_tfidf_match(unmatched, combined_map, std_terms, std_terms_norm, tax_map)
    mapping.update(r); unmatched = [k for k in unmatched if k not in mapping]
    log.info("TF-IDF: +%d matched | %d remaining", len(r), len(unmatched))

    # ── Phase 5: Embeddings ────────────────────────────────────────
    if unmatched:
        log.info("--- Phase 5: Embeddings (%s) ---", EMBED_MODEL)
        r = run_embed_match(unmatched, combined_map, std_terms, tax_map)
        mapping.update(r); unmatched = [k for k in unmatched if k not in mapping]
        log.info("Embed:  +%d matched | %d remaining (unknown)", len(r), len(unmatched))
    else:
        log.info("--- Phase 5: Embeddings skipped (nothing left) ---")

    # Mark unknowns
    for k in unmatched:
        mapping[k] = (None, None, 0.0, "unknown", True)

    # ── Save master_mapping cache ──────────────────────────────────
    log.info("Saving master_mapping.parquet (%d entries)...", len(mapping))
    pl.DataFrame([
        {"item_key_clean": k, "std_term": v[0], "taxonomy": v[1],
         "confidence": float(v[2]), "method": v[3], "review_required": bool(v[4])}
        for k, v in mapping.items()
    ]).write_parquet(MASTER_PARQUET)

    # ── Coverage stats (unique keys) ──────────────────────────────
    method_counts: dict[str, int] = {}
    for v in mapping.values():
        m = v[3] or "unknown"
        method_counts[m] = method_counts.get(m, 0) + 1
    total_keys = len(mapping)
    log.info("=== Coverage (unique keys) ===")
    for m, cnt in sorted(method_counts.items(), key=lambda x: -x[1]):
        log.info("  %-12s %6d  (%5.1f%%)", m, cnt, 100 * cnt / total_keys)

    # ── Join back to 1.1M rows ────────────────────────────────────
    log.info("Joining unique mapping back to 1.1M rows...")
    full_df = await load_full_id_key(pool)
    mapping_df = pl.DataFrame([
        {"item_key_clean": k, "std_term": v[0], "taxonomy": v[1],
         "confidence": float(v[2]), "method": v[3], "review_required": bool(v[4])}
        for k, v in mapping.items()
    ])
    joined = full_df.join(mapping_df, on="item_key_clean", how="left").with_columns([
        pl.col("confidence").fill_null(0.0),
        pl.col("method").fill_null("unknown"),
        pl.col("review_required").fill_null(True),
    ])
    log.info("Join complete: %d rows", len(joined))

    # ── Write to DB ────────────────────────────────────────────────
    log.info("Writing to PostgreSQL...")
    t0 = time.time()
    rows_tuples = [
        (r["id"], r["std_term"], r["taxonomy"], r["confidence"], r["method"], r["review_required"])
        for r in joined.select(["id", "std_term", "taxonomy", "confidence", "method", "review_required"]).to_dicts()
    ]
    await bulk_write(pool, rows_tuples)
    log.info("DB write done in %.1fs", time.time() - t0)

    # ── Final verification ────────────────────────────────────────
    await pool.close()
    vpool = await asyncpg.create_pool(**LOCAL_PG, min_size=1, max_size=2)
    async with vpool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT
                COUNT(*)                                      AS total,
                COUNT(std_term)                               AS matched,
                COUNT(*) FILTER (WHERE method='rule')         AS by_rule,
                COUNT(*) FILTER (WHERE method='exact')        AS by_exact,
                COUNT(*) FILTER (WHERE method='kb_exact')     AS by_kb,
                COUNT(*) FILTER (WHERE method='fuzzy')        AS by_fuzzy,
                COUNT(*) FILTER (WHERE method='tfidf')        AS by_tfidf,
                COUNT(*) FILTER (WHERE method='embedding')    AS by_embed,
                COUNT(*) FILTER (WHERE method='unknown')      AS unknown,
                COUNT(*) FILTER (WHERE review_required=TRUE)  AS needs_review
            FROM talabat_menu_items;
        """)
    await vpool.close()

    log.info("=" * 65)
    log.info("VERIFICATION — talabat_menu_items")
    log.info("  Total rows          : %s", stats["total"])
    log.info("  Matched (std_term)  : %s  (%.1f%%)",
             stats["matched"], 100 * stats["matched"] / stats["total"])
    log.info("  ├ rule              : %s", stats["by_rule"])
    log.info("  ├ exact             : %s", stats["by_exact"])
    log.info("  ├ KB name lookup    : %s", stats["by_kb"])
    log.info("  ├ fuzzy             : %s", stats["by_fuzzy"])
    log.info("  ├ TF-IDF            : %s", stats["by_tfidf"])
    log.info("  └ embedding         : %s", stats["by_embed"])
    log.info("  Unknown             : %s", stats["unknown"])
    log.info("  Needs review        : %s  (%.1f%%)",
             stats["needs_review"], 100 * stats["needs_review"] / stats["total"])
    log.info("=" * 65)
    log.info("Total pipeline time: %.1fs", time.time() - t_start)


if __name__ == "__main__":
    asyncio.run(main())
