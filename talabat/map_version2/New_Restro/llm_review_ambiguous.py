"""
LLM arbitration for the ONLY two buckets where deterministic geo+fuzzy
matching couldn't resolve a verdict (see match_new_listings.py):

  1. "Review" (710 rows) — geo-close (<=100m) English/Latin names with a
     name-similarity score in the genuinely mixed 45-60 band. Hand-sampling
     this band showed real matches ("SunbeamLanka Restaurant IMPZ Dubai" =
     "Sunbeam Lanka") sitting right next to coincidental non-matches
     ("Kaashi Restaurant" vs "kayarasa restaurant") — no numeric threshold
     separates them cleanly; needs judgment.

  2. Pure-Arabic-only names (129 rows) with a geo-close Talabat candidate —
     these scored 0 by construction (nothing to compare, since the fuzzy
     matcher only extracts Latin text) even though some are likely the same
     business. This is the one place translation genuinely helps, per the
     task's "use OpenAI only if required" instruction — every other row in
     the dataset was resolved without any LLM call.

Everything else (13,083 "New", 4,353 "Matched") is untouched — no LLM cost
spent on rows that already had a confident deterministic answer.

Async + tenacity + RPM-limited, following this project's established
pattern (menu_classifier.py's AsyncTokenBucket).

Run:
  python talabat/map_version2/New_Restro/llm_review_ambiguous.py
"""

import asyncio
import json
import os
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from loguru import logger
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_random_exponential

HERE = Path(__file__).parent
RESULTS_PATH = HERE / "match_results.csv"
OUT_PATH = HERE / "match_results_final.csv"

load_dotenv(HERE.parent.parent / ".env")
API_KEY = os.environ.get("OPEN_AI_API") or os.environ.get("OPENAI_API_KEY", "")
MODEL = "gpt-4o-mini"
RPM_LIMIT = 4200
CONCURRENCY = 20

SYSTEM_PROMPT = """You are verifying whether two nearby location records describe the SAME real-world restaurant/cafe/food business in the UAE, or two DIFFERENT businesses that simply share a building/mall/address (common with cloud kitchens and food courts).

You will get:
- name_a: name from a Google Maps discovery scrape (may be in Arabic, English, or both)
- name_b: an existing Talabat listing name (English)
- distance_m: how many meters apart their coordinates are

Use your knowledge of UAE restaurant brands and Arabic-English transliteration. A shared generic word (restaurant, cafe, grill) is NOT enough evidence alone. Respond with strict JSON: {"same_business": true/false, "confidence": "high"/"medium"/"low"}"""


class AsyncTokenBucket:
    def __init__(self, rpm: int):
        self.capacity = rpm
        self.tokens = rpm
        self.refill_rate = rpm / 60.0
        self.last_refill = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
                self.last_refill = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.refill_rate)


@retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(4), reraise=True)
async def classify_pair(client, bucket, sem, name_a, name_b, distance_m):
    async with sem:
        await bucket.acquire()
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"name_a": name_a, "name_b": name_b, "distance_m": round(distance_m, 1)})},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        usage = resp.usage
        content = json.loads(resp.choices[0].message.content)
        return content, usage.prompt_tokens, usage.completion_tokens


async def main():
    if not API_KEY:
        raise RuntimeError("No OpenAI API key found (checked OPEN_AI_API / OPENAI_API_KEY)")

    df = pd.read_csv(RESULTS_PATH)

    review_mask = df["verdict"] == "Review"
    arabic_close_mask = df["is_arabic_only"] & (df["dist_m"] <= 100)
    to_review = df[review_mask | arabic_close_mask].copy()
    logger.info(f"Rows needing LLM arbitration: {len(to_review):,} (Review={review_mask.sum()}, Arabic-close={arabic_close_mask.sum()})")

    client = AsyncOpenAI(api_key=API_KEY)
    bucket = AsyncTokenBucket(RPM_LIMIT)
    sem = asyncio.Semaphore(CONCURRENCY)

    total_prompt_tok = 0
    total_completion_tok = 0
    results = {}

    async def worker(idx, name_a, name_b, dist):
        nonlocal total_prompt_tok, total_completion_tok
        try:
            content, pt, ct = await classify_pair(client, bucket, sem, name_a, name_b, dist)
            total_prompt_tok += pt
            total_completion_tok += ct
            results[idx] = content
        except Exception as e:
            logger.warning(f"Row {idx} failed after retries: {e}")
            results[idx] = {"same_business": False, "confidence": "low", "error": str(e)}

    tasks = [
        worker(idx, row["Name"], row["nearest_talabat_name"], row["dist_m"])
        for idx, row in to_review.iterrows()
    ]

    t0 = time.time()
    batch_size = 200
    for i in range(0, len(tasks), batch_size):
        await asyncio.gather(*tasks[i : i + batch_size])
        logger.info(f"Progress: {min(i+batch_size, len(tasks))}/{len(tasks)}")

    elapsed = time.time() - t0
    cost = total_prompt_tok / 1_000_000 * 0.15 + total_completion_tok / 1_000_000 * 0.60
    logger.info(f"Done in {elapsed:.1f}s | tokens: {total_prompt_tok:,} in / {total_completion_tok:,} out | est. cost: ${cost:.4f}")

    df["llm_same_business"] = None
    df["llm_confidence"] = None
    for idx, content in results.items():
        df.at[idx, "llm_same_business"] = content.get("same_business")
        df.at[idx, "llm_confidence"] = content.get("confidence")

    def final_verdict(row):
        if row["verdict"] in ("Matched", "New") and pd.isna(row.get("llm_same_business")):
            return row["verdict"]
        if row["llm_same_business"] is True:
            return "Matched"
        if row["llm_same_business"] is False:
            return "New"
        return row["verdict"]

    df["final_verdict"] = df.apply(final_verdict, axis=1)

    print()
    print("Final verdict distribution:")
    print(df["final_verdict"].value_counts())

    df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    logger.info(f"Saved -> {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
