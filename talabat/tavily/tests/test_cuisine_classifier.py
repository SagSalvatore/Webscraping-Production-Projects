"""
Tests for cuisine_classifier.py + cuisine_config.py
All mocked — zero real API calls.
Run: pytest tests/test_cuisine_classifier.py -v
"""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from cuisine_config import (
    CUISINE_TYPES, SUB_CUISINE_TYPES, needs_tavily,
    COL_CUISINE_TYPE, COL_SUB_CUISINE, COL_CUISINE_CONF,
)
from cuisine_classifier import (
    CuisineClassifier, _normalise_cuisine_type,
    _parse_cuisine_response, _build_cuisine_prompt,
)


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def make_clients(cuisine_list: list[str], sub_list: list[str]):
    """Build mock OpenAI + Tavily async clients."""
    payload = [
        {
            "restaurant_index": i + 1,
            "name": f"R{i}",
            "cuisine_type": cuisine_list[i] if i < len(cuisine_list) else "Multi-Cuisine",
            "sub_cuisine_types": sub_list[i] if i < len(sub_list) else "International",
            "confidence": "High",
            "reasoning": "test",
        }
        for i in range(max(len(cuisine_list), len(sub_list)))
    ]
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = json.dumps(payload)
    mock_resp.usage.prompt_tokens   = 400
    mock_resp.usage.completion_tokens = 100

    openai_client = MagicMock()
    openai_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    tavily_client = MagicMock()
    tavily_client.search = AsyncMock(return_value={
        "answer": "popular Indian restaurant in Dubai",
        "results": [{"content": "great biryani and curries"}],
    })
    return openai_client, tavily_client


# ══════════════════════════════════════════════════════════════════════════════
# 1. cuisine_config — taxonomy
# ══════════════════════════════════════════════════════════════════════════════
class TestCuisineConfig:
    def test_cuisine_types_count(self):
        assert len(CUISINE_TYPES) == 9

    @pytest.mark.parametrize("t", [
        "Asian", "American", "Café", "Multi-Cuisine",
        "Middle Eastern and North African", "Bakery",
        "Mediterranean", "European", "Beverages",
    ])
    def test_all_cuisine_types_present(self, t):
        assert t in CUISINE_TYPES

    def test_no_food_ingredients_in_cuisine_types(self):
        """Lemon juice and Butter should have been removed from taxonomy."""
        for ct in CUISINE_TYPES:
            assert "lemon" not in ct.lower()
            assert "butter" not in ct.lower()

    def test_sub_cuisine_types_not_empty(self):
        assert len(SUB_CUISINE_TYPES) >= 30

    def test_sub_cuisine_has_arabic(self):
        assert any("Arabic" in s for s in SUB_CUISINE_TYPES)

    def test_sub_cuisine_has_indian(self):
        assert any("Indian" in s for s in SUB_CUISINE_TYPES)


# ══════════════════════════════════════════════════════════════════════════════
# 2. cuisine_config — routing
# ══════════════════════════════════════════════════════════════════════════════
class TestNeedsTavily:
    @pytest.mark.parametrize("key_cuis,expected", [
        (None,                          True),   # missing
        ("",                            True),   # empty
        ("  ",                          True),   # whitespace
        ("ican·Pizza",                  True),   # garbled (starts lowercase)
        ("·Coffee·Breakfast",           True),   # garbled (starts with bullet)
        ("Fried Chicken·Burgers",       False),  # bullet in middle but starts OK
        ("Indian, Pakistani, Arabic",   False),  # clean
        ("Bakery, European, Western",   False),  # clean
        ("Asian, Indian",               False),  # clean
        ("Biryani, Grills, Indian",     False),  # clean
        ("Arabic, Kuwaiti, Grills",     False),  # clean
    ])
    def test_routing(self, key_cuis, expected):
        assert needs_tavily(key_cuis) == expected


# ══════════════════════════════════════════════════════════════════════════════
# 3. _normalise_cuisine_type
# ══════════════════════════════════════════════════════════════════════════════
class TestNormaliseCuisineType:
    @pytest.mark.parametrize("raw,expected", [
        ("Asian",                          "Asian"),
        ("asian food",                     "Asian"),
        ("American",                       "American"),
        ("Café",                           "Café"),
        ("cafe",                           "Café"),
        ("Coffee Shop",                    "Café"),
        ("Multi-Cuisine",                  "Multi-Cuisine"),
        ("Middle Eastern and North African","Middle Eastern and North African"),
        ("Middle Eastern",                 "Middle Eastern and North African"),
        ("Arabic",                         "Middle Eastern and North African"),
        ("Bakery",                         "Bakery"),
        ("Mediterranean",                  "Mediterranean"),
        ("European",                       "European"),
        ("Western",                        "European"),
        ("Beverages",                      "Beverages"),
        ("Unknown XYZ",                    "Multi-Cuisine"),
        ("",                               "Multi-Cuisine"),
    ])
    def test_normalise(self, raw, expected):
        assert _normalise_cuisine_type(raw) == expected


# ══════════════════════════════════════════════════════════════════════════════
# 4. _parse_cuisine_response
# ══════════════════════════════════════════════════════════════════════════════
class TestParseCuisineResponse:
    def test_valid_array(self):
        raw = json.dumps([
            {"cuisine_type": "Asian", "sub_cuisine_types": "Indian",
             "confidence": "High", "reasoning": "Indian restaurant"},
        ])
        result = _parse_cuisine_response(raw, 1)
        assert len(result) == 1
        assert result[0][COL_CUISINE_TYPE] == "Asian"
        assert result[0][COL_SUB_CUISINE]  == "Indian"

    def test_wrapped_in_dict(self):
        raw = json.dumps({"results": [
            {"cuisine_type": "Bakery", "sub_cuisine_types": "Bakeries",
             "confidence": "High", "reasoning": "bakery"}
        ]})
        result = _parse_cuisine_response(raw, 1)
        assert result[0][COL_CUISINE_TYPE] == "Bakery"

    def test_normalises_cuisine_type(self):
        raw = json.dumps([
            {"cuisine_type": "middle eastern", "sub_cuisine_types": "Arabic",
             "confidence": "High", "reasoning": "arab food"},
        ])
        result = _parse_cuisine_response(raw, 1)
        assert result[0][COL_CUISINE_TYPE] == "Middle Eastern and North African"

    def test_pads_missing_items(self):
        raw = json.dumps([
            {"cuisine_type": "Asian", "sub_cuisine_types": "Chinese",
             "confidence": "High", "reasoning": ""},
        ])
        result = _parse_cuisine_response(raw, 3)
        assert len(result) == 3
        assert result[0][COL_CUISINE_TYPE] == "Asian"
        assert result[1][COL_CUISINE_TYPE] == "Multi-Cuisine"  # padded fallback

    def test_invalid_json_returns_fallback(self):
        result = _parse_cuisine_response("not json {{{{", 2)
        assert len(result) == 2
        assert all(r[COL_CUISINE_CONF] == "Low" for r in result)

    def test_all_results_have_required_keys(self):
        raw = json.dumps([
            {"cuisine_type": "American", "sub_cuisine_types": "North American",
             "confidence": "High", "reasoning": "burger place"},
        ])
        result = _parse_cuisine_response(raw, 1)
        for key in [COL_CUISINE_TYPE, COL_SUB_CUISINE, COL_CUISINE_CONF]:
            assert key in result[0]


# ══════════════════════════════════════════════════════════════════════════════
# 5. _build_cuisine_prompt
# ══════════════════════════════════════════════════════════════════════════════
class TestBuildCuisinePrompt:
    def test_contains_all_cuisine_types(self):
        batch = [{"name": "X", "key_cuisines": "Indian",
                  "type_of_restaurant": "Casual", "context_text": ""}]
        prompt = _build_cuisine_prompt(batch)
        for ct in CUISINE_TYPES:
            assert ct in prompt, f"Missing cuisine type: {ct}"

    def test_contains_restaurant_name(self):
        batch = [{"name": "Biryani Palace", "key_cuisines": "Indian, Biryani",
                  "type_of_restaurant": "Casual Dining", "context_text": ""}]
        prompt = _build_cuisine_prompt(batch)
        assert "Biryani Palace" in prompt

    def test_key_cuisines_in_prompt(self):
        batch = [{"name": "R", "key_cuisines": "Arabic, Lebanese, Grills",
                  "type_of_restaurant": "QSR", "context_text": ""}]
        prompt = _build_cuisine_prompt(batch)
        assert "Arabic, Lebanese, Grills" in prompt

    def test_batch_count_in_prompt(self):
        batch = [
            {"name": f"R{i}", "key_cuisines": "Indian",
             "type_of_restaurant": "FSR", "context_text": ""}
            for i in range(7)
        ]
        prompt = _build_cuisine_prompt(batch)
        assert "7 objects" in prompt

    def test_not_available_shown_when_no_key_cuisines(self):
        batch = [{"name": "X", "key_cuisines": "",
                  "type_of_restaurant": "Cafe", "context_text": "found online"}]
        prompt = _build_cuisine_prompt(batch)
        assert "Not available" in prompt


# ══════════════════════════════════════════════════════════════════════════════
# 6. CuisineClassifier — path routing
# ══════════════════════════════════════════════════════════════════════════════
class TestCuisineClassifierRouting:
    @pytest.fixture
    def clf(self):
        oai, tv = make_clients(["Asian", "Bakery"], ["Indian", "Bakeries"])
        c = CuisineClassifier(oai, tv)
        c._sem_oai = asyncio.Semaphore(5)
        c._sem_tv  = asyncio.Semaphore(5)
        return c, oai, tv

    def test_path_a_no_tavily(self, clf):
        """Clean KEY CUISINES → Tavily must NOT be called."""
        c, oai, tv = clf
        records = [{
            "matched_uae_name" : "Biryani Palace",
            "UAE_KEY CUISINES" : "Indian, Biryani, Curry",
            "UAE_Type of Restaurants": "Full-Service Restaurants",
            "classification"   : "Independent",
        }]

        async def _run():
            from utils.autosave import AutoSaveManager
            import tempfile, pathlib
            with tempfile.TemporaryDirectory() as td:
                async with AutoSaveManager(pathlib.Path(td)/"s.json", flush_every=100) as saver:
                    return await c.classify_all(records, saver)

        run_async(_run())
        tv.search.assert_not_called()
        assert oai.chat.completions.create.call_count == 1

    def test_path_b_calls_tavily(self, clf):
        """Missing KEY CUISINES → Tavily MUST be called."""
        c, oai, tv = clf
        records = [{
            "matched_uae_name" : "Unknown Cafe",
            "UAE_KEY CUISINES" : None,
            "UAE_Type of Restaurants": "Cafes",
            "classification"   : "Independent",
        }]

        async def _run():
            from utils.autosave import AutoSaveManager
            import tempfile, pathlib
            with tempfile.TemporaryDirectory() as td:
                async with AutoSaveManager(pathlib.Path(td)/"s.json", flush_every=100) as saver:
                    return await c.classify_all(records, saver)

        run_async(_run())
        tv.search.assert_called_once()

    def test_garbled_key_cuisines_calls_tavily(self, clf):
        """Garbled KEY CUISINES (starts lowercase) → Tavily called."""
        c, oai, tv = clf
        records = [{
            "matched_uae_name" : "Amer Pizza",
            "UAE_KEY CUISINES" : "ican·Pizza",   # truncated/garbled
            "UAE_Type of Restaurants": "QSR",
            "classification"   : "Local Chain",
        }]

        async def _run():
            from utils.autosave import AutoSaveManager
            import tempfile, pathlib
            with tempfile.TemporaryDirectory() as td:
                async with AutoSaveManager(pathlib.Path(td)/"s.json", flush_every=100) as saver:
                    return await c.classify_all(records, saver)

        run_async(_run())
        tv.search.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# 7. CuisineClassifier — output structure
# ══════════════════════════════════════════════════════════════════════════════
class TestCuisineClassifierOutput:
    REQUIRED_KEYS = {COL_CUISINE_TYPE, COL_SUB_CUISINE, COL_CUISINE_CONF}

    def _run_classify(self, records):
        oai, tv = make_clients(
            [r.get("_expected_ct", "Asian") for r in records],
            [r.get("_expected_sc", "Indian") for r in records],
        )
        # Dynamic response based on batch size
        async def create_side_effect(**kwargs):
            prompt = kwargs["messages"][1]["content"]
            import re
            m = re.search(r"(\d+) objects", prompt)
            n = int(m.group(1)) if m else len(records)
            payload = [{"cuisine_type": "Asian", "sub_cuisine_types": "Indian",
                         "confidence": "High", "reasoning": "test"}] * n
            r = MagicMock()
            r.choices[0].message.content = json.dumps(payload)
            r.usage.prompt_tokens = 300; r.usage.completion_tokens = 100
            return r
        oai.chat.completions.create = create_side_effect

        clf = CuisineClassifier(oai, tv)
        clf._sem_oai = asyncio.Semaphore(5)
        clf._sem_tv  = asyncio.Semaphore(5)

        async def _run():
            from utils.autosave import AutoSaveManager
            import tempfile, pathlib
            with tempfile.TemporaryDirectory() as td:
                async with AutoSaveManager(pathlib.Path(td)/"s.json", flush_every=100) as saver:
                    return await clf.classify_all(records, saver)

        return run_async(_run())

    def test_result_count_matches_input(self):
        records = [
            {"matched_uae_name": f"R{i}", "UAE_KEY CUISINES": "Indian",
             "UAE_Type of Restaurants": "FSR", "classification": "Independent"}
            for i in range(5)
        ]
        results = self._run_classify(records)
        assert len(results) == 5

    def test_all_results_have_required_keys(self):
        records = [{"matched_uae_name": "X", "UAE_KEY CUISINES": "Arabic",
                    "UAE_Type of Restaurants": "FSR", "classification": "Independent"}]
        results = self._run_classify(records)
        assert self.REQUIRED_KEYS.issubset(set(results[0].keys()))

    def test_cuisine_type_is_valid(self):
        records = [{"matched_uae_name": "X", "UAE_KEY CUISINES": "Indian, Biryani",
                    "UAE_Type of Restaurants": "FSR", "classification": "Independent"}]
        results = self._run_classify(records)
        assert results[0][COL_CUISINE_TYPE] in CUISINE_TYPES
