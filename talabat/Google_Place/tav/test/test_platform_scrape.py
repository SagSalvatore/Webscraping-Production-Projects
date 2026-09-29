"""
Tests for platform_scrape.py -- Zomato/Deliveroo/noon Food JSON-LD scraping.
"""
import httpx
import pytest

from platform_scrape import find_platform_urls, _flatten_address, _extract_restaurant_ldjson, fetch_platform_listing, _looks_like_uae, is_uae_zomato_url


def test_find_platform_urls_detects_zomato():
    payload = {
        "results": [
            {"url": "https://www.talabat.com/uae/restaurant/123/foo"},
            {"url": "https://www.zomato.com/dubai/foo-bar/menu"},
            {"url": "https://www.zomato.com/dubai/foo-bar/reviews"},  # dup platform, should collapse to 1
        ]
    }
    found = find_platform_urls(payload)
    assert len(found) == 1
    assert found[0]["platform"] == "zomato"
    assert "menu" in found[0]["url"]  # first occurrence kept


def test_find_platform_urls_empty_when_no_match():
    payload = {"results": [{"url": "https://www.talabat.com/uae/restaurant/123/foo"}]}
    assert find_platform_urls(payload) == []


def test_is_uae_zomato_url_accepts_all_emirates():
    assert is_uae_zomato_url("https://www.zomato.com/dubai/some-place")
    assert is_uae_zomato_url("https://www.zomato.com/abudhabi/some-place")
    assert is_uae_zomato_url("https://www.zomato.com/sharjah/some-place")
    assert is_uae_zomato_url("https://www.zomato.com/ajman/some-place")
    assert is_uae_zomato_url("https://www.zomato.com/fujairah/some-place")
    assert is_uae_zomato_url("https://www.zomato.com/ras-al-khaimah/some-place")
    assert is_uae_zomato_url("https://www.zomato.com/umm-al-quwain/some-place")
    assert is_uae_zomato_url("https://www.zomato.com/al-ain/some-place")


def test_is_uae_zomato_url_rejects_other_countries():
    assert not is_uae_zomato_url("https://www.zomato.com/bangalore/some-place")
    assert not is_uae_zomato_url("https://www.zomato.com/mumbai/some-place")
    assert not is_uae_zomato_url("https://www.zomato.com/london/some-place")


def test_find_platform_urls_filters_non_uae_zomato():
    payload = {
        "results": [
            {"url": "https://www.zomato.com/bangalore/dessert-haven"},
            {"url": "https://www.deliveroo.ae/menu/dubai/some-place"},
        ]
    }
    found = find_platform_urls(payload)
    platforms = {f["platform"] for f in found}
    assert "zomato" not in platforms  # non-UAE zomato filtered out
    assert "deliveroo" in platforms  # deliveroo collected as-is, no prefix check


def test_flatten_address_dedupes_and_joins():
    addr = {
        "streetAddress": "Al Mamzar Building, Al Waheida Street, Hor Al Anz, Dubai",
        "addressLocality": "Hor Al Anz, Dubai",
        "addressRegion": "Dubai",
        "addressCountry": "UAE",
    }
    flat = _flatten_address(addr)
    assert "Al Mamzar Building" in flat
    assert flat.count("Dubai") >= 1


def test_looks_like_uae_rejects_india_listing():
    """Regression test: a real production run matched a same-named Zomato
    listing in Bangalore, India to a Talabat UAE restaurant purely on name
    overlap. Country/geo must be checked too."""
    india_listing = {
        "address": {"addressCountry": "India"},
        "geo": {"latitude": "12.841262", "longitude": "77.649828"},
    }
    assert not _looks_like_uae(india_listing)


def test_looks_like_uae_accepts_real_uae_listing():
    uae_listing = {
        "address": {"addressCountry": "UAE"},
        "geo": {"latitude": "25.2839596687", "longitude": "55.3519012034"},
    }
    assert _looks_like_uae(uae_listing)


def test_looks_like_uae_falls_back_to_geo_when_country_missing():
    listing = {"address": {}, "geo": {"latitude": "25.05", "longitude": "55.17"}}
    assert _looks_like_uae(listing)
    listing_outside = {"address": {}, "geo": {"latitude": "51.5", "longitude": "-0.12"}}  # London
    assert not _looks_like_uae(listing_outside)


def test_extract_restaurant_ldjson_from_html_snippet():
    html = '''
    <script type="application/ld+json">{"@context":"http://schema.org","@type":"WebSite","name":"Zomato"}</script>
    <script data-rh="true" type="application/ld+json">{"@context":"https://schema.org","@type":"Restaurant","name":"Test Place","telephone":"+971500000001, +97140000002"}</script>
    '''
    ld = _extract_restaurant_ldjson(html)
    assert ld is not None
    assert ld["name"] == "Test Place"


@pytest.mark.smoke
async def test_real_zomato_ldjson_fetch():
    """The exact listing the user pasted -- confirms raw httpx (no JS) still
    gets the JSON-LD address/phone/geo/rating server-rendered."""
    url = "https://www.zomato.com/dubai/asmak-al-aumdah-seafood-restaurant-hor-al-anz"
    async with httpx.AsyncClient() as client:
        listing = await fetch_platform_listing(url, client)
    assert listing is not None
    print(f"\n[smoke] {listing}")
    assert "asmak" in listing["name"].lower() or "aumdah" in listing["name"].lower()
    assert listing["address"]
    assert listing["phone"]
    assert listing["latitude"]
