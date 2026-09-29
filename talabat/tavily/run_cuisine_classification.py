"""
run_cuisine_classification.py — Runner for UAE_Cuisine Type + UAE_Sub- cuisine types.

Input  : output/final_output_classified.csv (has UAE_KEY CUISINES per restaurant)
Output : output/cuisine_results.csv
         output/final_output_classified.xlsx  (updated with new cuisine columns)
         output/final_output_classified.csv   (updated)
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import pandas as pd
from openai  import AsyncOpenAI
from tavily  import AsyncTavilyClient

sys.path.insert(0, str(Path(__file__).parent))

from utils               import setup_logging, get_logger, AutoSaveManager
from cuisine_classifier  import CuisineClassifier
from cuisine_config      import (
    COL_CUISINE_TYPE, COL_SUB_CUISINE, COL_CUISINE_CONF,
    COL_CUISINE_REASON, COL_MATCHED_UAE,
)

setup_logging(console_level="INFO", file_level="DEBUG")
log = get_logger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE        = Path(__file__).parent
PRODUCT_DIR = BASE.parent
OUTPUT_DIR  = PRODUCT_DIR / "output"
CACHE_DIR   = BASE / "cache"

INPUT_CSV   = OUTPUT_DIR / "final_output_classified.csv"
CUISINE_CSV = OUTPUT_DIR / "cuisine_results.csv"
AUTOSAVE_P  = CACHE_DIR  / "cuisine_autosave.json"
FINAL_XL    = OUTPUT_DIR / "final_output_classified.xlsx"
OUT_XL      = OUTPUT_DIR / "final_output_classified.xlsx"
OUT_CSV     = OUTPUT_DIR / "final_output_classified.csv"


# ── Dotenv loader ─────────────────────────────────────────────────────────────
def _load_env():
    env = BASE / ".env"
    if env.exists():
        with open(env, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip().strip("\"'"), v.strip().strip("\"'"))

_load_env()


async def main():
    t0 = time.perf_counter()

    # ── 1. Build unique-restaurant records ────────────────────────────────────
    log.info("Loading {}", INPUT_CSV.name)
    df_full = pd.read_csv(INPUT_CSV, low_memory=False, encoding="utf-8-sig")

    # Collapse to one row per restaurant (we classify at restaurant level)
    KEEP_COLS = [
        "matched_uae_name", "UAE_KEY CUISINES",
        "UAE_Type of Restaurants", "classification",
        COL_CUISINE_TYPE, COL_SUB_CUISINE,        # may already have some values
    ]
    existing_cols = [c for c in KEEP_COLS if c in df_full.columns]
    df_restro = (
        df_full[existing_cols]
        .drop_duplicates(subset="matched_uae_name")
        .reset_index(drop=True)
    )
    log.info("Unique restaurants to classify: {}", len(df_restro))

    records = df_restro.to_dict(orient="records")

    # ── 2. Init clients ───────────────────────────────────────────────────────
    oai_key = os.getenv("OPENAI_API_KEY", "").strip("\"'")
    tv_key  = os.getenv("TAVILY_API_KEY", "").strip("\"'")
    if not oai_key:
        raise EnvironmentError("OPENAI_API_KEY not set")
    if not tv_key:
        raise EnvironmentError("TAVILY_API_KEY not set")

    openai_client = AsyncOpenAI(api_key=oai_key)
    tavily_client = AsyncTavilyClient(api_key=tv_key)
    clf           = CuisineClassifier(openai_client, tavily_client)

    # ── 3. Classify with autosave ─────────────────────────────────────────────
    CACHE_DIR.mkdir(exist_ok=True)
    async with AutoSaveManager(AUTOSAVE_P, flush_every=10) as saver:
        results = await clf.classify_all(records, autosave=saver)

    log.info("Completed in {:.1f}s", time.perf_counter() - t0)

    # ── 4. Build cuisine_results DataFrame ────────────────────────────────────
    rows = []
    for rec, res in zip(records, results):
        rows.append({
            COL_MATCHED_UAE   : rec["matched_uae_name"],
            COL_CUISINE_TYPE  : res[COL_CUISINE_TYPE],
            COL_SUB_CUISINE   : res[COL_SUB_CUISINE],
            COL_CUISINE_CONF  : res[COL_CUISINE_CONF],
            COL_CUISINE_REASON: res[COL_CUISINE_REASON],
        })
    df_cuisine = pd.DataFrame(rows)
    df_cuisine.to_csv(CUISINE_CSV, index=False, encoding="utf-8-sig")
    log.info("cuisine_results.csv saved → {} rows", len(df_cuisine))
    log.info(
        "Cuisine Type distribution:\n{}",
        df_cuisine[COL_CUISINE_TYPE].value_counts().to_string(),
    )

    # ── 5. Merge back into full dataset ──────────────────────────────────────
    log.info("Merging cuisine into final_output_classified ...")

    # Drop old cuisine cols from full dataset
    df_merged = df_full.drop(
        columns=[COL_CUISINE_TYPE, COL_SUB_CUISINE], errors="ignore"
    )

    # Merge on restaurant name
    df_merged = df_merged.merge(
        df_cuisine[[COL_MATCHED_UAE, COL_CUISINE_TYPE, COL_SUB_CUISINE]],
        on=COL_MATCHED_UAE,
        how="left",
    )

    # Move cuisine columns to sit after UAE_Chained Outlet Type
    cols = list(df_merged.columns)
    for col in [COL_CUISINE_TYPE, COL_SUB_CUISINE]:
        if col in cols:
            cols.remove(col)
    anchor = "UAE_Chained Outlet Type"
    if anchor in cols:
        idx = cols.index(anchor) + 1
        cols.insert(idx, COL_SUB_CUISINE)
        cols.insert(idx, COL_CUISINE_TYPE)
    df_merged = df_merged[cols]

    # Save CSV
    df_merged.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    log.info("CSV  → {} ({:.1f} MB)", OUT_CSV.name, OUT_CSV.stat().st_size/1024/1024)

    # Save Excel (3 sheets + new Cuisine_Audit)
    df_e = df_merged[df_merged["match_method"] == "EXACT"]
    df_h = df_merged[df_merged["match_method"] == "FUZZY_HIGH"]
    try:
        df_audit_cls = pd.read_excel(FINAL_XL, sheet_name="Classification_Audit")
        df_audit_typ = pd.read_excel(FINAL_XL, sheet_name="Type_Audit")
    except Exception:
        df_audit_cls = pd.DataFrame()
        df_audit_typ = pd.DataFrame()

    with pd.ExcelWriter(OUT_XL, engine="openpyxl") as writer:
        df_e.to_excel(writer, index=False, sheet_name="EXACT")
        df_h.to_excel(writer, index=False, sheet_name="FUZZY_HIGH")
        if not df_audit_cls.empty:
            df_audit_cls.to_excel(writer, index=False, sheet_name="Classification_Audit")
        if not df_audit_typ.empty:
            df_audit_typ.to_excel(writer, index=False, sheet_name="Type_Audit")
        df_cuisine.to_excel(writer, index=False, sheet_name="Cuisine_Audit")

    log.info("Excel → {} ({:.1f} MB)", OUT_XL.name, OUT_XL.stat().st_size/1024/1024)
    log.info(
        "=== DONE | OpenAI: {} calls | Tavily: {} calls | Tokens: {} | Cost: ~${:.4f} ===",
        clf.openai_calls, clf.tavily_calls, clf.tokens_used,
        (clf.tokens_used / 1_000_000) * 0.375,
    )


if __name__ == "__main__":
    asyncio.run(main())
