"""
Live fetch tests for Talabat UAE listing pages.
These hit the real website — mark them @pytest.mark.integration.

Run:  pytest tests/test_listing_fetch.py -v -m integration
"""

import asyncio
import re

import pytest

from url_collector.parser import extract_max_page, parse_listing_page
from tests.conftest import TEST_AREA_ID, TEST_AREA_NAME, TEST_AREA_URL


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ------------------------------------------------------------------
# HTML fetch tests
# ------------------------------------------------------------------

@pytest.mark.integration
def test_fetch_listing_page_1_returns_html(fetcher):
    """Page 1 of a known area should return non-empty HTML."""
    html = _run(fetcher.fetch(f"{TEST_AREA_URL}?page=1"))
    assert html is not None, "Fetch returned None — possible block or network issue"
    assert len(html) > 5000, f"HTML too short ({len(html)} chars) — may be blocked"
    assert "talabat" in html.lower()
    print(f"\nHTML length: {len(html):,} chars")


@pytest.mark.integration
def test_fetch_returns_200_not_403(fetcher):
    """Must not get a Cloudflare/bot block (403)."""
    import curl_cffi.requests as cf_req
    # We check indirectly: a blocked page has very short HTML and/or "Access denied"
    html = _run(fetcher.fetch(f"{TEST_AREA_URL}?page=1"))
    assert html is not None
    assert "access denied" not in html.lower(), "Got Cloudflare Access Denied block"
    assert "cf-challenge" not in html.lower(), "Got Cloudflare JS challenge"


# ------------------------------------------------------------------
# URL extraction tests
# ------------------------------------------------------------------

@pytest.mark.integration
def test_extract_restaurant_urls_from_page_1(fetcher):
    """Page 1 should yield at least 10 restaurant URLs."""
    html = _run(fetcher.fetch(f"{TEST_AREA_URL}?page=1"))
    assert html is not None

    result = parse_listing_page(html, page_num=1)
    urls = result.restaurant_urls

    assert len(urls) >= 10, (
        f"Expected ≥10 restaurant URLs on page 1, got {len(urls)}. "
        "Possible parse failure or bot block."
    )
    print(f"\nExtracted {len(urls)} URLs from page 1")
    print("Sample URLs:")
    for u in urls[:3]:
        print(f"  {u}")


@pytest.mark.integration
def test_restaurant_urls_have_correct_format(fetcher):
    """All extracted URLs must match the expected Talabat restaurant pattern."""
    html = _run(fetcher.fetch(f"{TEST_AREA_URL}?page=1"))
    assert html is not None

    result = parse_listing_page(html, page_num=1)
    pattern = re.compile(r"https://www\.talabat\.com/uae/restaurant/\d+/[\w-]+")

    for url in result.restaurant_urls:
        assert pattern.match(url), f"Bad URL format: {url}"


@pytest.mark.integration
def test_extract_total_pages(fetcher):
    """Should be able to determine how many pages this area has."""
    html = _run(fetcher.fetch(f"{TEST_AREA_URL}?page=1"))
    assert html is not None

    result = parse_listing_page(html, page_num=1)
    max_pg = extract_max_page(html)

    total = result.total_pages or max_pg
    print(f"\nTotal pages for '{TEST_AREA_NAME}': {total}")

    # We know from the screenshot this area has pages (at least 2+)
    assert total is not None, (
        "Could not determine total pages — __NEXT_DATA__ and pagination parsing both failed"
    )
    assert total >= 1


@pytest.mark.integration
def test_fetch_page_2_returns_different_urls(fetcher):
    """Page 2 should return a different set of restaurant URLs than page 1."""
    html1 = _run(fetcher.fetch(f"{TEST_AREA_URL}?page=1"))
    html2 = _run(fetcher.fetch(f"{TEST_AREA_URL}?page=2"))

    assert html1 and html2

    urls1 = set(parse_listing_page(html1, 1).restaurant_urls)
    urls2 = set(parse_listing_page(html2, 2).restaurant_urls)

    overlap = urls1 & urls2
    print(f"\nPage 1: {len(urls1)} URLs, Page 2: {len(urls2)} URLs, Overlap: {len(overlap)}")

    assert len(urls2) > 0, "Page 2 returned no URLs"
    # Minor overlap is OK (some restaurants appear on multiple pages), but they shouldn't be identical
    assert urls1 != urls2, "Page 1 and page 2 returned exactly the same URLs"


# ------------------------------------------------------------------
# CSV area loading test
# ------------------------------------------------------------------

def test_city_urls_csv_is_readable():
    """CITY-URLS.csv should load with 646 rows of valid URLs."""
    import csv
    from pathlib import Path
    csv_path = Path(__file__).resolve().parents[1] / "CITY-URLS.csv"

    assert csv_path.exists(), f"CITY-URLS.csv not found at {csv_path}"

    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                rows.append(row)

    assert len(rows) >= 600, f"Expected 600+ rows, got {len(rows)}"
    print(f"\nCSV rows loaded: {len(rows)}")

    for name, url in rows[:5]:
        assert url.startswith("https://www.talabat.com/uae/restaurants/"), (
            f"Unexpected URL format: {url}"
        )
        m = re.search(r"/restaurants/(\d+)/", url)
        assert m, f"No area_id in URL: {url}"
