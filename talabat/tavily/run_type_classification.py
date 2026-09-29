"""
run_type_classification.py — Runner for UAE_Type of Restaurants re-classification.

Steps:
  1. Load classification_results.csv (170 rows) — has name, existing type, reasoning
  2. Run TypeClassifier async (all batches concurrent, jitter backoff, autosave)
  3. Save type_results.csv — one row per restaurant with new 5-category type
  4. Merge into final_output_classified.xlsx / .csv
"""

import sys
import asyncio
import time
import os
from pathlib import Path

import pandas as pd
from openai import AsyncOpenAI

sys.path.insert(0, str(Path(__file__).parent))

from utils              import setup_logging, get_logger, AutoSaveManager
from type_classifier    import TypeClassifier, VALID_TYPES, MAX_CONCURRENT

setup_logging(console_level="INFO", file_level="DEBUG")
log = get_logger(__name__)

# ── Dotenv loader (handles "KEY"=value format) ────────────────────────────────
def _load_env():
    env = Path(__file__).parent / ".env"
    if env.exists():
        with open(env, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip().strip("\"'")
                v = v.strip().strip("\"'")
                if k:
                    os.environ.setdefault(k, v)

_load_env()

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE        = Path(__file__).parent
PRODUCT_DIR = BASE.parent
OUTPUT_DIR  = PRODUCT_DIR / "output"
CACHE_DIR   = BASE / "cache"

INPUT_CSV   = OUTPUT_DIR / "classification_results.csv"
TYPE_CSV    = OUTPUT_DIR / "type_results.csv"
AUTOSAVE_P  = CACHE_DIR  / "type_autosave.json"
FINAL_XL    = OUTPUT_DIR / "final_output_classified.xlsx"
FINAL_CSV   = OUTPUT_DIR / "final_output_classified.csv"
OUT_XL      = OUTPUT_DIR / "final_output_classified.xlsx"   # overwrite in-place
OUT_CSV     = OUTPUT_DIR / "final_output_classified.csv"


# ── Main async pipeline ───────────────────────────────────────────────────────

async def main():
    t0 = time.perf_counter()

    # 1. Load input
    log.info("Loading classification_results.csv ...")
    df_input = pd.read_csv(INPUT_CSV, encoding="utf-8-sig")
    records  = df_input.to_dict(orient="records")
    log.info("Loaded {} restaurants", len(records))

    # 2. OpenAI client + classifier
    api_key = os.getenv("OPENAI_API_KEY", "").strip("\"'")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY not set")

    client = AsyncOpenAI(api_key=api_key)
    sem    = asyncio.Semaphore(MAX_CONCURRENT)
    clf    = TypeClassifier(openai_client=client, sem=sem)

    # 3. Run with autosave (resumes if interrupted)
    CACHE_DIR.mkdir(exist_ok=True)
    async with AutoSaveManager(AUTOSAVE_P, flush_every=10) as saver:
        results = await clf.classify_all(records, autosave=saver)

    elapsed = time.perf_counter() - t0
    log.info("Finished in {:.1f}s", elapsed)

    # 4. Build type_results DataFrame
    rows = []
    for rec, res in zip(records, results):
        rows.append({
            "matched_uae_name"      : rec["matched_uae_name"],
            "UAE_Type of Restaurants": res["type"],
            "type_confidence"       : res["confidence"],
            "type_reasoning"        : res["reasoning"],
        })
    df_types = pd.DataFrame(rows)

    # Validate all values are in VALID_TYPES
    invalid = df_types[~df_types["UAE_Type of Restaurants"].isin(VALID_TYPES)]
    if len(invalid):
        log.warning("{} rows have invalid type — check type_results.csv", len(invalid))
        log.warning("{}", invalid[["matched_uae_name","UAE_Type of Restaurants"]].to_string())

    df_types.to_csv(TYPE_CSV, index=False, encoding="utf-8-sig")
    log.info("type_results.csv saved → {} ({:.0f} KB)", TYPE_CSV, TYPE_CSV.stat().st_size/1024)

    # 5. Merge into final_output_classified
    log.info("Merging into final_output_classified files ...")
    df_final_e = pd.read_excel(FINAL_XL, sheet_name="EXACT")
    df_final_h = pd.read_excel(FINAL_XL, sheet_name="FUZZY_HIGH")
    df_audit   = pd.read_excel(FINAL_XL, sheet_name="Classification_Audit")

    def _merge_types(df: pd.DataFrame) -> pd.DataFrame:
        # Drop old UAE_Type of Restaurants column
        df = df.drop(columns=["UAE_Type of Restaurants"], errors="ignore")
        # Merge new types
        df = df.merge(
            df_types[["matched_uae_name", "UAE_Type of Restaurants"]],
            on="matched_uae_name",
            how="left",
        )
        # Move the column next to UAE_Outlet Type
        cols = list(df.columns)
        if "UAE_Type of Restaurants" in cols and "UAE_Outlet Type" in cols:
            cols.remove("UAE_Type of Restaurants")
            insert_at = cols.index("UAE_Outlet Type")
            cols.insert(insert_at, "UAE_Type of Restaurants")
            df = df[cols]
        return df

    df_final_e = _merge_types(df_final_e)
    df_final_h = _merge_types(df_final_h)

    with pd.ExcelWriter(OUT_XL, engine="openpyxl") as writer:
        df_final_e.to_excel(writer, index=False, sheet_name="EXACT")
        df_final_h.to_excel(writer, index=False, sheet_name="FUZZY_HIGH")
        df_audit.to_excel(writer, index=False, sheet_name="Classification_Audit")
        df_types.to_excel(writer, index=False, sheet_name="Type_Audit")

    df_all = pd.concat([df_final_e, df_final_h], ignore_index=True)
    df_all.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    log.info(
        "Outputs saved → {} ({:.1f} MB) | {} ({:.1f} MB)",
        OUT_XL.name, OUT_XL.stat().st_size/1024/1024,
        OUT_CSV.name, OUT_CSV.stat().st_size/1024/1024,
    )

    # 6. Summary
    log.info("=== Summary ===")
    log.info("Type distribution:\n{}", df_types["UAE_Type of Restaurants"].value_counts().to_string())
    log.info("OpenAI calls: {} | Tokens: {} | Cost: ~${:.4f}",
             clf.calls_made, clf.tokens_used,
             (clf.tokens_used / 1_000_000) * 0.375)


if __name__ == "__main__":
    asyncio.run(main())
