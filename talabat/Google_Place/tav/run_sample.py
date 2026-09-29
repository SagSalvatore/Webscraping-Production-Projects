"""
run_sample.py — run the Tavily+OpenAI recovery pipeline on a RANDOM sample
of N unique "Not Found" names (default 150). This is the mandatory sanity
check before any production-scale run: confirms real recovery rate and
real cost-per-name on live data before spending API budget on all ~5,145
names.

Usage:
  python tav/run_sample.py --size 150 --seed 42
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
from pipeline import load_unique_notfound_names, run_batch  # noqa: E402


async def main(sample_size: int, seed: int) -> None:
    all_names = load_unique_notfound_names()
    logger.info(f"Loaded {len(all_names)} unique Not-Found names from {config.NOT_FOUND_CSV.name}")

    sample = all_names.sample(n=min(sample_size, len(all_names)), random_state=seed).reset_index(drop=True)
    logger.info(f"Random sample drawn: {len(sample)} names (seed={seed})")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = config.SAMPLE_DIR / f"sample_{timestamp}.jsonl"

    results = await run_batch(sample, jsonl_path, checkpoint_file=None, progress_every=10)

    df = pd.DataFrame(results)
    csv_path = config.SAMPLE_DIR / f"sample_{timestamp}.csv"
    json_path = config.SAMPLE_DIR / f"sample_{timestamp}.json"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_json(json_path, orient="records", indent=2, force_ascii=False)

    found = df["Found"].sum()
    is_chain = (df["Address"] == "Chain").sum()
    with_address = df[(df["Found"]) & (df["Address"] != "Chain")]["Address"].notna().sum()
    with_phone = df[df["Found"]]["Phone"].notna().sum()
    maps_urls = df[df["Found"]]["Google_Maps_URL"].fillna("")
    maps_verified_real = (~maps_urls.str.contains("/maps/search/") & (maps_urls != "")).sum()
    maps_constructed = maps_urls.str.contains("/maps/search/").sum()

    print(f"\n{'='*70}")
    print(f"SAMPLE RUN COMPLETE — {len(df)} names tested")
    print(f"{'='*70}")
    print(f"Recovered (Found=true)      : {found} ({found/len(df)*100:.1f}%)")
    print(f"  With a specific address   : {with_address}")
    print(f"  Flagged 'Chain' (no single address, needs manual follow-up): {is_chain}")
    print(f"  With phone                : {with_phone}")
    print(f"  Verified-real Maps link   : {maps_verified_real}")
    print(f"  Safe constructed Maps link: {maps_constructed}")
    print(f"\nSaved -> {csv_path}")
    print(f"Saved -> {json_path}")
    print(f"Raw JSONL -> {jsonl_path}")
    print(f"{'='*70}")
    print("\nReview these results before running the full production batch.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=config.DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=config.DEFAULT_SAMPLE_SEED)
    args = parser.parse_args()
    asyncio.run(main(args.size, args.seed))
