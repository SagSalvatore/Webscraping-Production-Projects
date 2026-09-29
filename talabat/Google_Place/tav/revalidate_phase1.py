"""
revalidate_phase1.py — re-checks every Phase-1 "Found=True" name against
the FIXED (stricter) name_supported_by_results guardrail, discovered
necessary after finding 376/679 (55%) of Phase-2 Zomato matches were a
different, unrelated business that merely shared one generic word with
the Talabat name (e.g. "Fuji Delights" matched to "Foodies Delights").

Phase 1's original raw search results were never persisted (kept the
output files smaller), so this re-fetches a fresh Tavily search per name
-- Tavily-only, NO OpenAI calls, so this costs nothing against the OpenAI
budget. Only re-validates the NAME-MATCH guardrail; does not re-run
extraction, so a name whose address was correct is untouched.

Usage:
  python tav/revalidate_phase1.py output/production/*.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
import config  # noqa: E402
from tavily_search import TavilySearcher  # noqa: E402
from validation import name_supported_by_results, has_independent_confirmation  # noqa: E402


def load_all_rows(patterns: list[str]) -> pd.DataFrame:
    files = sorted(f for pattern in patterns for f in glob.glob(pattern))
    rows = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                rows.append(json.loads(line))
    return pd.DataFrame(rows)


async def revalidate_all(found_rows: pd.DataFrame) -> list[dict]:
    searcher = TavilySearcher()
    sem = asyncio.Semaphore(config.TAVILY_MAX_CONCURRENCY)
    results = []
    completed = 0
    total = len(found_rows)

    async def _one(row: dict) -> None:
        nonlocal completed
        name = row["Talabat_Restaurant_Name_Clean"]
        area = row.get("Area_Name")
        async with sem:
            try:
                payload = await searcher.search(name, area)
            except Exception as exc:
                logger.warning(f"[revalidate] search failed for '{name}': {exc}")
                payload = {"results": []}

        still_valid = (
            name_supported_by_results(name, payload)
            and has_independent_confirmation(name, payload)
        )
        results.append({"Talabat_Restaurant_Name_Clean": name, "Still_Valid": still_valid})
        completed += 1
        if completed % 100 == 0 or completed == total:
            logger.info(f"[revalidate] {completed}/{total} re-checked | still_valid={sum(r['Still_Valid'] for r in results)}")

    await asyncio.gather(*(_one(r) for r in found_rows.to_dict("records")))
    return results


def main(patterns: list[str]) -> None:
    df = load_all_rows(patterns)
    found = df[df["Found"]].reset_index(drop=True)
    logger.info(f"Re-validating {len(found)} Phase-1 Found=True names (Tavily-only, no OpenAI cost)...")

    results = asyncio.run(revalidate_all(found))
    result_df = pd.DataFrame(results)

    merged = df.merge(result_df, on="Talabat_Restaurant_Name_Clean", how="left")
    merged["Still_Valid"] = merged["Still_Valid"].fillna(True)  # rows that were never Found=True pass through unchanged

    downgrade_mask = merged["Found"] & ~merged["Still_Valid"]
    n_downgraded = int(downgrade_mask.sum())
    logger.warning(f"Downgrading {n_downgraded} rows that fail the stricter name-match check")

    merged.loc[downgrade_mask, "Found"] = False
    for col in ["Address", "Phone", "Website", "Google_Maps_URL", "Rating", "Review_Count"]:
        if col in merged.columns:
            merged.loc[downgrade_mask, col] = None
    merged.loc[downgrade_mask, "Notes"] = "Downgraded on re-validation: fails stricter name-match check (was a weak single-generic-word match)."

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = config.PRODUCTION_DIR / f"revalidated_phase1_{timestamp}.csv"
    out_json = config.PRODUCTION_DIR / f"revalidated_phase1_{timestamp}.json"
    merged.drop(columns=["Still_Valid"]).to_csv(out_csv, index=False, encoding="utf-8-sig")
    merged.drop(columns=["Still_Valid"]).to_json(out_json, orient="records", indent=2, force_ascii=False)

    print(f"\n{'='*70}")
    print(f"PHASE 1 RE-VALIDATION COMPLETE")
    print(f"{'='*70}")
    print(f"Total names: {len(merged)}")
    print(f"Originally Found=True: {df['Found'].sum()}")
    print(f"Downgraded (false positives caught): {n_downgraded}")
    print(f"Final Found=True: {merged['Found'].sum()}")
    print(f"\nSaved -> {out_csv}")
    print(f"Saved -> {out_json}")
    print(f"{'='*70}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("patterns", nargs="+")
    args = parser.parse_args()
    main(args.patterns)
