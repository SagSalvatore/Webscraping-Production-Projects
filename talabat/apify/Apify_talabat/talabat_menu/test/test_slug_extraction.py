"""
test_slug_extraction.py
-----------------------
Unit tests for slug and country extraction from Talabat URLs.
"""

import sys
from pathlib import Path

import pytest

# Add parent directory to sys.path so we can import the module
sys.path.insert(0, str(Path(__file__).parent.parent))
from run_talabat_menu import extract_slug, extract_country, load_urls


class TestExtractSlug:
    """Test slug extraction from various Talabat URL formats."""

    def test_standard_url_with_aid(self):
        url = "https://www.talabat.com/uae/restaurant/611383/texas-de-brazil-downtown-burj-khalifa?aid=1176"
        assert extract_slug(url) == "texas-de-brazil-downtown-burj-khalifa"

    def test_standard_url_different_aid(self):
        url = "https://www.talabat.com/uae/restaurant/636774/saigon-taste-of-vietnam-jlt?aid=1176"
        assert extract_slug(url) == "saigon-taste-of-vietnam-jlt"

    def test_url_without_query_params(self):
        url = "https://www.talabat.com/uae/restaurant/611383/texas-de-brazil-downtown-burj-khalifa"
        assert extract_slug(url) == "texas-de-brazil-downtown-burj-khalifa"

    def test_url_with_trailing_slash(self):
        url = "https://www.talabat.com/uae/restaurant/611383/texas-de-brazil-downtown-burj-khalifa/"
        assert extract_slug(url) == "texas-de-brazil-downtown-burj-khalifa"

    def test_url_with_whitespace(self):
        url = "  https://www.talabat.com/uae/restaurant/611383/texas-de-brazil-downtown-burj-khalifa?aid=1176  "
        assert extract_slug(url) == "texas-de-brazil-downtown-burj-khalifa"

    def test_url_with_carriage_return(self):
        url = "https://www.talabat.com/uae/restaurant/611383/texas-de-brazil-downtown-burj-khalifa?aid=1176\r"
        assert extract_slug(url) == "texas-de-brazil-downtown-burj-khalifa"

    def test_url_saudi_arabia(self):
        url = "https://www.talabat.com/sa/restaurant/12345/some-restaurant-name?aid=1000"
        assert extract_slug(url) == "some-restaurant-name"

    def test_url_kuwait(self):
        url = "https://www.talabat.com/kw/restaurant/99999/al-baik-salmiya?aid=2000"
        assert extract_slug(url) == "al-baik-salmiya"

    def test_slug_with_numbers(self):
        url = "https://www.talabat.com/uae/restaurant/803572/e7-sushi-lounge-al-hosn?aid=1443"
        assert extract_slug(url) == "e7-sushi-lounge-al-hosn"


class TestExtractCountry:
    """Test country code extraction from Talabat URLs."""

    def test_uae(self):
        url = "https://www.talabat.com/uae/restaurant/611383/texas-de-brazil?aid=1176"
        assert extract_country(url) == "uae"

    def test_saudi_arabia(self):
        url = "https://www.talabat.com/sa/restaurant/12345/some-restaurant"
        assert extract_country(url) == "sa"

    def test_kuwait(self):
        url = "https://www.talabat.com/kw/restaurant/99999/al-baik"
        assert extract_country(url) == "kw"

    def test_jordan(self):
        url = "https://www.talabat.com/jo/restaurant/12345/blue-fig"
        assert extract_country(url) == "jo"

    def test_egypt(self):
        url = "https://www.talabat.com/eg/restaurant/12345/koshary-abou-tarek"
        assert extract_country(url) == "eg"


class TestLoadUrls:
    """Test loading URLs from CSV."""

    def test_load_real_csv(self):
        csv_path = Path(__file__).parent.parent / "urls.csv"
        if csv_path.exists():
            urls = load_urls(csv_path)
            assert len(urls) > 0, "Should load at least 1 URL"
            assert len(urls) >= 900, f"Expected ~1000 URLs, got {len(urls)}"
            # All should be valid talabat URLs
            for url in urls:
                assert "talabat.com" in url, f"Invalid URL: {url}"

    def test_all_slugs_are_valid(self):
        csv_path = Path(__file__).parent.parent / "urls.csv"
        if csv_path.exists():
            urls = load_urls(csv_path)
            for url in urls:
                slug = extract_slug(url)
                assert len(slug) > 0, f"Empty slug from: {url}"
                assert "/" not in slug, f"Slug contains slash: {slug} from {url}"
                assert "?" not in slug, f"Slug contains query: {slug} from {url}"

    def test_all_countries_are_valid(self):
        csv_path = Path(__file__).parent.parent / "urls.csv"
        valid_countries = {"uae", "sa", "kw", "bh", "om", "qa", "jo", "eg", "iq"}
        if csv_path.exists():
            urls = load_urls(csv_path)
            for url in urls:
                country = extract_country(url)
                assert country in valid_countries, f"Invalid country '{country}' from {url}"
