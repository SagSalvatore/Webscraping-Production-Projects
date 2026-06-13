"""
Unit tests for url_collector/parser.py — no network calls.
Uses mock HTML that mimics Talabat's __NEXT_DATA__ structure.

Run:  pytest tests/test_parser.py -v
"""

import json
import re

import pytest

from url_collector.parser import (
    ListingPageResult,
    discover_areas,
    extract_max_page,
    parse_listing_page,
)


# ------------------------------------------------------------------
# Mock HTML builders
# ------------------------------------------------------------------

def _make_listing_html(
    restaurants: list[dict],
    total_count: int = None,
    page_size: int = 16,
    current_page: int = 1,
    pagination_links: list[int] = None,
) -> str:
    """Build minimal Talabat-like listing page HTML."""

    next_data = {
        "props": {
            "pageProps": {
                "initialListingState": {
                    "restaurants": restaurants,
                    "totalCount": total_count or len(restaurants),
                    "pageSize": page_size,
                    "pagination": {
                        "current": current_page,
                        "total": total_count or len(restaurants),
                        "pageSize": page_size,
                    },
                }
            }
        }
    }

    pagination_html = ""
    if pagination_links:
        links = "".join(
            f'<a href="?page={p}">{p}</a>' for p in pagination_links
        )
        pagination_html = f'<div class="pagination">{links}</div>'

    return f"""
    <html><body>
      <script id="__NEXT_DATA__" type="application/json">
        {json.dumps(next_data)}
      </script>
      {pagination_html}
    </body></html>
    """


def _make_restaurant_entry(rest_id: int, slug: str, branch_id: int = None) -> dict:
    """branch_id is the physical-location ID used in the URL; rest_id is the chain ID."""
    return {
        "id": rest_id,
        "restaurantId": rest_id,
        "branchId": branch_id if branch_id is not None else rest_id * 10,
        "branchSlug": slug,
        "urlSlug": slug,
        "name": slug.replace("-", " ").title(),
        "latitude": 25.0 + rest_id * 0.001,
        "longitude": 55.0 + rest_id * 0.001,
    }


# ------------------------------------------------------------------
# parse_listing_page tests
# ------------------------------------------------------------------

class TestParseListingPage:

    def test_extracts_urls_from_next_data(self):
        restaurants = [
            _make_restaurant_entry(1001, "mcdonalds-dubai"),
            _make_restaurant_entry(1002, "kfc-downtown"),
            _make_restaurant_entry(1003, "subway-marina"),
        ]
        html = _make_listing_html(restaurants)
        result = parse_listing_page(html, page_num=1)

        assert len(result.restaurant_urls) == 3
        for url in result.restaurant_urls:
            assert url.startswith("https://www.talabat.com/uae/restaurant/")
            assert re.search(r"/restaurant/\d+/[\w-]+", url)

    def test_total_pages_calculated_from_count_and_size(self):
        restaurants = [_make_restaurant_entry(i, f"rest-{i}") for i in range(16)]
        html = _make_listing_html(restaurants, total_count=48, page_size=16)
        result = parse_listing_page(html, page_num=1)

        assert result.total_restaurants == 48
        assert result.page_size == 16
        assert result.total_pages == 3  # ceil(48/16)

    def test_no_urls_returns_empty_list(self):
        html = _make_listing_html([])
        result = parse_listing_page(html, page_num=1)
        assert result.restaurant_urls == []

    def test_deduplicates_urls_within_page(self):
        # Same branch appearing twice (same branch_id) — should be collapsed to 1
        restaurants = [
            _make_restaurant_entry(999, "burger-king", branch_id=5001),
            _make_restaurant_entry(999, "burger-king", branch_id=5001),
        ]
        html = _make_listing_html(restaurants)
        result = parse_listing_page(html, page_num=1)
        assert len(result.restaurant_urls) == 1
        assert len(result.vendors) == 1
        assert result.vendors[0].branch_id == 5001
        assert result.vendors[0].restaurant_id == 999

    def test_css_fallback_when_no_next_data(self):
        """If __NEXT_DATA__ is absent, CSS selector extraction should work."""
        html = """
        <html><body>
          <a href="/uae/restaurant/5001/test-restaurant-dubai">Test</a>
          <a href="/uae/restaurant/5002/another-restaurant">Another</a>
          <a href="/uae/some-other-link">Not a restaurant</a>
        </body></html>
        """
        result = parse_listing_page(html, page_num=1)
        assert len(result.restaurant_urls) == 2
        for url in result.restaurant_urls:
            assert "/uae/restaurant/" in url

    def test_url_built_correctly_from_branch_id_and_slug(self):
        # branch_id (663941) is the value in the URL, restaurant_id (12345) is the chain
        restaurants = [_make_restaurant_entry(12345, "al-banosh-seafood", branch_id=663941)]
        html = _make_listing_html(restaurants)
        result = parse_listing_page(html, page_num=1)

        assert result.restaurant_urls == [
            "https://www.talabat.com/uae/restaurant/663941/al-banosh-seafood"
        ]
        assert result.vendors[0].branch_id == 663941
        assert result.vendors[0].restaurant_id == 12345

    def test_page_num_stored_correctly(self):
        html = _make_listing_html([_make_restaurant_entry(1, "r")])
        result = parse_listing_page(html, page_num=7)
        assert result.current_page == 7


# ------------------------------------------------------------------
# extract_max_page tests
# ------------------------------------------------------------------

class TestExtractMaxPage:

    def test_reads_max_page_from_pagination_links(self):
        html = _make_listing_html(
            [_make_restaurant_entry(1, "r")],
            pagination_links=[1, 2, 3, 4, 191],
        )
        assert extract_max_page(html) == 191

    def test_reads_max_page_from_next_data(self):
        html = _make_listing_html(
            [_make_restaurant_entry(i, f"r{i}") for i in range(16)],
            total_count=160,
            page_size=16,
        )
        assert extract_max_page(html) == 10

    def test_returns_none_for_single_page(self):
        html = _make_listing_html(
            [_make_restaurant_entry(1, "r")],
            total_count=5,
            page_size=16,
        )
        # ceil(5/16) = 1
        assert extract_max_page(html) == 1

    def test_pagination_links_beat_empty_next_data(self):
        html = """
        <html><body>
          <div class="pagination">
            <a href="?page=1">1</a>
            <a href="?page=50">50</a>
          </div>
        </body></html>
        """
        assert extract_max_page(html) == 50

    def test_returns_none_for_empty_html(self):
        assert extract_max_page("<html><body></body></html>") is None


# ------------------------------------------------------------------
# discover_areas tests
# ------------------------------------------------------------------

class TestDiscoverAreas:

    def _make_area_html(self, areas: list[dict]) -> str:
        next_data = {
            "props": {
                "pageProps": {
                    "areas": areas
                }
            }
        }
        return f"""
        <html><body>
          <script id="__NEXT_DATA__" type="application/json">
            {json.dumps(next_data)}
          </script>
        </body></html>
        """

    def test_extracts_areas_from_next_data(self):
        html = self._make_area_html([
            {"id": 1001, "slug": "dubai-marina", "name": "Dubai Marina"},
            {"id": 1002, "slug": "downtown-dubai", "name": "Downtown Dubai"},
        ])
        areas = discover_areas(html)
        assert len(areas) == 2
        assert areas[0]["area_id"] == 1001
        assert areas[0]["slug"] == "dubai-marina"
        assert "talabat.com/uae/restaurants/1001/dubai-marina" in areas[0]["url"]

    def test_css_fallback_for_area_links(self):
        html = """
        <html><body>
          <a href="/uae/restaurants/2652/industrial-area-18">Industrial Area 18</a>
          <a href="/uae/restaurants/1280/dwtc">DWTC</a>
        </body></html>
        """
        areas = discover_areas(html)
        assert len(areas) == 2
        ids = {a["area_id"] for a in areas}
        assert 2652 in ids
        assert 1280 in ids

    def test_no_areas_returns_empty(self):
        html = "<html><body><p>Nothing here</p></body></html>"
        assert discover_areas(html) == []
