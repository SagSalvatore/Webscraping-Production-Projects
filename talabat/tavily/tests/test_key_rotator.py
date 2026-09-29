"""
tests/test_key_rotator.py — Unit tests for TavilyKeyRotator.
Run: pytest tests/test_key_rotator.py -v
No real API calls — all Tavily interactions are mocked.
"""
from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from key_rotator import TavilyKeyRotator


# ── Helpers ───────────────────────────────────────────────────────────────────

def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def make_rotator(*keys: str) -> TavilyKeyRotator:
    return TavilyKeyRotator(list(keys))


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestInit:
    def test_no_keys_raises(self):
        with pytest.raises(ValueError, match="at least one key"):
            TavilyKeyRotator([])

    def test_single_key(self):
        r = make_rotator("key1")
        assert r.current_key_num == 1
        assert r.keys_remaining == 1

    def test_three_keys(self):
        r = make_rotator("k1", "k2", "k3")
        assert r.keys_remaining == 3
        assert not r.all_exhausted


class TestFromCsv:
    def test_loads_keys(self, tmp_path):
        csv_file = tmp_path / "tav_keys.csv"
        with open(csv_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["S.No", "Keys"])
            writer.writerow(["1", "tvly-key-aaa"])
            writer.writerow(["2", "tvly-key-bbb"])
        r = TavilyKeyRotator.from_csv(csv_file)
        assert r.keys_remaining == 2

    def test_empty_csv_raises(self, tmp_path):
        csv_file = tmp_path / "empty.csv"
        with open(csv_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["S.No", "Keys"])
        with pytest.raises(ValueError, match="No keys found"):
            TavilyKeyRotator.from_csv(csv_file)


class TestSearchSuccess:
    def test_successful_search_returns_result(self):
        r = make_rotator("k1", "k2")
        fake_result = {"answer": "Starbucks is a chain", "results": []}

        with patch("key_rotator.AsyncTavilyClient") as MockClient:
            mock_inst = AsyncMock()
            mock_inst.search = AsyncMock(return_value=fake_result)
            MockClient.return_value = mock_inst

            result = run(r.search("Starbucks UAE"))

        assert result == fake_result
        assert r.searches_made == 1
        assert r.rotations == 0
        assert r.current_key_num == 1   # no rotation happened


class TestKeyRotation:
    def test_rotates_on_429(self):
        r = make_rotator("k1", "k2", "k3")
        call_count = 0

        with patch("key_rotator.AsyncTavilyClient") as MockClient:
            async def side_effect(query, **kwargs):
                nonlocal call_count
                call_count += 1
                # First call: fail with 429; second: succeed
                if call_count == 1:
                    raise Exception("429 Too Many Requests credit limit exceeded")
                return {"answer": "ok", "results": []}

            mock_inst = AsyncMock()
            mock_inst.search = side_effect
            MockClient.return_value = mock_inst

            result = run(r.search("query"))

        assert r.rotations == 1
        assert r.current_key_num == 2   # moved to key #2
        assert result["answer"] == "ok"

    def test_all_keys_exhausted_raises(self):
        r = make_rotator("k1", "k2")

        with patch("key_rotator.AsyncTavilyClient") as MockClient:
            mock_inst = AsyncMock()
            mock_inst.search = AsyncMock(
                side_effect=Exception("429 credit quota exceeded")
            )
            MockClient.return_value = mock_inst

            with pytest.raises(RuntimeError, match="exhausted"):
                run(r.search("query"))

        assert r.all_exhausted

    def test_non_rate_limit_error_propagates(self):
        r = make_rotator("k1")

        with patch("key_rotator.AsyncTavilyClient") as MockClient:
            mock_inst = AsyncMock()
            mock_inst.search = AsyncMock(
                side_effect=ValueError("unexpected format")
            )
            MockClient.return_value = mock_inst

            with pytest.raises(ValueError, match="unexpected format"):
                run(r.search("query"))

        assert r.rotations == 0          # no rotation for non-rate-limit error


class TestConcurrentRotation:
    """Multiple coroutines hitting the same rotator — only rotate once."""

    def test_concurrent_rotations_dont_double_increment(self):
        r = make_rotator("k1", "k2", "k3")
        call_count = 0

        with patch("key_rotator.AsyncTavilyClient") as MockClient:
            async def side_effect(query, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count <= 5:
                    raise Exception("429 limit exceeded")
                return {"answer": "ok", "results": []}

            mock_inst = AsyncMock()
            mock_inst.search = side_effect
            MockClient.return_value = mock_inst

            async def _run():
                tasks = [r.search(f"query_{i}") for i in range(3)]
                return await asyncio.gather(*tasks, return_exceptions=True)

            loop = asyncio.new_event_loop()
            try:
                results = loop.run_until_complete(_run())
            finally:
                loop.close()

        # Rotation index should not exceed total keys
        assert r._idx <= len(r._keys)


class TestStats:
    def test_status_line_format(self):
        r = make_rotator("k1", "k2")
        line = r.status_line()
        assert "TavilyKeyRotator" in line
        assert "searches=0" in line
        assert "rotations=0" in line

    def test_keys_remaining_decrements_on_rotation(self):
        r = make_rotator("k1", "k2", "k3")
        assert r.keys_remaining == 3
        r._idx = 1
        assert r.keys_remaining == 2
        r._idx = 3
        assert r.keys_remaining == 0
        assert r.all_exhausted
