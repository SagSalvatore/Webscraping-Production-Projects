"""
Tests for classifier.py — uses mocks so NO real API calls are made.
Run with:  pytest tests/ -v
"""
import json
import pytest
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    COL_CLASSIFICATION, COL_OUTLET_TYPE, COL_CHAINED_TYPE,
    COL_TYPE_OF_REST, COL_CONFIDENCE, COL_REASONING,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-tavily-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")


@pytest.fixture
def classifier_no_cache(mock_env, tmp_path, monkeypatch):
    """Classifier with empty cache, backed by tmp_path."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cache").mkdir()

    with (
        patch("classifier.TavilyClient") as mock_tv,
        patch("classifier.OpenAI") as mock_oa,
    ):
        mock_tv.return_value = MagicMock()
        mock_oa.return_value = MagicMock()
        from classifier import RestaurantClassifier
        clf = RestaurantClassifier()
        clf._tavily_cache  = {}
        clf._results_cache = {}
        yield clf, mock_tv.return_value, mock_oa.return_value


# ── Unit tests ────────────────────────────────────────────────────────────────

class TestPreClassifyInClassifier:
    """Known chains must never touch Tavily or OpenAI."""

    @pytest.mark.parametrize("name,expected_cls", [
        ("McDonald's",             "MNC Chain"),
        ("KFC",                    "MNC Chain"),
        ("Shake Shack",            "MNC Chain"),
        ("ALBAIK - Al Majaz",      "Local Chain"),
        ("ChicKing - Al Nahdha",   "Local Chain"),
        ("Zaatar w Zeit - Motor City", "Local Chain"),
    ])
    def test_known_chains_no_api(self, classifier_no_cache, name, expected_cls):
        clf, mock_tv, mock_oa = classifier_no_cache
        results = clf.classify_all([name])
        assert results[0][COL_CLASSIFICATION] == expected_cls
        mock_tv.search.assert_not_called()
        mock_oa.chat.completions.create.assert_not_called()


class TestTavilySearch:
    def test_search_called_for_unknown(self, classifier_no_cache):
        clf, mock_tv, mock_oa = classifier_no_cache

        # Tavily returns a mock result
        mock_tv.search.return_value = {
            "answer": "Pandecia is a local bakery in Abu Dhabi",
            "results": [{"url": "https://example.com", "content": "Single location cafe"}],
        }

        # OpenAI returns a valid JSON array
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps([{
            "name": "Pandecia Bakery Cafe",
            "classification": "Independent",
            "outlet_type": "Independent",
            "chained_outlet_type": "N/A",
            "type_of_restaurant": "Bakery & Café",
            "confidence": "High",
            "reasoning": "Single location bakery.",
            "locations_found": ["Abu Dhabi"],
        }])
        mock_resp.usage.prompt_tokens = 300
        mock_resp.usage.completion_tokens = 100
        mock_oa.chat.completions.create.return_value = mock_resp

        results = clf.classify_all(["Pandecia Bakery Cafe"])
        assert mock_tv.search.call_count == 1
        assert results[0][COL_CLASSIFICATION] == "Independent"

    def test_tavily_cache_prevents_duplicate_call(self, classifier_no_cache):
        clf, mock_tv, _ = classifier_no_cache
        clf._tavily_cache["pandecia bakery cafe"] = "Cached result"

        result = clf.search("Pandecia Bakery Cafe")
        assert result == "Cached result"
        mock_tv.search.assert_not_called()

    def test_tavily_error_returns_empty_string(self, classifier_no_cache):
        clf, mock_tv, _ = classifier_no_cache
        mock_tv.search.side_effect = Exception("network error")
        result = clf.search("Some Restaurant")
        assert result == ""


class TestOpenAIBatchClassify:
    def _make_openai_response(self, mock_oa, items_data: list[dict]):
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps(items_data)
        mock_resp.usage.prompt_tokens = 400
        mock_resp.usage.completion_tokens = 200
        mock_oa.chat.completions.create.return_value = mock_resp

    def test_batch_of_5_sends_one_openai_call(self, classifier_no_cache):
        clf, mock_tv, mock_oa = classifier_no_cache

        mock_tv.search.return_value = {"answer": "small cafe", "results": []}
        batch_resp = [
            {"name": f"Cafe {i}", "classification": "Independent",
             "outlet_type": "Independent", "chained_outlet_type": "N/A",
             "type_of_restaurant": "Café / Coffee Shop",
             "confidence": "High", "reasoning": "solo cafe", "locations_found": []}
            for i in range(5)
        ]
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps(batch_resp)
        mock_resp.usage.prompt_tokens = 600
        mock_resp.usage.completion_tokens = 250
        mock_oa.chat.completions.create.return_value = mock_resp

        names = [f"Cafe {i}" for i in range(5)]
        results = clf.classify_all(names)
        assert mock_oa.chat.completions.create.call_count == 1
        assert len(results) == 5
        assert all(r[COL_CLASSIFICATION] == "Independent" for r in results)

    def test_invalid_json_returns_unknown(self, classifier_no_cache):
        clf, mock_tv, mock_oa = classifier_no_cache
        mock_tv.search.return_value = {"answer": "test", "results": []}

        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "not valid json {{{"
        mock_resp.usage.prompt_tokens = 100
        mock_resp.usage.completion_tokens = 50
        mock_oa.chat.completions.create.return_value = mock_resp

        results = clf.classify_all(["Unknown Restaurant"])
        assert results[0][COL_CLASSIFICATION] == "Unknown"
        assert results[0][COL_CONFIDENCE] == "Low"

    def test_results_cache_avoids_reprocessing(self, classifier_no_cache):
        clf, mock_tv, mock_oa = classifier_no_cache
        cached = {
            COL_CLASSIFICATION: "Independent",
            COL_OUTLET_TYPE:    "Independent",
            COL_CHAINED_TYPE:   "N/A",
            COL_TYPE_OF_REST:   "Café / Coffee Shop",
            COL_CONFIDENCE:     "High",
            COL_REASONING:      "Cached result.",
        }
        clf._results_cache["Pandecia Bakery Cafe"] = cached

        results = clf.classify_all(["Pandecia Bakery Cafe"])
        mock_tv.search.assert_not_called()
        mock_oa.chat.completions.create.assert_not_called()
        assert results[0][COL_CLASSIFICATION] == "Independent"


class TestCostGuards:
    def test_tavily_limit_stops_search(self, classifier_no_cache):
        clf, mock_tv, _ = classifier_no_cache
        from config import TAVILY_CREDIT_LIMIT
        clf.tavily_calls_made = TAVILY_CREDIT_LIMIT  # simulate limit reached
        result = clf.search("Some New Restaurant")
        assert result == ""
        mock_tv.search.assert_not_called()

    def test_token_counter_increments(self, classifier_no_cache):
        clf, mock_tv, mock_oa = classifier_no_cache
        mock_tv.search.return_value = {"answer": "abc", "results": []}

        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps([{
            "name": "X", "classification": "Independent",
            "outlet_type": "Independent", "chained_outlet_type": "N/A",
            "type_of_restaurant": "Casual Dining Restaurant",
            "confidence": "Medium", "reasoning": "test", "locations_found": [],
        }])
        mock_resp.usage.prompt_tokens = 500
        mock_resp.usage.completion_tokens = 200
        mock_oa.chat.completions.create.return_value = mock_resp

        clf.classify_all(["Unknown Eatery"])
        assert clf.total_tokens_used == 700


class TestOutputStructure:
    """Verify result dicts always have all required keys."""

    REQUIRED_KEYS = {
        COL_CLASSIFICATION, COL_OUTLET_TYPE, COL_CHAINED_TYPE,
        COL_TYPE_OF_REST, COL_CONFIDENCE, COL_REASONING,
    }

    def test_known_chain_has_all_keys(self, classifier_no_cache):
        clf, _, _ = classifier_no_cache
        results = clf.classify_all(["McDonald's"])
        assert self.REQUIRED_KEYS.issubset(set(results[0].keys()))

    def test_api_result_has_all_keys(self, classifier_no_cache):
        clf, mock_tv, mock_oa = classifier_no_cache
        mock_tv.search.return_value = {"answer": "test", "results": []}
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps([{
            "name": "X", "classification": "Independent",
            "outlet_type": "Independent", "chained_outlet_type": "N/A",
            "type_of_restaurant": "Casual Dining Restaurant",
            "confidence": "High", "reasoning": "single location",
            "locations_found": [],
        }])
        mock_resp.usage.prompt_tokens = 200
        mock_resp.usage.completion_tokens = 100
        mock_oa.chat.completions.create.return_value = mock_resp

        results = clf.classify_all(["Some Independent Cafe"])
        assert self.REQUIRED_KEYS.issubset(set(results[0].keys()))
