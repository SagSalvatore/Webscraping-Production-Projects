"""
scrape_platform_urls.py — Phase 2: fetch every Zomato/Deliveroo/noon Food
URL collected during a run_sample.py / run_production.py pass, and pull
structured address/phone/rating data from each one's JSON-LD.

Deliberately separate from the main pipeline (which only COLLECTS these
URLs, cheaply, with no HTTP calls) so platform rate-limiting never blocks
or slows down the Tavily+OpenAI loop -- this phase paces itself.

Usage:
  python tav/scrape_platform_urls.py tav/output/sample/sample_20260704_171252.jsonl
  python tav/scrape_platform_urls.py tav/output/production/*.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd
from loguru import logger

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
import config  # noqa: E402
from platform_scrape import scrape_and_validate  # noqa: E402

PLATFORM_SCRAPE_DIR = config.OUTPUT_DIR / "platform_scrape"
MAX_CONCURRENCY = 6  # deliberately modest -- these are real sites, not our own Apify/Tavily infra


def load_url_candidates(input_paths: list[Path]) -> dict[str, list[str]]:
    """Read one or more JSONL result files and build url -> [candidate Talabat names]."""
    url_to_names: dict[str, list[str]] = defaultdict(list)
    for path in input_paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                name = row.get("Talabat_Restaurant_Name_Clean")
                for url in row.get("Platform_URLs_Found") or []:
                    if name not in url_to_names[url]:
                        url_to_names[url].append(name)
    return dict(url_to_names)


def infer_platform(url: str) -> str:
    from platform_scrape import PLATFORM_DOMAINS
    for domain, platform in PLATFORM_DOMAINS.items():
        if domain in url:
            return platform
    return "unknown"


async def scrape_all(url_candidates: dict[str, list[str]]) -> list[dict]:
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    results: list[dict] = []
    completed = 0
    total = len(url_candidates)

    async def _one(url: str, names: list[str], client: httpx.AsyncClient) -> None:
        nonlocal completed
        platform = infer_platform(url)
        async with sem:
            listing = await scrape_and_validate(url, platform, names, client)
        completed += 1
        if completed % 10 == 0 or completed == total:
            logger.info(f"[scrape_platform_urls] {completed}/{total} URLs processed")
        results.append({
            "URL": url,
            "Platform": platform,
            "Candidate_Talabat_Names": names,
            "Scraped_OK": listing is not None,
            "Matched_Talabat_Name": listing.get("matched_talabat_name") if listing else None,
            "Listing_Name": listing.get("name") if listing else None,
            "Address": listing.get("address") if listing else None,
            "Phone": listing.get("phone") if listing else None,
            "Rating": listing.get("rating") if listing else None,
            "Review_Count": listing.get("review_count") if listing else None,
            "Latitude": listing.get("latitude") if listing else None,
            "Longitude": listing.get("longitude") if listing else None,
        })

    async with httpx.AsyncClient() as client:
        await asyncio.gather(*(_one(u, n, client) for u, n in url_candidates.items()))
    return results


def main(input_patterns: list[str]) -> None:
    input_paths = [Path(p) for pattern in input_patterns for p in glob.glob(pattern)]
    if not input_paths:
        logger.error(f"No files matched: {input_patterns}")
        return

    logger.info(f"Reading platform URLs collected in: {[p.name for p in input_paths]}")
    url_candidates = load_url_candidates(input_paths)
    logger.info(f"{len(url_candidates)} unique platform URLs to scrape")

    by_platform: dict[str, int] = defaultdict(int)
    for url in url_candidates:
        by_platform[infer_platform(url)] += 1
    for platform, count in by_platform.items():
        logger.info(f"  {platform}: {count} URLs")

    if not url_candidates:
        return

    results = asyncio.run(scrape_all(url_candidates))
    df = pd.DataFrame(results)

    PLATFORM_SCRAPE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = PLATFORM_SCRAPE_DIR / f"enrichment_{timestamp}.csv"
    json_path = PLATFORM_SCRAPE_DIR / f"enrichment_{timestamp}.json"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_json(json_path, orient="records", indent=2, force_ascii=False)

    ok = df["Scraped_OK"].sum()
    print(f"\n{'='*70}")
    print(f"PLATFORM SCRAPE COMPLETE — {len(df)} URLs attempted")
    print(f"{'='*70}")
    print(f"Successfully scraped + name-validated: {ok} ({ok/len(df)*100:.1f}%)")
    for platform in by_platform:
        sub = df[df["Platform"] == platform]
        sub_ok = sub["Scraped_OK"].sum()
        print(f"  {platform}: {sub_ok}/{len(sub)} succeeded")
    print(f"\nSaved -> {csv_path}")
    print(f"Saved -> {json_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", help="JSONL result file(s) or glob pattern(s)")
    args = parser.parse_args()
    main(args.inputs)
