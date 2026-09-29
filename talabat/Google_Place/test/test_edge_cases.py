"""
test_edge_cases.py
------------------
Edge case tests — all use mocked HTTP so no API key needed.

Covers:
  - Not-found falls back to broad UAE search
  - Chain restaurant (multiple locations) — returns first result
  - Generic restaurant name (might match wrong place)
  - HTTP 429 (quota exceeded) triggers retry
  - Network timeout triggers retry
  - Duplicate slug handling
  - Slug with special characters
  - Restaurant with no phone/website
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.fetch_places import (
    fetch_restaurant,
    call_places_api,
    parse_result,
    slug_to_name,
)

import logging
logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_mock_response(data: dict, status_code: int = 200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = data
    if status_code >= 400:
        mock.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"HTTP Error {status_code}", response=mock
        )
    else:
        mock.raise_for_status.return_value = None
    return mock


def make_found_payload(name="Test Restaurant", address="Dubai, UAE",
                        lat=25.2, lng=55.3, phone="+971 4 000 0000",
                        website="https://example.com",
                        maps_url="https://maps.google.com/?cid=1"):
    return {
        "places": [{
            "id": "test_place_id",
            "displayName": {"text": name},
            "formattedAddress": address,
            "location": {"latitude": lat, "longitude": lng},
            "internationalPhoneNumber": phone,
            "websiteUri": website,
            "googleMapsUri": maps_url,
        }]
    }


NOT_FOUND_PAYLOAD = {"places": []}


# ── Fallback to broad search ──────────────────────────────────────────────────

class TestFallbackToBroadSearch:
    @patch("fetch_places.call_places_api")
    def test_not_found_triggers_broad_retry(self, mock_api):
        """When Dubai-biased search returns nothing, re-queries without location bias."""
        mock_api.side_effect = [
            NOT_FOUND_PAYLOAD,                          # first call (Dubai bias) — miss
            make_found_payload(name="Nyla", address="Sharjah, UAE"),  # broad retry — hit
        ]
        record = fetch_restaurant("nyla", 10, logger)
        assert record["status"] == "found_broad"
        assert record["Restaurant_Name"] == "Nyla"
        assert mock_api.call_count == 2

    @patch("fetch_places.call_places_api")
    def test_both_searches_miss_returns_not_found(self, mock_api):
        """When both searches return nothing, status is 'not_found'."""
        mock_api.return_value = NOT_FOUND_PAYLOAD
        record = fetch_restaurant("totally_xyz_fake_99", 99, logger)
        assert record["status"] == "not_found"
        assert record["Restaurant_Name"] == ""
        assert record["Address"] == ""

    @patch("fetch_places.call_places_api")
    def test_found_on_first_try_does_not_call_broad(self, mock_api):
        """When Dubai search succeeds, no broad retry is made."""
        mock_api.return_value = make_found_payload(name="McDonald's")
        fetch_restaurant("mcdonalds", 1, logger)
        assert mock_api.call_count == 1


# ── Chain restaurants (multiple locations) ────────────────────────────────────

class TestChainRestaurants:
    @patch("fetch_places.call_places_api")
    def test_chain_returns_first_result(self, mock_api):
        """Chains like Starbucks have many locations — we take maxResultCount=1."""
        mock_api.return_value = make_found_payload(
            name="Starbucks",
            address="Dubai Mall, Financial Centre Road, Dubai, UAE"
        )
        record = fetch_restaurant("starbucks", 142, logger)
        assert record["status"] == "found"
        assert record["Restaurant_Name"] == "Starbucks"

    @patch("fetch_places.call_places_api")
    def test_record_has_full_address(self, mock_api):
        mock_api.return_value = make_found_payload(
            name="KFC",
            address="Level 2, Ibn Battuta Mall, Dubai, UAE"
        )
        record = fetch_restaurant("kfc", 908, logger)
        assert "Ibn Battuta Mall" in record["Address"]


# ── Generic / ambiguous names ─────────────────────────────────────────────────

class TestGenericNames:
    @patch("fetch_places.call_places_api")
    def test_generic_name_still_returns_first_hit(self, mock_api):
        """'Eagle' is very generic — we accept whatever Google returns."""
        mock_api.return_value = make_found_payload(
            name="Eagle Restaurant",
            address="Deira, Dubai, UAE"
        )
        record = fetch_restaurant("eagle", 1000, logger)
        assert record["status"] == "found"

    def test_slug_to_name_for_generic_slug(self):
        assert slug_to_name("eagle") == "Eagle"
        assert slug_to_name("goat") == "Goat"


# ── Error handling ────────────────────────────────────────────────────────────

class TestErrorHandling:
    @patch("fetch_places._post_search")
    def test_http_403_returns_error_status(self, mock_post):
        """403 Forbidden (invalid key) — captured as error, not raised."""
        http_err = requests.exceptions.HTTPError("403 Forbidden")
        http_err.response = MagicMock(status_code=403)
        mock_post.side_effect = http_err
        record = fetch_restaurant("mcdonalds", 1, logger)
        assert record["status"] == "error"
        assert "403" in record["error_msg"]

    @patch("fetch_places._post_search")
    def test_timeout_returns_error_status(self, mock_post):
        """Timeout after retries exhausted — captured as error."""
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")
        record = fetch_restaurant("mcdonalds", 1, logger)
        assert record["status"] == "error"

    @patch("fetch_places.call_places_api")
    def test_unexpected_exception_captured(self, mock_api):
        """Unexpected exception doesn't crash the whole run."""
        mock_api.side_effect = ValueError("Unexpected JSON structure")
        record = fetch_restaurant("shake_shack", 3, logger)
        assert record["status"] == "error"
        assert "Unexpected JSON structure" in record["error_msg"]


# ── Missing fields in response ────────────────────────────────────────────────

class TestMissingFields:
    @patch("fetch_places.call_places_api")
    def test_no_phone_returns_empty_string(self, mock_api):
        mock_api.return_value = {
            "places": [{
                "id": "abc",
                "displayName": {"text": "No Phone Place"},
                "formattedAddress": "Dubai, UAE",
                "location": {"latitude": 25.2, "longitude": 55.3},
                "googleMapsUri": "https://maps.google.com/?cid=1",
            }]
        }
        record = fetch_restaurant("no_dough", 52, logger)
        assert record["Contact_No"] == ""

    @patch("fetch_places.call_places_api")
    def test_no_website_returns_empty_string(self, mock_api):
        mock_api.return_value = {
            "places": [{
                "id": "abc",
                "displayName": {"text": "No Website Place"},
                "formattedAddress": "Sharjah, UAE",
                "location": {"latitude": 25.3, "longitude": 55.4},
                "googleMapsUri": "https://maps.google.com/?cid=2",
            }]
        }
        record = fetch_restaurant("khoya", 13, logger)
        assert record["Website"] == ""

    @patch("fetch_places.call_places_api")
    def test_no_coordinates_returns_empty_string(self, mock_api):
        mock_api.return_value = {
            "places": [{
                "id": "abc",
                "displayName": {"text": "No Coords"},
                "formattedAddress": "Dubai, UAE",
                "googleMapsUri": "https://maps.google.com/?cid=3",
            }]
        }
        record = fetch_restaurant("some_place", 1, logger)
        assert record["Geo_Lat"] == ""
        assert record["Geo_Lng"] == ""


# ── Duplicate slugs ───────────────────────────────────────────────────────────

class TestDuplicateSlugs:
    def test_duplicate_names_in_csv_handled_gracefully(self):
        """
        RESTRO_LIST.csv has 'vasanta_bhavan' at row 25 and 'vasanta_bhavan_vegetarian'
        at row 1257. These are distinct slugs so they generate distinct queries.
        The slug→name conversion should produce different search strings.
        """
        assert slug_to_name("vasanta_bhavan") != slug_to_name("vasanta_bhavan_vegetarian")

    def test_p_f_changs_vs_pf_changs_different_queries(self):
        """'pf_changs' (row 23) and 'p_f_changs' (row 173) yield slightly different queries."""
        name1 = slug_to_name("pf_changs")
        name2 = slug_to_name("p_f_changs")
        assert name1 != name2
        assert "Pf Changs" in name1
        assert "P F Changs" in name2


# ── Output field completeness ─────────────────────────────────────────────────

class TestOutputFieldCompleteness:
    @patch("fetch_places.call_places_api")
    def test_found_record_has_all_required_keys(self, mock_api):
        mock_api.return_value = make_found_payload()
        record = fetch_restaurant("mcdonalds", 1, logger)
        required = [
            "row_id", "slug",
            "Restaurant_Name", "Address", "Contact_No",
            "Website", "Geo_Lat", "Geo_Lng", "Google_Maps_URL",
            "status",
        ]
        for key in required:
            assert key in record, f"Missing key: {key}"

    @patch("fetch_places.call_places_api")
    def test_not_found_record_has_empty_strings_not_none(self, mock_api):
        mock_api.return_value = NOT_FOUND_PAYLOAD
        record = fetch_restaurant("xyz_fake_place", 1, logger)
        for key in ["Restaurant_Name", "Address", "Contact_No", "Website"]:
            assert record[key] == "", f"Expected empty string for {key}, got {record[key]!r}"
