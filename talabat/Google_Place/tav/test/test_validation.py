"""
Unit tests for validation.py guardrails -- pure functions, no network calls.
"""
import pandas as pd

from validation import (
    is_placeholder_phone,
    name_supported_by_results,
    flag_shared_contact_duplicates,
    maps_url_supported_by_results,
    build_maps_search_url,
    has_independent_confirmation,
)


def test_placeholder_phone_detects_sequential_digits():
    assert is_placeholder_phone("+971123456789")
    assert is_placeholder_phone("987654321")


def test_placeholder_phone_detects_repeated_digits():
    assert is_placeholder_phone("+971111111111")
    assert is_placeholder_phone("0000000000")


def test_placeholder_phone_accepts_real_looking_number():
    assert not is_placeholder_phone("+971 4 343 5878")
    assert not is_placeholder_phone("+971502261234")


def test_placeholder_phone_handles_none_and_short():
    assert not is_placeholder_phone(None)
    assert is_placeholder_phone("12345")  # too short to be a real number


def test_name_supported_requires_token_overlap():
    payload = {
        "results": [
            {"title": "Sato Restaurant Dubai", "content": "Sato Restaurant serves Japanese food in Al Barsha."}
        ]
    }
    assert name_supported_by_results("sato restaurant", payload)


def test_name_not_supported_when_results_are_unrelated():
    payload = {
        "results": [
            {"title": "Zayed Sports City Stadium", "content": "Contact us at the stadium switchboard."}
        ]
    }
    assert not name_supported_by_results("koshari abu daqqa", payload)


def test_name_not_supported_on_single_generic_word_overlap():
    """Regression test: 376/679 (55%) of one production run's Zomato
    matches turned out to be a DIFFERENT business that merely shared one
    common word with the Talabat name -- the old 'half the tokens' rule
    let these through. Short (<=2 significant-token) names now require
    ALL tokens to appear, not just half."""
    cases = [
        ("fuji delights", "Foodies Delights"),
        ("chai samosa", "Chai Wala Cafe"),
        ("the kain club", "The Club Restaurant"),
        ("acai stop", "The Acai Spot"),
        ("smoothie street", "Smoothie Factory"),
    ]
    for name, listing_title in cases:
        payload = {"results": [{"title": listing_title, "content": ""}]}
        assert not name_supported_by_results(name, payload), f"{name!r} should NOT match {listing_title!r}"


def test_name_supported_when_all_tokens_present():
    payload = {"results": [{"title": "Rice Factory - Dubai Investment Park", "content": ""}]}
    assert name_supported_by_results("rice factory", payload)


def test_name_supported_longer_name_needs_at_least_two_tokens():
    # 4 significant tokens ("hyderabadi", "dum", "biryani", "specialists");
    # a single generic hit ("biryani" alone) must NOT be enough, but 2
    # distinct hits should pass the absolute floor.
    single_hit = {"results": [{"title": "Best Biryani In Town", "content": ""}]}
    assert not name_supported_by_results("hyderabadi dum biryani specialists", single_hit)

    two_hits = {"results": [{"title": "Hyderabadi Dum Biryani House", "content": ""}]}
    assert name_supported_by_results("hyderabadi dum biryani specialists", two_hits)


def test_maps_url_rejects_fabricated_coordinates():
    """Regression test: the model fabricated maps.google.com/place/Name/@lat,lng
    URLs with invented coordinates -- e.g. 'Pukhtoon Darbar' got a URL that
    was never in any Tavily result."""
    payload = {"results": [{"url": "https://www.talabat.com/uae/restaurant/1/pukhtoon-darbar"}]}
    fabricated = "https://www.google.com/maps/place/Pukhtoon+Darbar/@25.2575,55.3075"
    assert not maps_url_supported_by_results(fabricated, payload)


def test_maps_url_accepts_url_literally_in_results():
    real_url = "https://maps.app.goo.gl/abc123"
    payload = {"results": [{"url": real_url}]}
    assert maps_url_supported_by_results(real_url, payload)


def test_maps_url_rejects_none():
    assert not maps_url_supported_by_results(None, {"results": []})


def test_maps_url_rejects_non_google_domain():
    """Regression test: the model reported a magicpin.com listing URL as
    if it were a google_maps_url, and it passed the naive 'is this URL in
    the results' check since magicpin really was in the results -- but
    it's not a Google Maps URL at all."""
    magicpin_url = "https://magicpin.com/uae/Dubai/Burj-Khalifa-Area/Restaurant/Mytai/store/22c5859"
    payload = {"results": [{"url": magicpin_url}]}
    assert not maps_url_supported_by_results(magicpin_url, payload)


def test_build_maps_search_url_is_a_query_link_not_a_place_link():
    url = build_maps_search_url("Pukhtoon Darbar", "Al Rigga, Dubai")
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "Pukhtoon" in url
    assert "@" not in url  # never a fabricated /place/@lat,lng style link


def test_has_independent_confirmation_rejects_talabat_only():
    """Regression test: 'Obamine Coffee Shop' only appeared on talabat.com
    pages -- that's our own source data, not a real recovery, and should
    not count as found=true."""
    payload = {
        "results": [
            {"url": "https://www.talabat.com/uae/restaurant/1/obamine-coffee-shop-muhaisnah-1",
             "title": "OBAMINE COFFEE SHOP menu for delivery | Talabat",
             "content": "OBAMINE COFFEE SHOP in Muhaisnah, UAE"},
            {"url": "https://www.talabat.com/uae/obamine-coffee-shop",
             "title": "OBAMINE COFFEE SHOP delivery service in UAE | Talabat",
             "content": "OBAMINE COFFEE SHOP"},
        ]
    }
    assert not has_independent_confirmation("obamine coffee shop", payload)


def test_has_independent_confirmation_accepts_non_talabat_source():
    payload = {
        "results": [
            {"url": "https://www.talabat.com/uae/obamine-coffee-shop", "title": "Talabat", "content": "x"},
            {"url": "https://www.zomato.com/dubai/obamine-coffee-shop", "title": "Obamine Coffee Shop, Dubai", "content": "cafe"},
        ]
    }
    assert has_independent_confirmation("obamine coffee shop", payload)


def test_flag_shared_contact_duplicates_downgrades_shared_phone():
    df = pd.DataFrame([
        {"Talabat_Restaurant_Name_Clean": "a", "Found": True, "Phone": "+971 2 403 4200", "Address": "addr a",
         "Website": None, "Google_Maps_URL": None, "Rating": None, "Review_Count": None, "Notes": None},
        {"Talabat_Restaurant_Name_Clean": "b", "Found": True, "Phone": "+971 2 403 4200", "Address": "addr b",
         "Website": None, "Google_Maps_URL": None, "Rating": None, "Review_Count": None, "Notes": None},
        {"Talabat_Restaurant_Name_Clean": "c", "Found": True, "Phone": "+971 4 111 1111", "Address": "addr c",
         "Website": None, "Google_Maps_URL": None, "Rating": None, "Review_Count": None, "Notes": None},
    ])
    result = flag_shared_contact_duplicates(df)
    assert result.loc[result["Talabat_Restaurant_Name_Clean"] == "a", "Found"].iloc[0] == False
    assert result.loc[result["Talabat_Restaurant_Name_Clean"] == "b", "Found"].iloc[0] == False
    assert result.loc[result["Talabat_Restaurant_Name_Clean"] == "c", "Found"].iloc[0] == True
