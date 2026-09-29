"""
Smoke test: true end-to-end (Tavily search -> OpenAI extraction -> merged
row) on a tiny handful of REAL "Not Found" names from map/output. This is
the last gate before run_sample.py's 100-200 batch.
"""
import asyncio

import pytest

from pipeline import load_unique_notfound_names, process_one
from tavily_search import TavilySearcher
from openai_extract import OpenAIExtractor


@pytest.mark.smoke
async def test_end_to_end_on_five_real_names():
    names_df = load_unique_notfound_names()
    assert len(names_df) > 0
    sample = names_df.sample(n=5, random_state=1).to_dict("records")

    searcher = TavilySearcher()
    extractor = OpenAIExtractor()
    sem_tavily = asyncio.Semaphore(5)
    sem_openai = asyncio.Semaphore(5)

    results = await asyncio.gather(
        *(process_one(row, searcher, extractor, sem_tavily, sem_openai) for row in sample)
    )

    assert len(results) == 5
    for r in results:
        assert "Talabat_Restaurant_Name_Clean" in r
        assert isinstance(r["Found"], bool)
        assert isinstance(r["Confidence"], float)
        print(f"\n[smoke] {r['Talabat_Restaurant_Name_Clean']!r} -> found={r['Found']} conf={r['Confidence']:.2f} addr={r['Address']!r}")

    print(f"\n[smoke] OpenAI cost so far: ${extractor.estimated_cost_usd:.4f}")
    print(f"[smoke] Tavily searches made: {searcher.rotator.searches_made}")
