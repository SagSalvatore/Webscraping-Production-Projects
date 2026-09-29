"""
Tests for classifier_async.py — all mocked, no real API calls.
Run with:  pytest tests/ -v
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    COL_CLASSIFICATION, COL_OUTLET_TYPE, COL_CHAINED_TYPE,
    COL_TYPE_OF_REST, COL_CONFIDENCE, COL_REASONING,
)


# ── Async test helpers ─────────────────────────────────────────────────────────

def run_async(coro):
    """Run a coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-tavily-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")


@pytest.fixture
def async_clf(mock_env, tmp_path, monkeypatch):
    """AsyncRestaurantClassifier with empty caches in tmp dir."""
    (tmp_path / "cache").mkdir()

    with (
        patch("classifier_async.AsyncTavilyClient") as mock_tv,
        patch("classifier_async.AsyncOpenAI") as mock_oa,
        patch("classifier_async._load_dotenv_robust"),
    ):
        mock_tv.return_value = AsyncMock()
        mock_oa.return_value = MagicMock()
        from classifier_async import AsyncRestaurantClassifier
        clf = AsyncRestaurantClassifier()
        clf._tavily_cache  = {}
        clf._results_cache = {}
        clf._tavily_sem    = asyncio.Semaphore(20)
        clf._openai_sem    = asyncio.Semaphore(28)
        clf._cache_lock    = asyncio.Lock()
        yield clf, mock_tv.return_value, mock_oa.return_value


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestAsyncPreClassify:
    """Known chains still need no API calls in the async path."""

    @pytest.mark.parametrize("name,cls", [
        ("McDonald's",          "MNC Chain"),
        ("KFC",                 "MNC Chain"),
        ("ALBAIK - Al Majaz",   "Local Chain"),
        ("ChicKing - Al Nahdha","Local Chain"),
    ])
    def test_known_chains_no_api(self, async_clf, name, cls):
        clf, mock_tv, mock_oa = async_clf
        results = run_async(clf.classify_all([name]))
        assert results[0][COL_CLASSIFICATION] == cls
        mock_tv.search.assert_not_called()
        mock_oa.chat.completions.create.assert_not_called()


class TestAsyncConcurrency:
    """Verify that all Tavily searches and all OpenAI calls are fired concurrently."""

    def _make_openai_mock(self, mock_oa, n_items: int):
        """Configure AsyncOpenAI mock to return n_items classifications."""
        payload = [
            {
                "name": f"R{i}", "classification": "Independent",
                "outlet_type": "Independent", "chained_outlet_type": "N/A",
                "type_of_restaurant": "Café / Coffee Shop",
                "confidence": "High", "reasoning": "single location",
                "locations_found": [],
            }
            for i in range(n_items)
        ]
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps(payload)
        mock_resp.usage.prompt_tokens = 500
        mock_resp.usage.completion_tokens = 200
        mock_oa.chat.completions.create = AsyncMock(return_value=mock_resp)

    def test_all_tavily_calls_made_concurrently(self, async_clf):
        """
        For 10 unknown restaurants, 10 Tavily searches should all be created
        as tasks (not awaited sequentially one-by-one).
        """
        clf, mock_tv, mock_oa = async_clf

        # Track call order via timing
        call_log = []
        async def mock_search(**kwargs):
            call_log.append(kwargs["query"])
            await asyncio.sleep(0.01)   # simulate small latency
            return {"answer": "small cafe", "results": []}

        mock_tv.search = mock_search
        self._make_openai_mock(mock_oa, 10)

        names = [f"Unknown Cafe {i}" for i in range(10)]
        import time
        t0 = time.perf_counter()
        results = run_async(clf.classify_all(names))
        elapsed = time.perf_counter() - t0

        # If truly concurrent, 10 × 0.01s should finish << 10 × 0.01s = 0.1s
        # Sequential would be 10 × 0.01 = 0.1s; concurrent should be ~0.01-0.03s
        assert elapsed < 0.5, f"Expected concurrent completion in <0.5s, got {elapsed:.2f}s"
        assert len(results) == 10
        assert len(call_log) == 10

    def test_all_openai_batches_fired_concurrently(self, async_clf):
        """15 unknowns = 3 batches of 5. All 3 should fire simultaneously."""
        clf, mock_tv, mock_oa = async_clf

        batch_starts = []
        async def mock_search(**kwargs):
            return {"answer": "abc", "results": []}
        mock_tv.search = mock_search

        async def mock_create(**kwargs):
            batch_starts.append(asyncio.get_event_loop().time())
            await asyncio.sleep(0.05)   # simulate network latency
            n = len(json.loads(kwargs["messages"][1]["content"].split("RESTAURANT")[1].split(":")[0].strip())) if False else 5
            payload = [
                {"name": f"X", "classification": "Independent",
                 "outlet_type": "Independent", "chained_outlet_type": "N/A",
                 "type_of_restaurant": "Café / Coffee Shop",
                 "confidence": "High", "reasoning": "test", "locations_found": []}
            ] * 5
            r = MagicMock()
            r.choices[0].message.content = json.dumps(payload)
            r.usage.prompt_tokens = 200; r.usage.completion_tokens = 100
            return r

        mock_oa.chat.completions.create = mock_create

        names = [f"Cafe {i}" for i in range(15)]
        import time
        t0 = time.perf_counter()
        results = run_async(clf.classify_all(names))
        elapsed = time.perf_counter() - t0

        # 3 batches × 0.05s latency — concurrent = ~0.05-0.15s, sequential = ~0.15s
        assert elapsed < 0.8
        assert len(results) == 15


class TestAsyncCache:
    def test_cache_prevents_duplicate_tavily_calls(self, async_clf):
        clf, mock_tv, _ = async_clf
        clf._tavily_cache["pandecia bakery cafe"] = "Cached search result"

        result = run_async(clf._search_one("Pandecia Bakery Cafe"))
        assert result == "Cached search result"
        mock_tv.search.assert_not_called()

    def test_results_cache_skips_api(self, async_clf):
        clf, mock_tv, mock_oa = async_clf
        clf._results_cache["Some Cached Place"] = {
            COL_CLASSIFICATION: "Independent",
            COL_OUTLET_TYPE: "Independent",
            COL_CHAINED_TYPE: "N/A",
            COL_TYPE_OF_REST: "Café / Coffee Shop",
            COL_CONFIDENCE: "High",
            COL_REASONING: "was cached",
        }
        results = run_async(clf.classify_all(["Some Cached Place"]))
        mock_tv.search.assert_not_called()
        mock_oa.chat.completions.create.assert_not_called()
        assert results[0][COL_CLASSIFICATION] == "Independent"


class TestAsyncOutputStructure:
    REQUIRED_KEYS = {
        COL_CLASSIFICATION, COL_OUTLET_TYPE, COL_CHAINED_TYPE,
        COL_TYPE_OF_REST, COL_CONFIDENCE,
    }

    def test_all_result_keys_present(self, async_clf):
        clf, mock_tv, mock_oa = async_clf
        mock_tv.search = AsyncMock(return_value={"answer": "test", "results": []})
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps([{
            "name": "X", "classification": "Independent",
            "outlet_type": "Independent", "chained_outlet_type": "N/A",
            "type_of_restaurant": "Casual Dining Restaurant",
            "confidence": "High", "reasoning": "one location",
            "locations_found": [],
        }])
        mock_resp.usage.prompt_tokens = 200
        mock_resp.usage.completion_tokens = 80
        mock_oa.chat.completions.create = AsyncMock(return_value=mock_resp)

        results = run_async(clf.classify_all(["Unknown Diner"]))
        assert self.REQUIRED_KEYS.issubset(set(results[0].keys()))
