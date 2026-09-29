"""
conftest.py — shared pytest fixtures for all test modules.
"""

import json
import pytest


# ── Mock API responses ────────────────────────────────────────────────────────

@pytest.fixture
def mock_found_response():
    """Realistic Places API response for a found restaurant."""
    return {
        "places": [
            {
                "id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
                "displayName": {"text": "McDonald's", "languageCode": "en"},
                "formattedAddress": (
                    "Ground Floor, Mall of Emirates, "
                    "Sheikh Zayed Road, Dubai, United Arab Emirates"
                ),
                "location": {"latitude": 25.1185, "longitude": 55.2004},
                "nationalPhoneNumber": "04 341 0000",
                "internationalPhoneNumber": "+971 4 341 0000",
                "websiteUri": "https://www.mcdonalds.com/ae/en-ae.html",
                "googleMapsUri": "https://maps.google.com/?cid=12345678",
            }
        ]
    }


@pytest.fixture
def mock_found_response_no_phone():
    """Response where phone and website are absent (some small restaurants)."""
    return {
        "places": [
            {
                "id": "ChIJabc123",
                "displayName": {"text": "Nyla", "languageCode": "en"},
                "formattedAddress": "JVC, Dubai, United Arab Emirates",
                "location": {"latitude": 25.0547, "longitude": 55.2011},
                "googleMapsUri": "https://maps.google.com/?cid=99999",
            }
        ]
    }


@pytest.fixture
def mock_not_found_response():
    """Response when the API returns zero places."""
    return {"places": []}


@pytest.fixture
def mock_empty_response():
    """Response with no 'places' key at all."""
    return {}


@pytest.fixture
def sample_slugs():
    """A small list of slugs covering common scenarios."""
    return [
        (1,  "mcdonalds"),           # global chain
        (2,  "shake_shack"),         # popular in Dubai malls
        (3,  "nyla"),                # UAE-local restaurant
        (4,  "starbucks"),           # global chain, multiple locations
        (5,  "totally_xyz_fake_99"), # intentional not-found
    ]


@pytest.fixture
def sample_csv_path(tmp_path):
    """Write sample_5.csv into a temp dir and return its path."""
    csv = tmp_path / "sample_5.csv"
    csv.write_text(
        "1,mcdonalds\n"
        "2,shake_shack\n"
        "3,nyla\n"
        "4,starbucks\n"
        "5,totally_xyz_fake_99\n",
        encoding="utf-8",
    )
    return csv


# ── Markers ───────────────────────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: mark test as requiring a live Google API key",
    )
