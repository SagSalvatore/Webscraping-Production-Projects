"""
run_classification.py — Main runner.

Steps:
  1. Load restaurant names from tavily-data.csv
  2. Classify via RestaurantClassifier (Tavily + GPT-4o-mini)
  3. Save classification results to output/classification_results.csv
  4. Merge with output/final_output.xlsx  →  output/final_output_classified.xlsx + .csv
"""

import sys
import time
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE        = Path(__file__).parent
PRODUCT_DIR = BASE.parent
DATA_CSV    = BASE / "tavily-data.csv"
OUTPUT_DIR  = PRODUCT_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

CLASS_CSV   = OUTPUT_DIR / "classification_results.csv"
FINAL_XL    = OUTPUT_DIR / "final_output.xlsx"
OUT_XL      = OUTPUT_DIR / "final_output_classified.xlsx"
OUT_CSV     = OUTPUT_DIR / "final_output_classified.csv"

# ── Import (local) ────────────────────────────────────────────────────────────
sys.path.insert(0, str(BASE))
from classifier_async import classify_all_sync   # async engine (fast)
from config import (
    COL_MATCHED_UAE_NAME, COL_CLASSIFICATION, COL_OUTLET_TYPE,
    COL_CHAINED_TYPE, COL_TYPE_OF_REST, COL_CONFIDENCE, COL_REASONING,
)

t0 = time.time()

# ── 1. Load restaurant names ──────────────────────────────────────────────────
print("Loading restaurant names from tavily-data.csv ...")
raw_df = pd.read_csv(DATA_CSV, encoding="latin1", header=None, names=["matched_uae_name"])
raw_names = raw_df["matched_uae_name"].dropna().str.strip().tolist()
print(f"  {len(raw_names)} restaurant names loaded")

# ── 2. Classify ───────────────────────────────────────────────────────────────
print("\nRunning classification (async: concurrent Tavily + OpenAI, Tier-2 4500 RPM) ...")
classifications, classifier = classify_all_sync(raw_names)

# ── 3. Build classification DataFrame ────────────────────────────────────────
cls_rows = []
for raw, res in zip(raw_names, classifications):
    cls_rows.append({
        COL_MATCHED_UAE_NAME : raw,
        COL_CLASSIFICATION   : res[COL_CLASSIFICATION],
        COL_OUTLET_TYPE      : res[COL_OUTLET_TYPE],
        COL_CHAINED_TYPE     : res[COL_CHAINED_TYPE],
        COL_TYPE_OF_REST     : res[COL_TYPE_OF_REST],
        COL_CONFIDENCE       : res[COL_CONFIDENCE],
        COL_REASONING        : res[COL_REASONING],
    })

df_cls = pd.DataFrame(cls_rows)
df_cls.to_csv(CLASS_CSV, index=False, encoding="utf-8-sig")
print(f"\nClassification results saved -> {CLASS_CSV}")
print(df_cls["classification"].value_counts().to_string())
print(f"Confidence breakdown:\n{df_cls['confidence'].value_counts().to_string()}")

# ── 4. Load final_output.xlsx and merge ──────────────────────────────────────
print(f"\nLoading {FINAL_XL} ...")
df_exact = pd.read_excel(FINAL_XL, sheet_name="EXACT")
df_fhigh  = pd.read_excel(FINAL_XL, sheet_name="FUZZY_HIGH")
df_final = pd.concat([df_exact, df_fhigh], ignore_index=True)
print(f"  Loaded {len(df_final)} rows from final_output.xlsx")

# Drop existing (potentially empty) classification columns to avoid conflicts
for col in [COL_OUTLET_TYPE, COL_CHAINED_TYPE, COL_TYPE_OF_REST]:
    if col in df_final.columns:
        df_final.drop(columns=[col], inplace=True)

# Merge: final_output.matched_uae_name  ←→  cls.matched_uae_name
df_merged = df_final.merge(
    df_cls[[COL_MATCHED_UAE_NAME, COL_OUTLET_TYPE, COL_CHAINED_TYPE,
            COL_TYPE_OF_REST, COL_CONFIDENCE, COL_REASONING]],
    on=COL_MATCHED_UAE_NAME,
    how="left",
)

enriched = df_merged[COL_OUTLET_TYPE].notna().sum()
print(f"  Rows enriched with classification: {enriched:,} / {len(df_merged):,}")

# Reorder columns so classification cols appear near the UAE_* group
non_cls_cols  = [c for c in df_merged.columns
                 if c not in [COL_OUTLET_TYPE, COL_CHAINED_TYPE, COL_TYPE_OF_REST,
                               COL_CONFIDENCE, COL_REASONING]]
cls_insert_at = next(
    (i for i, c in enumerate(non_cls_cols) if c.startswith("UAE_")),
    len(non_cls_cols),
)
ordered_cols  = (non_cls_cols[:cls_insert_at]
                 + [COL_TYPE_OF_REST, COL_OUTLET_TYPE, COL_CHAINED_TYPE,
                    COL_CONFIDENCE, COL_REASONING]
                 + non_cls_cols[cls_insert_at:])
df_merged = df_merged[ordered_cols]

# ── 5. Export ─────────────────────────────────────────────────────────────────
print("\nExporting ...")
df_exact_cls = df_merged[df_merged["match_method"] == "EXACT"]
df_fhigh_cls = df_merged[df_merged["match_method"] == "FUZZY_HIGH"]

with pd.ExcelWriter(OUT_XL, engine="openpyxl") as writer:
    df_exact_cls.to_excel(writer, index=False, sheet_name="EXACT")
    df_fhigh_cls.to_excel(writer, index=False, sheet_name="FUZZY_HIGH")
    df_cls.to_excel(writer, index=False, sheet_name="Classification_Audit")

df_merged.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
print(f"  Excel -> {OUT_XL}  ({OUT_XL.stat().st_size/1024/1024:.1f} MB)")
print(f"  CSV   -> {OUT_CSV}  ({OUT_CSV.stat().st_size/1024/1024:.1f} MB)")

print(f"\nTotal time: {time.time()-t0:.1f}s")
print(f"Tavily calls: {classifier.tavily_calls_made}")
print(f"OpenAI calls: {classifier.openai_calls_made}")
print(f"OpenAI tokens: {classifier.total_tokens_used:,}")
est_cost = (classifier.total_tokens_used / 1_000_000) * (0.15 + 0.60) / 2
print(f"Estimated OpenAI cost: ~${est_cost:.4f}")
