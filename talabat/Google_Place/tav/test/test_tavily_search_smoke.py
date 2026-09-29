"""
Smoke test: ONE real Tavily search against a well-known real UAE business,
to confirm the key rotator + query builder actually work end to end before
spending budget on the Not-Found list. Uses only 1 of the 18 keys.
"""
import pytest

from tavily_search import TavilySearcher, build_query


def test_build_query_format():
    q = build_query("nando's", "Dubai Marina")
    assert q == "Nando's UAE Restaurant"
    assert "Dubai Marina" not in q  # area deliberately excluded -- see validation.py


@pytest.mark.smoke
async def test_real_search_returns_results():
    searcher = TavilySearcher()
    payload = await searcher.search("Nando's", "Dubai Marina")
    assert payload["results"], "expected at least one search result for a well-known real business"
    assert payload["query"]
    print(f"\n[smoke] query={payload['query']!r}")
    print(f"[smoke] answer={payload.get('answer')!r}")
    print(f"[smoke] {len(payload['results'])} results, first title={payload['results'][0]['title']!r}")
