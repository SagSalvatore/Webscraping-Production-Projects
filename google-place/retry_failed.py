"""
retry_failed.py
---------------
Re-queries only the rows in OUTPUT.csv that have status 'not_found' or 'error'.
Writes the merged results back to OUTPUT.csv and OUTPUT.json.

Usage:
    python retry_failed.py
"""

import os
import sys
import json
import time
import logging
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from tqdm import tqdm

from fetch_places import (
    setup_logging,
    fetch_restaurant,
    write_outputs,
    USER_COLS,
    RATE_SLEEP,
)

load_dotenv()

INPUT_CSV  = "output/OUTPUT.csv"
OUTPUT_CSV = "output/OUTPUT.csv"
OUTPUT_JSON = "output/OUTPUT.json"


def main() -> None:
    logger = setup_logging()

    if not Path(INPUT_CSV).exists():
        logger.error(f"{INPUT_CSV} not found. Run fetch_places.py first.")
        sys.exit(1)

    df = pd.read_csv(INPUT_CSV, dtype=str).fillna("")
    failed = df[df["status"].isin(["not_found", "error"])].copy()

    if failed.empty:
        logger.info("No failed rows to retry. All done!")
        return

    logger.info(f"Retrying {len(failed)} failed restaurantsâ€¦\n")

    updated_rows = []
    for _, row in tqdm(failed.iterrows(), total=len(failed), desc="Retrying"):
        record = fetch_restaurant(row["slug"], row["row_id"], logger)
        updated_rows.append(record)
        time.sleep(RATE_SLEEP)

    # Merge: replace old failed rows with new results
    updated_df = pd.DataFrame(updated_rows)
    df_merged  = df[~df["slug"].isin(updated_df["slug"].tolist())]
    df_merged  = pd.concat([df_merged, updated_df], ignore_index=True)
    df_merged  = df_merged.sort_values("row_id").reset_index(drop=True)

    all_rows = df_merged.to_dict("records")
    write_outputs(all_rows, logger)

    counts = df_merged["status"].value_counts()
    found  = counts.get("found", 0) + counts.get("found_broad", 0)
    logger.info(f"After retry â€” Found: {found} / {len(df_merged)}")


if __name__ == "__main__":
    main()

