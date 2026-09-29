"""
Tests for type_classifier.py + utils/ modules.
All mocked — no real API calls.
Run: pytest tests/ -v
"""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from type_classifier import (
    TypeClassifier, _normalise_type, _parse_response,
    VALID_TYPES, DIRECT_MAP, _build_type_prompt,
)
from utils.retry   import retry_async
from utils.autosave import AutoSaveManager


# ── Helpers ───────────────────────────────────────────────────────────────────

def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def make_openai_mock(type_list: list[str]):
    """Return an AsyncMock that yields a response with the given type list."""
    payload = [
        {
            "restaurant_index": i + 1,
            "name": f"R{i}",
            "type": t,
            "confidence": "High",
            "reasoning": "test",
        }
        for i, t in enumerate(type_list)
    ]
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = json.dumps(payload)
    mock_resp.usage.prompt_tokens   = 300
    mock_resp.usage.completion_tokens = 100
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=mock_resp)
    return client


@pytest.fixture
def sem():
    return asyncio.Semaphore(5)


# ══════════════════════════════════════════════════════════════════════════════
# 1. VALID_TYPES sanity
# ══════════════════════════════════════════════════════════════════════════════

class TestValidTypes:
    def test_exactly_5_categories(self):
        assert len(VALID_TYPES) == 5

    @pytest.mark.parametrize("t", [
        "Full-Service Restaurants",
        "Quick-Service Restaurants",
        "Cafes",
        "Bakery",
        "Cloud-Kitchen",
    ])
    def test_all_5_present(self, t):
        assert t in VALID_TYPES


# ══════════════════════════════════════════════════════════════════════════════
# 2. _normalise_type
# ══════════════════════════════════════════════════════════════════════════════

class TestNormaliseType:
    @pytest.mark.parametrize("raw,expected", [
        ("Full-Service Restaurants",   "Full-Service Restaurants"),
        ("full service restaurants",   "Full-Service Restaurants"),
        ("Full Service",               "Full-Service Restaurants"),
        ("Quick-Service Restaurants",  "Quick-Service Restaurants"),
        ("QSR",                        "Quick-Service Restaurants"),
        ("fast food",                  "Quick-Service Restaurants"),
        ("Cafes",                      "Cafes"),
        ("Café / Coffee Shop",         "Cafes"),
        ("coffee shop",                "Cafes"),
        ("Bakery",                     "Bakery"),
        ("Patisserie",                 "Bakery"),
        ("pastry shop",                "Bakery"),
        ("Cloud-Kitchen",              "Cloud-Kitchen"),
        ("Virtual Kitchen",            "Cloud-Kitchen"),
        ("delivery only",              "Cloud-Kitchen"),
    ])
    def test_normalise(self, raw, expected):
        assert _normalise_type(raw) == expected

    def test_unknown_defaults_to_full_service(self):
        assert _normalise_type("XYZ Unknown") == "Full-Service Restaurants"

    def test_empty_string_defaults(self):
        assert _normalise_type("") == "Full-Service Restaurants"


# ══════════════════════════════════════════════════════════════════════════════
# 3. DIRECT_MAP coverage
# ══════════════════════════════════════════════════════════════════════════════

class TestDirectMap:
    @pytest.mark.parametrize("src,expected_target", [
        ("Casual Dining Restaurant",       "Full-Service Restaurants"),
        ("Fine Dining Restaurant",         "Full-Service Restaurants"),
        ("Quick-Service Restaurant (QSR)", "Quick-Service Restaurants"),
        ("Café / Coffee Shop",             "Cafes"),
        ("Coffee Shop",                    "Cafes"),
        ("Patisserie / Dessert Shop",      "Bakery"),
        ("Patisserie",                     "Bakery"),
        ("Cloud Kitchen",                  "Cloud-Kitchen"),
        ("Fast-Casual Restaurant",         "Quick-Service Restaurants"),
    ])
    def test_direct_map_values(self, src, expected_target):
        assert DIRECT_MAP[src] == expected_target

    def test_all_direct_map_targets_are_valid(self):
        for src, tgt in DIRECT_MAP.items():
            assert tgt in VALID_TYPES, f"DIRECT_MAP['{src}'] = '{tgt}' is not in VALID_TYPES"


# ══════════════════════════════════════════════════════════════════════════════
# 4. _parse_response
# ══════════════════════════════════════════════════════════════════════════════

class TestParseResponse:
    def test_valid_json_array(self):
        raw = json.dumps([
            {"type": "Cafes", "confidence": "High", "reasoning": "coffee shop"},
            {"type": "Bakery", "confidence": "Medium", "reasoning": "bakery"},
        ])
        result = _parse_response(raw, 2)
        assert len(result) == 2
        assert result[0]["type"] == "Cafes"
        assert result[1]["type"] == "Bakery"

    def test_wrapped_in_dict_results_key(self):
        raw = json.dumps({"results": [
            {"type": "Quick-Service Restaurants", "confidence": "High", "reasoning": "QSR"},
        ]})
        result = _parse_response(raw, 1)
        assert result[0]["type"] == "Quick-Service Restaurants"

    def test_normalises_raw_variant(self):
        raw = json.dumps([{"type": "quick service", "confidence": "High", "reasoning": ""}])
        result = _parse_response(raw, 1)
        assert result[0]["type"] == "Quick-Service Restaurants"

    def test_pads_if_fewer_items_returned(self):
        raw = json.dumps([{"type": "Cafes", "confidence": "High", "reasoning": "one item"}])
        result = _parse_response(raw, 3)   # expect 3, got 1
        assert len(result) == 3
        assert result[0]["type"] == "Cafes"
        assert result[1]["type"] == "Full-Service Restaurants"  # padded

    def test_invalid_json_returns_fallback(self):
        result = _parse_response("not json {{{{", 2)
        assert len(result) == 2
        assert all(r["confidence"] == "Low" for r in result)


# ══════════════════════════════════════════════════════════════════════════════
# 5. TypeClassifier.classify_batch
# ══════════════════════════════════════════════════════════════════════════════

class TestTypeClassifierBatch:
    def test_single_batch_correct_types(self, sem):
        client  = make_openai_mock(["Cafes", "Bakery", "Quick-Service Restaurants"])
        clf     = TypeClassifier(client, sem)
        items   = [
            {"name": "Cafe X",   "current_type": "Café / Coffee Shop",  "classification": "Independent", "reasoning": "coffee"},
            {"name": "Bake Y",   "current_type": "Bakery & Café",        "classification": "Local Chain",  "reasoning": "bakery"},
            {"name": "Fast Z",   "current_type": "Fast-Casual Restaurant","classification": "MNC Chain",   "reasoning": "qsr"},
        ]
        results = run_async(clf.classify_batch(items))
        assert len(results) == 3
        assert results[0]["type"] == "Cafes"
        assert results[1]["type"] == "Bakery"
        assert results[2]["type"] == "Quick-Service Restaurants"
        assert clf.calls_made == 1
        assert clf.tokens_used == 400

    def test_all_results_have_valid_types(self, sem):
        types   = list(VALID_TYPES)[:3]
        client  = make_openai_mock(types)
        clf     = TypeClassifier(client, sem)
        items   = [{"name": f"R{i}", "current_type": "Unknown", "classification": "Independent", "reasoning": ""} for i in range(3)]
        results = run_async(clf.classify_batch(items))
        for r in results:
            assert r["type"] in VALID_TYPES

    def test_token_counter_increments(self, sem):
        client = make_openai_mock(["Cafes"])
        clf    = TypeClassifier(client, sem)
        items  = [{"name": "X", "current_type": "Café", "classification": "Independent", "reasoning": ""}]
        run_async(clf.classify_batch(items))
        assert clf.tokens_used == 400   # 300 prompt + 100 completion


# ══════════════════════════════════════════════════════════════════════════════
# 6. TypeClassifier.classify_all (concurrency + autosave)
# ══════════════════════════════════════════════════════════════════════════════

class TestTypeClassifierAll:
    def _make_records(self, n: int) -> list[dict]:
        return [
            {
                "matched_uae_name"      : f"Restaurant {i}",
                "UAE_Type of Restaurants": "Casual Dining Restaurant",
                "classification"        : "Independent",
                "reasoning"             : f"reason {i}",
            }
            for i in range(n)
        ]

    def test_classify_all_returns_correct_count(self, sem, tmp_path):
        n       = 12
        types   = ["Full-Service Restaurants"] * n
        # 12 restaurants = 2 batches of 10+2
        client  = make_openai_mock(types[:10])   # first batch
        client2 = make_openai_mock(types[10:])   # second batch
        # Alternate return values
        async def create_side_effect(**kwargs):
            # Parse how many items are in this batch from the prompt
            prompt = kwargs["messages"][1]["content"]
            import re as _re
            m = _re.search(r"(\d+) objects", prompt)
            n_items = int(m.group(1)) if m else 5
            payload = [{"type": "Full-Service Restaurants", "confidence": "High", "reasoning": ""}
                       for _ in range(n_items)]
            r = MagicMock()
            r.choices[0].message.content = json.dumps(payload)
            r.usage.prompt_tokens = 300; r.usage.completion_tokens = 100
            return r
        client.chat.completions.create = create_side_effect

        clf     = TypeClassifier(client, sem)
        records = self._make_records(n)

        async def _run():
            async with AutoSaveManager(tmp_path / "test.json", flush_every=5) as saver:
                return await clf.classify_all(records, saver)

        results = run_async(_run())
        assert len(results) == n
        assert all(r["type"] in VALID_TYPES for r in results)

    def test_autosave_skips_already_processed(self, sem, tmp_path):
        """Pre-populated autosave should skip those restaurants."""
        records = self._make_records(5)
        # Pre-save 3 out of 5
        existing = {
            records[0]["matched_uae_name"]: {"type": "Cafes", "confidence": "High", "reasoning": "pre-saved"},
            records[1]["matched_uae_name"]: {"type": "Bakery", "confidence": "High", "reasoning": "pre-saved"},
            records[2]["matched_uae_name"]: {"type": "Cloud-Kitchen", "confidence": "High", "reasoning": "pre-saved"},
        }
        save_path = tmp_path / "test.json"
        import json as _json
        save_path.write_text(_json.dumps(existing), encoding="utf-8")

        client = make_openai_mock(["Quick-Service Restaurants", "Full-Service Restaurants"])
        clf    = TypeClassifier(client, sem)

        async def _run():
            async with AutoSaveManager(save_path, flush_every=5) as saver:
                return await clf.classify_all(records, saver)

        results = run_async(_run())
        assert len(results) == 5
        assert results[0]["type"] == "Cafes"          # from pre-save
        assert results[1]["type"] == "Bakery"          # from pre-save
        assert results[2]["type"] == "Cloud-Kitchen"   # from pre-save
        # Only 1 OpenAI call (batch of 2 for indices 3 & 4)
        assert clf.calls_made == 1


# ══════════════════════════════════════════════════════════════════════════════
# 7. utils/retry.py
# ══════════════════════════════════════════════════════════════════════════════

class TestRetryAsync:
    def test_success_on_first_try(self):
        async def ok_fn():
            return "success"
        result = run_async(retry_async(ok_fn))
        assert result == "success"

    def test_retries_on_rate_limit_then_succeeds(self):
        from openai import RateLimitError
        call_count = [0]

        async def flaky_fn():
            call_count[0] += 1
            if call_count[0] < 3:
                raise RateLimitError("rate limited", response=MagicMock(), body={})
            return "ok"

        result = run_async(retry_async(flaky_fn, base_delay=0.01, cap_delay=0.1))
        assert result == "ok"
        assert call_count[0] == 3

    def test_raises_after_max_retries(self):
        from openai import RateLimitError

        async def always_fail():
            raise RateLimitError("always", response=MagicMock(), body={})

        with pytest.raises(RateLimitError):
            run_async(retry_async(always_fail, max_retries=2, base_delay=0.01, cap_delay=0.05))

    def test_non_retryable_raises_immediately(self):
        call_count = [0]

        async def value_error_fn():
            call_count[0] += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            run_async(retry_async(value_error_fn, max_retries=5))
        assert call_count[0] == 1   # only called once


# ══════════════════════════════════════════════════════════════════════════════
# 8. utils/autosave.py
# ══════════════════════════════════════════════════════════════════════════════

class TestAutoSaveManager:
    def test_add_and_retrieve(self, tmp_path):
        path = tmp_path / "save.json"
        async def _test():
            async with AutoSaveManager(path, flush_every=100) as saver:
                await saver.add("r1", {"type": "Cafes"})
                await saver.add("r2", {"type": "Bakery"})
                assert saver.get("r1") == {"type": "Cafes"}
                assert saver.contains("r2")
                assert saver.count() == 2
        run_async(_test())

    def test_flush_writes_to_disk(self, tmp_path):
        path = tmp_path / "save.json"
        async def _test():
            async with AutoSaveManager(path, flush_every=2) as saver:
                await saver.add("r1", {"type": "Cafes"})
                await saver.add("r2", {"type": "Bakery"})  # triggers flush
        run_async(_test())
        import json as _json
        data = _json.loads(path.read_text())
        assert "r1" in data and "r2" in data

    def test_resume_loads_existing(self, tmp_path):
        import json as _json
        path = tmp_path / "save.json"
        path.write_text(_json.dumps({"existing_key": {"type": "Cloud-Kitchen"}}))

        async def _test():
            async with AutoSaveManager(path, flush_every=100) as saver:
                assert saver.contains("existing_key")
                assert saver.get("existing_key")["type"] == "Cloud-Kitchen"
                assert saver.count() == 1
        run_async(_test())

    def test_corrupt_file_returns_empty(self, tmp_path):
        path = tmp_path / "corrupt.json"
        path.write_text("{{{{not valid json")
        data = AutoSaveManager.load(path)
        assert data == {}

    def test_autosave_always_flushes_on_exit(self, tmp_path):
        import json as _json
        path = tmp_path / "exit_test.json"
        async def _test():
            async with AutoSaveManager(path, flush_every=999) as saver:
                await saver.add("key", {"type": "Bakery"})
                # flush_every=999 won't trigger on add, but __aexit__ must flush
        run_async(_test())
        data = _json.loads(path.read_text())
        assert "key" in data


# ══════════════════════════════════════════════════════════════════════════════
# 9. Prompt builder
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildTypePrompt:
    def test_prompt_contains_all_5_categories(self):
        items = [{"name": "X", "current_type": "Cafe", "classification": "Independent", "reasoning": "test"}]
        prompt = _build_type_prompt(items)
        for cat in VALID_TYPES:
            assert cat in prompt, f"Category '{cat}' missing from prompt"

    def test_prompt_contains_restaurant_name(self):
        items = [{"name": "Le Pain Quotidien", "current_type": "Bakery & Café",
                  "classification": "MNC Chain", "reasoning": "French chain"}]
        prompt = _build_type_prompt(items)
        assert "Le Pain Quotidien" in prompt

    def test_prompt_count_matches_batch_size(self):
        items = [{"name": f"R{i}", "current_type": "Cafe", "classification": "Independent", "reasoning": ""}
                 for i in range(7)]
        prompt = _build_type_prompt(items)
        assert "7 objects" in prompt
