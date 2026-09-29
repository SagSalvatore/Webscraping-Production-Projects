"""
test_parser.py
--------------
Unit tests for slug_to_name() and parse_result().
No real API calls — all responses are fixtures.
"""

import sys
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.fetch_places import slug_to_name, parse_result


# ── slug_to_name ──────────────────────────────────────────────────────────────

class TestSlugToName:
    def test_basic_conversion(self):
        assert slug_to_name("the_lost_restaurant") == "The Lost Restaurant"

    def test_single_word(self):
        assert slug_to_name("mcdonalds") == "Mcdonalds"

    def test_with_numbers(self):
        assert slug_to_name("1762_deli") == "1762 Deli"

    def test_with_ampersand_style(self):
        assert slug_to_name("pf_changs") == "Pf Changs"

    def test_strips_whitespace(self):
        assert slug_to_name("  starbucks  ") == "Starbucks"

    def test_long_slug(self):
        result = slug_to_name("training_day_healthy_salads_warm_bowls")
        assert result == "Training Day Healthy Salads Warm Bowls"

    def test_already_clean(self):
        assert slug_to_name("nyla") == "Nyla"

    def test_numbers_only_segment(self):
        assert slug_to_name("800_pizza") == "800 Pizza"


# ── parse_result ──────────────────────────────────────────────────────────────

class TestParseResult:
    def test_full_response_extracts_all_fields(self, mock_found_response):
        result = parse_result(mock_found_response)
        assert result is not None
        assert result["Restaurant_Name"] == "McDonald's"
        assert "Mall of Emirates" in result["Address"]
        assert result["Contact_No"] == "+971 4 341 0000"
        assert result["Website"] == "https://www.mcdonalds.com/ae/en-ae.html"
        assert result["Geo_Lat"] == 25.1185
        assert result["Geo_Lng"] == 55.2004
        assert "maps.google.com" in result["Google_Maps_URL"]

    def test_not_found_response_returns_none(self, mock_not_found_response):
        assert parse_result(mock_not_found_response) is None

    def test_empty_response_returns_none(self, mock_empty_response):
        assert parse_result(mock_empty_response) is None

    def test_missing_phone_falls_back_to_empty_string(self, mock_found_response_no_phone):
        result = parse_result(mock_found_response_no_phone)
        assert result is not None
        assert result["Contact_No"] == ""

    def test_missing_website_returns_empty_string(self, mock_found_response_no_phone):
        result = parse_result(mock_found_response_no_phone)
        assert result["Website"] == ""

    def test_prefers_international_phone_over_national(self):
        raw = {
            "places": [{
                "id": "abc",
                "displayName": {"text": "Test"},
                "formattedAddress": "Dubai, UAE",
                "location": {"latitude": 25.2, "longitude": 55.3},
                "nationalPhoneNumber": "04 123 4567",
                "internationalPhoneNumber": "+971 4 123 4567",
                "googleMapsUri": "https://maps.google.com/?cid=1",
            }]
        }
        result = parse_result(raw)
        assert result["Contact_No"] == "+971 4 123 4567"

    def test_national_phone_used_when_international_absent(self):
        raw = {
            "places": [{
                "id": "abc",
                "displayName": {"text": "Test"},
                "formattedAddress": "Dubai, UAE",
                "location": {"latitude": 25.2, "longitude": 55.3},
                "nationalPhoneNumber": "04 123 4567",
                "googleMapsUri": "https://maps.google.com/?cid=1",
            }]
        }
        result = parse_result(raw)
        assert result["Contact_No"] == "04 123 4567"

    def test_place_id_captured(self, mock_found_response):
        result = parse_result(mock_found_response)
        assert result["place_id"] == "ChIJN1t_tDeuEmsRUsoyG83frY4"

    def test_coordinates_are_numeric(self, mock_found_response):
        result = parse_result(mock_found_response)
        assert isinstance(result["Geo_Lat"], float)
        assert isinstance(result["Geo_Lng"], float)

    def test_only_first_place_used(self):
        raw = {
            "places": [
                {
                    "id": "first",
                    "displayName": {"text": "First Restaurant"},
                    "formattedAddress": "Dubai, UAE",
                    "location": {"latitude": 25.1, "longitude": 55.1},
                    "googleMapsUri": "https://maps.google.com/?cid=1",
                },
                {
                    "id": "second",
                    "displayName": {"text": "Second Restaurant"},
                    "formattedAddress": "Sharjah, UAE",
                    "location": {"latitude": 25.3, "longitude": 55.4},
                    "googleMapsUri": "https://maps.google.com/?cid=2",
                },
            ]
        }
        result = parse_result(raw)
        assert result["Restaurant_Name"] == "First Restaurant"
        assert result["place_id"] == "first"
