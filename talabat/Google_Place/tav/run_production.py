"""
run_production.py — full-scale recovery run over ALL remaining "Not Found"
unique names, in checkpointed chunks (mirrors the Apify main-batch workflow:
run a chunk, verify, move on -- never blow the whole pool in one shot).

Resumable: names already recorded in checkpoints/production_checkpoint.json
are skipped automatically, so re-running is safe after an interruption.

Usage:
  python tav/run_production.py --batch-size 500
  python tav/run_production.py --batch-size 500 --limit-batches 1   # just one chunk
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
import config  # noqa: E402
from pipeline import load_unique_notfound_names, load_checkpoint, load_total_cost, run_batch  # noqa: E402


async def main(batch_size: int, limit_batches: int | None, cost_ceiling: float) -> None:
    all_names = load_unique_notfound_names()
    done_names = load_checkpoint(config.PRODUCTION_CHECKPOINT_FILE)
    remaining = all_names[~all_names["Talabat_Restaurant_Name_Clean"].isin(done_names)].reset_index(drop=True)

    logger.info(
        f"Total unique Not-Found names: {len(all_names)} | "
        f"already recovered/attempted: {len(done_names)} | "
        f"remaining: {len(remaining)} | "
        f"cost ceiling: ${cost_ceiling:.2f}"
    )

    if remaining.empty:
        logger.info("Nothing left to process.")
        return

    chunks = [remaining.iloc[i : i + batch_size] for i in range(0, len(remaining), batch_size)]
    if limit_batches:
        chunks = chunks[:limit_batches]

    for i, chunk in enumerate(chunks, start=1):
        spent_so_far = load_total_cost(config.PRODUCTION_CHECKPOINT_FILE)
        if spent_so_far >= cost_ceiling:
            logger.warning(
                f"Cost ceiling (${cost_ceiling:.2f}) reached (${spent_so_far:.4f} spent) -- "
                f"stopping before chunk {i}/{len(chunks)}. Resume later once ready to continue."
            )
            break

        logger.info(
            f"=== Production chunk {i}/{len(chunks)} — {len(chunk)} names "
            f"(${spent_so_far:.4f}/${cost_ceiling:.2f} spent so far) ==="
        )
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        jsonl_path = config.PRODUCTION_DIR / f"production_chunk_{timestamp}.jsonl"

        results = await run_batch(
            chunk.reset_index(drop=True),
            jsonl_path,
            checkpoint_file=config.PRODUCTION_CHECKPOINT_FILE,
            progress_every=50,
            cost_ceiling=cost_ceiling,
        )

        df = pd.DataFrame(results)
        csv_path = config.PRODUCTION_DIR / f"production_chunk_{timestamp}.csv"
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        found = df["Found"].sum()
        total_cost = load_total_cost(config.PRODUCTION_CHECKPOINT_FILE)
        print(
            f"\nChunk {i}/{len(chunks)} done: {len(df)} names, "
            f"{found} recovered ({found/len(df)*100:.1f}%) -> {csv_path} | "
            f"total spent: ${total_cost:.4f}/${cost_ceiling:.2f}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--limit-batches", type=int, default=None)
    parser.add_argument("--cost-ceiling", type=float, default=config.PRODUCTION_COST_CEILING_USD)
    args = parser.parse_args()
    asyncio.run(main(args.batch_size, args.limit_batches, args.cost_ceiling))
