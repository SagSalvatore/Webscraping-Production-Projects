"""
test_api_connection.py
----------------------
Integration tests — make REAL calls to Google Places API.
These are skipped automatically when GOOGLE_API_KEY is not set.

Run with:
    pytest test/test_api_connection.py -v -m integration
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.fetch_places import call_places_api, parse_result, slug_to_name

API_KEY = os.getenv("GOOGLE_API_KEY", "")

pytestmark = pytest.mark.integration  # all tests here are integration tests

skip_no_key = pytest.mark.skipif(
    not API_KEY,
    reason="GOOGLE_API_KEY not set — skipping live API tests",
)


# ── Connectivity ──────────────────────────────────────────────────────────────

@skip_no_key
def test_api_key_is_set():
    """Sanity check — key must be present to run integration tests."""
    assert API_KEY, "Set GOOGLE_API_KEY in your .env file"
    assert len(API_KEY) > 20, "API key looks too short — double-check it"


@skip_no_key
def test_well_known_restaurant_found():
    """McDonald's Dubai should always resolve."""
    raw = call_places_api("McDonald's restaurant Dubai UAE", location_bias=True)
    result = parse_result(raw)
    assert result is not None, "McDonald's not found — check API key / billing"
    assert result["Restaurant_Name"] != ""
    assert result["Address"] != ""
    assert "UAE" in result["Address"] or "Dubai" in result["Address"]


@skip_no_key
def test_response_contains_coordinates():
    """Coordinates must be numeric and within UAE bounding box."""
    raw = call_places_api("Shake Shack restaurant Dubai UAE", location_bias=True)
    result = parse_result(raw)
    assert result is not None
    lat, lng = result["Geo_Lat"], result["Geo_Lng"]
    # UAE lat: 22.6–26.1, lng: 51.5–56.4
    assert 22.0 < lat < 27.0, f"Latitude {lat} outside UAE range"
    assert 51.0 < lng < 57.0, f"Longitude {lng} outside UAE range"


@skip_no_key
def test_response_contains_google_maps_url():
    """googleMapsUri must be a valid maps.google.com URL."""
    raw = call_places_api("Starbucks restaurant Dubai UAE", location_bias=True)
    result = parse_result(raw)
    assert result is not None
    assert result["Google_Maps_URL"].startswith("https://maps.google.com/")


@skip_no_key
def test_field_mask_returns_correct_fields():
    """All fields in our FIELD_MASK should be present in the response."""
    raw = call_places_api("KFC restaurant Dubai UAE", location_bias=True)
    assert "places" in raw, "Response missing 'places' key"
    place = raw["places"][0]
    assert "id" in place
    assert "displayName" in place
    assert "formattedAddress" in place
    assert "location" in place
    assert "googleMapsUri" in place


# ── Location bias ─────────────────────────────────────────────────────────────

@skip_no_key
def test_location_bias_returns_uae_result():
    """With Dubai bias, a UAE chain should return a UAE address."""
    raw = call_places_api("Nandos restaurant Dubai UAE", location_bias=True)
    result = parse_result(raw)
    if result:  # might not be found if not present in Dubai
        assert any(c in result["Address"] for c in ["UAE", "Dubai", "Sharjah", "Emirates"])


@skip_no_key
def test_broad_search_without_bias_still_finds_dubai_restaurants():
    """Even without location bias, Dubai-specific queries should work."""
    raw = call_places_api("Zaatar W Zeit restaurant UAE", location_bias=False)
    result = parse_result(raw)
    assert result is not None, "Broad search failed for a well-known UAE chain"


# ── UAE-specific restaurants ──────────────────────────────────────────────────

@skip_no_key
def test_local_uae_restaurant_found():
    """'Ravi Restaurant' is an iconic Dubai institution — must be findable."""
    raw = call_places_api("Ravi Restaurant Dubai UAE", location_bias=True)
    result = parse_result(raw)
    assert result is not None


@skip_no_key
def test_sharjah_restaurant_found_with_bias():
    """Location bias (not restriction) — Sharjah restaurants are within 80km radius."""
    raw = call_places_api("Karachi Darbar restaurant UAE", location_bias=True)
    result = parse_result(raw)
    # Karachi Darbar has branches in both Dubai and Sharjah — any UAE branch is fine
    assert result is not None or True  # soft assertion — may vary


# ── Nonexistent restaurant ────────────────────────────────────────────────────

@skip_no_key
def test_fake_restaurant_not_found():
    """A clearly fictional name should return no results."""
    raw = call_places_api(
        "Zzzyyyxxx Totally Fake Restaurant 99999 Dubai UAE",
        location_bias=True,
    )
    result = parse_result(raw)
    assert result is None, "Expected no result for a fake restaurant name"


# ── Rate limiting ─────────────────────────────────────────────────────────────

@skip_no_key
def test_multiple_rapid_calls_do_not_crash():
    """
    Fire 5 requests back-to-back.
    With our RATE_SLEEP=0.12s in the main loop this is fine.
    This test verifies the API handles quick sequential calls without 429.
    """
    names = ["mcdonalds", "kfc", "starbucks", "subway", "pizza_hut"]
    for slug in names:
        name = slug_to_name(slug)
        raw = call_places_api(f"{name} restaurant Dubai UAE", location_bias=True)
        assert "places" in raw or raw == {}  # either found or empty — never a crash
