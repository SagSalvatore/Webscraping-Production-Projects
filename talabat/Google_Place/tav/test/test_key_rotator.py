"""
Unit tests for tav_keys/key_rotator.py -- no real network calls.
Verifies round-robin distribution and blacklist-on-exhaustion behavior.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tav_keys.key_rotator import TavilyKeyRotator


class _FakeTavilyClient:
    """Stands in for AsyncTavilyClient. Keys starting with 'bad' always 429."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def search(self, query: str, timeout: int = 25, **kwargs):
        if self.api_key.startswith("bad"):
            raise Exception("429 Too Many Requests: rate limit exceeded")
        return {"results": [{"title": f"hit for {query} via {self.api_key}"}]}


@pytest.fixture(autouse=True)
def patch_tavily_client(monkeypatch):
    monkeypatch.setattr(
        "tav_keys.key_rotator.AsyncTavilyClient", _FakeTavilyClient
    )


async def test_round_robin_cycles_through_all_keys():
    rotator = TavilyKeyRotator(["key1", "key2", "key3"])
    seen_keys = set()
    for _ in range(6):
        result = await rotator.search("test query")
        seen_keys.add(result["results"][0]["title"].split("via ")[1])
    assert seen_keys == {"key1", "key2", "key3"}
    assert rotator.searches_made == 6


async def test_blacklists_exhausted_key_and_continues():
    rotator = TavilyKeyRotator(["bad1", "good1", "good2"])
    result = await rotator.search("test query")
    assert "good" in result["results"][0]["title"]
    assert rotator.keys_remaining == 2
    assert 0 in rotator._blacklisted  # bad1 was index 0


async def test_all_keys_exhausted_raises():
    rotator = TavilyKeyRotator(["bad1", "bad2"])
    with pytest.raises(RuntimeError):
        await rotator.search("test query")
    assert rotator.all_exhausted


def test_from_csv_loads_keys(tmp_path):
    csv_content = "S.No,Name,Keys\n1,Alice,tvly-abc\n2,Bob,tvly-def\n3,Empty,\n"
    csv_path = tmp_path / "keys.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    rotator = TavilyKeyRotator.from_csv(csv_path)
    assert len(rotator._keys) == 2
    assert rotator._keys == ["tvly-abc", "tvly-def"]
