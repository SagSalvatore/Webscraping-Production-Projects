"""
Smoke test: real OpenAI extraction calls against canned search payloads
(no live Tavily call needed here -- isolates the extraction logic).
"""
import pytest

from openai_extract import OpenAIExtractor


@pytest.mark.smoke
async def test_extracts_clear_match():
    extractor = OpenAIExtractor()
    payload = {
        "query": '"Test Falafel House" restaurant Al Barsha UAE address phone contact',
        "answer": "Test Falafel House is a restaurant located in Al Barsha, Dubai, UAE.",
        "results": [
            {
                "title": "Test Falafel House - Al Barsha, Dubai",
                "url": "https://www.zomato.com/dubai/test-falafel-house-al-barsha",
                "content": (
                    "Test Falafel House, Shop 4, Al Barsha 1, Dubai, UAE. "
                    "Phone: +971 4 123 4567. Website: testfalafelhouse.ae. "
                    "Find us on Google Maps: https://maps.google.com/?cid=12345"
                ),
            }
        ],
    }
    result = await extractor.extract("test falafel house", "Al Barsha", payload)
    print(f"\n[smoke] {result}")
    assert result.found is True
    assert result.address
    assert result.phone
    assert extractor.calls_made == 1
    assert extractor.estimated_cost_usd > 0


@pytest.mark.smoke
async def test_no_match_returns_found_false():
    extractor = OpenAIExtractor()
    payload = {
        "query": '"Xyzabc Nonexistent Kitchen 12345" restaurant UAE address phone contact',
        "answer": None,
        "results": [],
    }
    result = await extractor.extract("xyzabc nonexistent kitchen 12345", None, payload)
    print(f"\n[smoke] {result}")
    assert result.found is False
