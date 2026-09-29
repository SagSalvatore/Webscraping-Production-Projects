"""
test_processor.py
-----------------
Unit tests for process_results.py — verifies normalisation, deduplication,
category mapping, URL cleaning, and output structure. No Apify API calls.
"""
import sys
import json
from pathlib import Path

import pytest
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from process_results import (
    normalise_record,
    map_category,
    clean_maps_url,
    norm_address,
    _deduplicate,
    load_seen_ids,
    build_enrichment_ids,
    process,
    write_outputs,
)

SAMPLE_FILE = Path(__file__).parent / "test_data" / "sample_raw.json"

# All fields expected in every normalised record
REQUIRED_FIELDS = [
    "place_id", "Name", "Address", "Street", "Neighborhood", "City",
    "Contact_No", "Website", "Geo_Lat", "Geo_Lng", "Google_Maps_URL",
    "Plus_Code", "Category", "All_Categories", "Category_Type",
    "Rating", "Review_Count", "Rating_Distribution",
    "Price_Range", "Menu_URL", "Opening_Hours",
    "Area", "Scraped_At", "Permanently_Closed",
]


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def raw_mcdonalds():
    return {
        "placeId":      "ChIJ001",
        "title":        "McDonald's Dubai Mall",
        "categoryName": "Fast food restaurant",
        "categories":   ["Fast food restaurant", "Restaurant"],
        "address":      "Dubai Mall, Downtown Dubai, Dubai, UAE",
        "street":       "Sheikh Mohammed bin Rashid Blvd",
        "neighborhood": "Downtown Dubai",
        "city":         "Dubai",
        "phone":        "+971 4 341 0000",
        "website":      "https://mcdonalds.com/ae",
        "location":     {"lat": 25.1972, "lng": 55.2744},
        "url":          "https://maps.google.com/?cid=12345&g_mp=tracking",
        "plusCode":     "8F3Q+WF Dubai",
        "totalScore":   4.2,
        "reviewsCount": 3421,
        "reviewsDistribution": {
            "oneStar": 120, "twoStar": 80, "threeStar": 300,
            "fourStar": 900, "fiveStar": 2021,
        },
        "price":             "$$",
        "openingHours":      [],
        "menu":              "https://mcdonalds.com/ae/menu",
        "permanentlyClosed": False,
        "temporarilyClosed": False,
        "scrapedAt":         "2025-05-10T08:30:00.000Z",
    }


@pytest.fixture
def raw_closed():
    return {
        "placeId":      "ChIJ002",
        "title":        "Closed Burger Joint",
        "categoryName": "Hamburger restaurant",
        "categories":   ["Hamburger restaurant"],
        "address":      "Al Karama, Dubai, UAE",
        "street":       "Kuwait Street",
        "neighborhood": "Al Karama",
        "city":         "Dubai",
        "phone":        "",
        "website":      "",
        "location":     {"lat": 25.2363, "lng": 55.3013},
        "url":          "https://maps.google.com/?cid=99999",
        "plusCode":     "",
        "totalScore":   3.8,
        "reviewsCount": 45,
        "reviewsDistribution": {
            "oneStar": 8, "twoStar": 5, "threeStar": 12,
            "fourStar": 12, "fiveStar": 8,
        },
        "price":             "$",
        "openingHours":      [],
        "menu":              "",
        "permanentlyClosed": True,
        "temporarilyClosed": False,
        "scrapedAt":         "2025-05-10T08:32:00.000Z",
    }


@pytest.fixture
def raw_no_name():
    return {
        "placeId":      "ChIJ003",
        "title":        "",
        "categoryName": "Restaurant",
        "categories":   ["Restaurant"],
        "address":      "Deira, Dubai, UAE",
        "street":       "",
        "neighborhood": "Deira",
        "city":         "Dubai",
        "phone":        "",
        "website":      "",
        "location":     {"lat": 25.2697, "lng": 55.3094},
        "url":          "",
        "plusCode":     "",
        "totalScore":   None,
        "reviewsCount": 0,
        "reviewsDistribution": {
            "oneStar": 0, "twoStar": 0, "threeStar": 0,
            "fourStar": 0, "fiveStar": 0,
        },
        "price":             "",
        "openingHours":      [],
        "menu":              "",
        "permanentlyClosed": False,
        "temporarilyClosed": False,
        "scrapedAt":         "2025-05-10T08:36:00.000Z",
    }


# ── normalise_record ──────────────────────────────────────────────────────────

class TestNormaliseRecord:
    def test_valid_record_returns_dict(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds, area="Downtown Dubai")
        assert isinstance(result, dict)

    def test_all_required_fields_present(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds)
        for key in REQUIRED_FIELDS:
            assert key in result, f"Missing field: {key}"

    def test_name_extracted(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds)
        assert result["Name"] == "McDonald's Dubai Mall"

    def test_phone_extracted(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds)
        assert result["Contact_No"] == "+971 4 341 0000"

    def test_coordinates_extracted(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds)
        assert result["Geo_Lat"] == 25.1972
        assert result["Geo_Lng"] == 55.2744

    def test_area_injected(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds, area="Downtown Dubai")
        assert result["Area"] == "Downtown Dubai"

    def test_permanently_closed_returns_none(self, raw_closed):
        assert normalise_record(raw_closed) is None

    def test_empty_name_returns_none(self, raw_no_name):
        assert normalise_record(raw_no_name) is None

    def test_missing_phone_is_empty_string(self, raw_closed):
        raw = {**raw_closed, "permanentlyClosed": False,
               "title": "Test Place", "phone": None}
        result = normalise_record(raw)
        assert result is not None
        assert result["Contact_No"] == ""

    def test_maps_url_cleaned(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds)
        assert "g_mp=" not in result["Google_Maps_URL"]
        assert "cid=12345" in result["Google_Maps_URL"]

    def test_street_extracted(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds)
        assert result["Street"] == "Sheikh Mohammed bin Rashid Blvd"

    def test_neighborhood_extracted(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds)
        assert result["Neighborhood"] == "Downtown Dubai"

    def test_city_extracted(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds)
        assert result["City"] == "Dubai"

    def test_plus_code_extracted(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds)
        assert result["Plus_Code"] == "8F3Q+WF Dubai"

    def test_all_categories_is_list(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds)
        assert isinstance(result["All_Categories"], list)
        assert len(result["All_Categories"]) >= 1

    def test_all_categories_contains_primary(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds)
        assert "Fast food restaurant" in result["All_Categories"]

    def test_missing_categories_falls_back_to_primary(self, raw_mcdonalds):
        raw = {k: v for k, v in raw_mcdonalds.items() if k != "categories"}
        result = normalise_record(raw)
        assert result["All_Categories"] == ["Fast food restaurant"]

    def test_rating_distribution_has_correct_keys(self, raw_mcdonalds):
        rd = normalise_record(raw_mcdonalds)["Rating_Distribution"]
        for key in ("1_star", "2_star", "3_star", "4_star", "5_star"):
            assert key in rd, f"Missing key: {key}"

    def test_rating_distribution_values_are_ints(self, raw_mcdonalds):
        rd = normalise_record(raw_mcdonalds)["Rating_Distribution"]
        for k, v in rd.items():
            assert isinstance(v, int), f"{k} is not int: {v}"

    def test_rating_distribution_sums_to_review_count(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds)
        assert sum(result["Rating_Distribution"].values()) == result["Review_Count"]

    def test_missing_rating_distribution_uses_zeros(self, raw_mcdonalds):
        raw = {k: v for k, v in raw_mcdonalds.items() if k != "reviewsDistribution"}
        result = normalise_record(raw)
        assert all(v == 0 for v in result["Rating_Distribution"].values())

    def test_price_range_extracted(self, raw_mcdonalds):
        assert normalise_record(raw_mcdonalds)["Price_Range"] == "$$"

    def test_menu_url_extracted(self, raw_mcdonalds):
        assert normalise_record(raw_mcdonalds)["Menu_URL"] == "https://mcdonalds.com/ae/menu"

    def test_opening_hours_is_list(self, raw_mcdonalds):
        assert isinstance(normalise_record(raw_mcdonalds)["Opening_Hours"], list)

    def test_scraped_at_extracted(self, raw_mcdonalds):
        assert normalise_record(raw_mcdonalds)["Scraped_At"] == "2025-05-10T08:30:00.000Z"

    def test_permanently_closed_flag_is_bool(self, raw_mcdonalds):
        result = normalise_record(raw_mcdonalds)
        assert isinstance(result["Permanently_Closed"], bool)
        assert result["Permanently_Closed"] is False

    def test_null_rating_becomes_none(self, raw_no_name):
        raw = {**raw_no_name, "title": "Has No Score Place"}
        assert normalise_record(raw)["Rating"] is None

    def test_missing_price_is_empty_string(self, raw_mcdonalds):
        raw = {k: v for k, v in raw_mcdonalds.items() if k != "price"}
        assert normalise_record(raw)["Price_Range"] == ""

    def test_missing_menu_is_empty_string(self, raw_mcdonalds):
        raw = {k: v for k, v in raw_mcdonalds.items() if k != "menu"}
        assert normalise_record(raw)["Menu_URL"] == ""


# ── norm_address ──────────────────────────────────────────────────────────────

class TestNormAddress:
    def test_strips_uae(self):
        assert "uae" not in norm_address("Dubai Mall, Downtown Dubai, Dubai, UAE")

    def test_strips_united_arab_emirates(self):
        result = norm_address("Satwa, Dubai, United Arab Emirates")
        assert "united arab emirates" not in result

    def test_same_address_different_country_token(self):
        """'UAE' and 'United Arab Emirates' variants must produce the same key."""
        a = norm_address("Dubai Mall, Downtown Dubai, Dubai, UAE")
        b = norm_address("Dubai Mall, Downtown Dubai, Dubai, United Arab Emirates")
        assert a == b

    def test_removes_commas_and_hyphens(self):
        result = norm_address("Al Karama, Dubai - UAE")
        assert "," not in result
        assert "-" not in result

    def test_collapses_whitespace(self):
        result = norm_address("Dubai   Mall   Downtown")
        assert "  " not in result

    def test_empty_returns_empty(self):
        assert norm_address("") == ""

    def test_none_like_empty(self):
        assert norm_address(None) == ""

    def test_lowercase(self):
        assert norm_address("DUBAI MALL") == "dubai mall"

    def test_different_addresses_produce_different_keys(self):
        a = norm_address("Dubai Mall, Downtown Dubai, Dubai, UAE")
        b = norm_address("JBR, Dubai Marina, Dubai, UAE")
        assert a != b


# ── _deduplicate ──────────────────────────────────────────────────────────────

def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Helper: build a minimal normalised DataFrame for dedup tests."""
    defaults = {
        "place_id": "", "Name": "", "Address": "", "Street": "",
        "Neighborhood": "", "City": "", "Contact_No": "", "Website": "",
        "Geo_Lat": 0.0, "Geo_Lng": 0.0, "Google_Maps_URL": "", "Plus_Code": "",
        "Category": "", "All_Categories": [], "Category_Type": "Other",
        "Rating": None, "Review_Count": 0, "Rating_Distribution": {},
        "Price_Range": "", "Menu_URL": "", "Opening_Hours": [],
        "Area": "", "Scraped_At": "", "Permanently_Closed": False,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


class TestDeduplicate:
    def test_layer1_removes_same_place_id(self):
        """Same placeId from two area queries → keep one."""
        df = _make_df([
            {"place_id": "P1", "Name": "KFC", "Address": "Dubai Mall, Dubai, UAE",  "Area": "Downtown"},
            {"place_id": "P1", "Name": "KFC", "Address": "Dubai Mall, Dubai, UAE",  "Area": "Business Bay"},
        ])
        result = _deduplicate(df)
        assert len(result) == 1

    def test_layer1_keeps_different_place_ids(self):
        """KFC at two different real locations must both survive."""
        df = _make_df([
            {"place_id": "P1", "Name": "KFC", "Address": "Dubai Mall, Dubai, UAE",    "Area": "Downtown"},
            {"place_id": "P2", "Name": "KFC", "Address": "JBR, Dubai Marina, UAE",    "Area": "Marina"},
        ])
        result = _deduplicate(df)
        assert len(result) == 2

    def test_layer2_removes_same_name_same_address_different_place_id(self):
        """Same restaurant, different placeId (stale ID edge case) → keep one."""
        df = _make_df([
            {"place_id": "P1", "Name": "Ravi Restaurant", "Address": "Satwa, Dubai, UAE"},
            {"place_id": "P2", "Name": "Ravi Restaurant", "Address": "Satwa, Dubai, UAE"},
        ])
        result = _deduplicate(df)
        assert len(result) == 1

    def test_layer2_keeps_different_restaurants_same_address(self):
        """
        Two different outlets inside the same food court share an address.
        They must NOT be collapsed — only the name differs.
        """
        df = _make_df([
            {"place_id": "P1", "Name": "McDonald's", "Address": "Dubai Mall, Downtown Dubai, UAE"},
            {"place_id": "P2", "Name": "KFC",         "Address": "Dubai Mall, Downtown Dubai, UAE"},
        ])
        result = _deduplicate(df)
        assert len(result) == 2

    def test_layer2_treats_uae_variants_as_same_address(self):
        """
        'Dubai, UAE' and 'Dubai, United Arab Emirates' must normalise to the
        same key so the duplicate is removed even though strings differ.
        """
        df = _make_df([
            {"place_id": "P1", "Name": "Subway",
             "Address": "Marina Walk, Dubai Marina, Dubai, UAE"},
            {"place_id": "P2", "Name": "Subway",
             "Address": "Marina Walk, Dubai Marina, Dubai, United Arab Emirates"},
        ])
        result = _deduplicate(df)
        assert len(result) == 1

    def test_empty_place_id_records_dropped(self):
        """Records with an empty placeId are discarded (cannot be reliably deduped)."""
        df = _make_df([
            {"place_id": "",   "Name": "Mystery Place", "Address": "Dubai, UAE"},
            {"place_id": "P1", "Name": "Known Place",   "Address": "Dubai, UAE"},
        ])
        result = _deduplicate(df)
        assert len(result) == 1
        assert result.iloc[0]["Name"] == "Known Place"

    def test_no_address_records_are_not_collapsed_by_name(self):
        """
        Two records with no address but different placeIds must both survive
        (we can't confirm they're the same place without an address).
        """
        df = _make_df([
            {"place_id": "P1", "Name": "Ghost Kitchen", "Address": ""},
            {"place_id": "P2", "Name": "Ghost Kitchen", "Address": ""},
        ])
        result = _deduplicate(df)
        assert len(result) == 2

    def test_temp_columns_not_in_output(self):
        """Internal _name / _addr helper columns must not leak into the result."""
        df = _make_df([
            {"place_id": "P1", "Name": "Test", "Address": "Dubai, UAE"},
        ])
        result = _deduplicate(df)
        assert "_name" not in result.columns
        assert "_addr" not in result.columns

    def test_first_occurrence_is_kept(self):
        """When deduplicating, the first-seen record is preserved."""
        df = _make_df([
            {"place_id": "P1", "Name": "Burger King", "Address": "Mall of Emirates, UAE",
             "Rating": 4.5},
            {"place_id": "P1", "Name": "Burger King", "Address": "Mall of Emirates, UAE",
             "Rating": 3.0},
        ])
        result = _deduplicate(df)
        assert result.iloc[0]["Rating"] == 4.5


# ── load_seen_ids ─────────────────────────────────────────────────────────────

class TestLoadSeenIds:
    def test_returns_empty_set_for_missing_file(self, tmp_path):
        ids = load_seen_ids(source=tmp_path / "nonexistent.json")
        assert ids == set()

    def test_loads_ids_from_valid_file(self, tmp_path):
        data = [
            {"place_id": "ChIJ_AAA", "Name": "Cafe X"},
            {"place_id": "ChIJ_BBB", "Name": "Cafe Y"},
        ]
        f = tmp_path / "out.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        ids = load_seen_ids(source=f)
        assert ids == {"ChIJ_AAA", "ChIJ_BBB"}

    def test_ignores_records_without_place_id(self, tmp_path):
        data = [{"Name": "No ID Place"}]
        f = tmp_path / "out.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        ids = load_seen_ids(source=f)
        assert ids == set()

    def test_returns_empty_on_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("NOT JSON", encoding="utf-8")
        ids = load_seen_ids(source=f)
        assert ids == set()

    def test_returns_set_not_list(self, tmp_path):
        data = [{"place_id": "P1"}, {"place_id": "P2"}]
        f = tmp_path / "out.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        assert isinstance(load_seen_ids(source=f), set)


# ── build_enrichment_ids ──────────────────────────────────────────────────────

class TestBuildEnrichmentIds:
    def test_returns_list(self, tmp_path):
        data = [{"place_id": "P1", "Name": "A"}, {"place_id": "P2", "Name": "B"}]
        f = tmp_path / "out.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        result = build_enrichment_ids(source=f)
        assert isinstance(result, list)

    def test_returns_correct_ids(self, tmp_path):
        data = [{"place_id": "P1"}, {"place_id": "P2"}]
        f = tmp_path / "out.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        assert build_enrichment_ids(source=f) == ["P1", "P2"]

    def test_empty_list_for_missing_file(self, tmp_path):
        assert build_enrichment_ids(source=tmp_path / "nope.json") == []

    def test_excludes_empty_place_ids(self, tmp_path):
        data = [{"place_id": ""}, {"place_id": "P1"}]
        f = tmp_path / "out.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        result = build_enrichment_ids(source=f)
        assert result == ["P1"]


# ── process — skip_seen ───────────────────────────────────────────────────────

class TestProcessSkipSeen:
    def _write_seen(self, tmp_path, ids: list[str]) -> Path:
        data = [{"place_id": pid, "Name": "dummy"} for pid in ids]
        f = tmp_path / "OUTPUT.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        return f

    def test_skip_seen_removes_known_place_ids(self, tmp_path, monkeypatch):
        """If a placeId is already in OUTPUT.json it must not appear in results."""
        import process_results as pr
        seen_file = self._write_seen(tmp_path, ["ChIJN1t_tDeuEmsR001"])  # McDonald's
        monkeypatch.setattr(pr, "OUTPUT_JSON", seen_file)

        df = process(SAMPLE_FILE, skip_seen=True)
        assert "McDonald's Dubai Mall" not in df["Name"].values

    def test_skip_seen_false_keeps_all_records(self, tmp_path, monkeypatch):
        """Default (skip_seen=False) must not filter anything based on OUTPUT.json."""
        import process_results as pr
        seen_file = self._write_seen(tmp_path, ["ChIJN1t_tDeuEmsR001"])
        monkeypatch.setattr(pr, "OUTPUT_JSON", seen_file)

        df_all  = process(SAMPLE_FILE, skip_seen=False)
        df_skip = process(SAMPLE_FILE, skip_seen=True)
        assert len(df_all) > len(df_skip)

    def test_skip_seen_empty_seen_file_changes_nothing(self, tmp_path, monkeypatch):
        """If OUTPUT.json is empty, skip_seen=True must produce the same result as False."""
        import process_results as pr
        seen_file = self._write_seen(tmp_path, [])  # no known IDs
        monkeypatch.setattr(pr, "OUTPUT_JSON", seen_file)

        df_false = process(SAMPLE_FILE, skip_seen=False)
        df_true  = process(SAMPLE_FILE, skip_seen=True)
        assert len(df_false) == len(df_true)


# ── map_category ──────────────────────────────────────────────────────────────

class TestMapCategory:
    def test_fast_food_maps_to_qsr(self):
        assert map_category("Fast food restaurant") == "QSR"

    def test_hamburger_maps_to_qsr(self):
        assert map_category("Hamburger restaurant") == "QSR"

    def test_cafe_maps_to_cafe(self):
        assert map_category("Cafe") == "Cafe"

    def test_coffee_shop_maps_to_cafe(self):
        assert map_category("Coffee shop") == "Cafe"

    def test_bakery_maps_to_bakery(self):
        assert map_category("Bakery") == "Bakery"

    def test_meal_delivery_maps_to_cloud_kitchen(self):
        assert map_category("Meal delivery") == "Cloud Kitchen"

    def test_ice_cream_maps_to_dessert(self):
        assert map_category("Ice cream shop") == "Dessert & Drinks"

    def test_restaurant_maps_to_full_service(self):
        assert map_category("Restaurant") == "Full-Service"

    def test_indian_restaurant_maps_to_full_service(self):
        assert map_category("Indian restaurant") == "Full-Service"

    def test_bar_maps_to_bar(self):
        assert map_category("Bar") == "Bar"

    def test_unknown_category_maps_to_other(self):
        assert map_category("Laser tag arena") == "Other"

    def test_empty_string_maps_to_other(self):
        assert map_category("") == "Other"

    def test_case_insensitive(self):
        assert map_category("CAFE") == "Cafe"
        assert map_category("Fast Food Restaurant") == "QSR"


# ── clean_maps_url ────────────────────────────────────────────────────────────

class TestCleanMapsUrl:
    def test_strips_tracking_params(self):
        raw   = "https://maps.google.com/?cid=12345&g_mp=LONGTRACKINGSTRING"
        clean = clean_maps_url(raw)
        assert clean == "https://maps.google.com/?cid=12345"

    def test_url_without_tracking_unchanged(self):
        assert "12345" in clean_maps_url("https://maps.google.com/?cid=12345")

    def test_empty_url_returns_empty(self):
        assert clean_maps_url("") == ""

    def test_none_handled(self):
        assert clean_maps_url(None) == ""


# ── process (integration) ─────────────────────────────────────────────────────

class TestProcess:
    def test_processes_sample_file(self):
        df = process(SAMPLE_FILE, include_closed=False)
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    def test_closed_places_excluded_by_default(self):
        df = process(SAMPLE_FILE, include_closed=False)
        assert "Closed Burger Joint" not in df["Name"].values

    def test_no_name_records_excluded(self):
        df = process(SAMPLE_FILE, include_closed=False)
        assert (df["Name"] == "").sum() == 0

    def test_duplicates_removed(self):
        """McDonald's appears twice in sample (same placeId) — must appear once."""
        df = process(SAMPLE_FILE, include_closed=False)
        assert df[df["Name"] == "McDonald's Dubai Mall"].shape[0] == 1

    def test_required_columns_present(self):
        df = process(SAMPLE_FILE)
        for col in REQUIRED_FIELDS:
            assert col in df.columns, f"Column missing: {col}"

    def test_category_type_mapped(self):
        df = process(SAMPLE_FILE)
        assert "QSR"           in df["Category_Type"].values
        assert "Cafe"          in df["Category_Type"].values
        assert "Cloud Kitchen" in df["Category_Type"].values

    def test_uae_addresses_only(self):
        df = process(SAMPLE_FILE)
        uae_kw = ["UAE", "United Arab Emirates", "Dubai",
                  "Sharjah", "Abu Dhabi", "Ajman"]
        for addr in df["Address"]:
            assert any(k.lower() in addr.lower() for k in uae_kw), (
                f"Non-UAE address: {addr}"
            )

    def test_all_categories_column_is_list_type(self):
        df = process(SAMPLE_FILE)
        for val in df["All_Categories"]:
            assert isinstance(val, list)

    def test_rating_distribution_column_is_dict_type(self):
        df = process(SAMPLE_FILE)
        for val in df["Rating_Distribution"]:
            assert isinstance(val, dict)

    def test_rating_distribution_keys(self):
        df = process(SAMPLE_FILE)
        for val in df["Rating_Distribution"]:
            for key in ("1_star", "2_star", "3_star", "4_star", "5_star"):
                assert key in val

    def test_price_range_column_present(self):
        assert "Price_Range" in process(SAMPLE_FILE).columns

    def test_scraped_at_column_present(self):
        df = process(SAMPLE_FILE)
        assert "Scraped_At" in df.columns
        assert (df["Scraped_At"] != "").any()


# ── write_outputs ─────────────────────────────────────────────────────────────

class TestWriteOutputs:
    def test_csv_and_json_written(self, tmp_path, monkeypatch):
        import process_results as pr
        monkeypatch.setattr(pr, "OUTPUT_CSV",  tmp_path / "OUT.csv")
        monkeypatch.setattr(pr, "OUTPUT_JSON", tmp_path / "OUT.json")
        monkeypatch.setattr(pr, "OUTPUT_DIR",  tmp_path)

        write_outputs(process(SAMPLE_FILE))
        assert (tmp_path / "OUT.csv").exists()
        assert (tmp_path / "OUT.json").exists()

    def test_json_has_geo_coordinates_object(self, tmp_path, monkeypatch):
        import process_results as pr
        monkeypatch.setattr(pr, "OUTPUT_CSV",  tmp_path / "OUT.csv")
        monkeypatch.setattr(pr, "OUTPUT_JSON", tmp_path / "OUT.json")
        monkeypatch.setattr(pr, "OUTPUT_DIR",  tmp_path)

        write_outputs(process(SAMPLE_FILE))
        data = json.loads((tmp_path / "OUT.json").read_text(encoding="utf-8"))
        for entry in data:
            assert "Geo_Coordinates" in entry
            assert "lat" in entry["Geo_Coordinates"]
            assert "lng" in entry["Geo_Coordinates"]

    def test_csv_row_count_matches_json(self, tmp_path, monkeypatch):
        import process_results as pr
        monkeypatch.setattr(pr, "OUTPUT_CSV",  tmp_path / "OUT.csv")
        monkeypatch.setattr(pr, "OUTPUT_JSON", tmp_path / "OUT.json")
        monkeypatch.setattr(pr, "OUTPUT_DIR",  tmp_path)

        write_outputs(process(SAMPLE_FILE))
        csv_rows  = pd.read_csv(tmp_path / "OUT.csv")
        json_rows = json.loads((tmp_path / "OUT.json").read_text(encoding="utf-8"))
        assert len(csv_rows) == len(json_rows)

    def test_json_has_rating_distribution(self, tmp_path, monkeypatch):
        import process_results as pr
        monkeypatch.setattr(pr, "OUTPUT_CSV",  tmp_path / "OUT.csv")
        monkeypatch.setattr(pr, "OUTPUT_JSON", tmp_path / "OUT.json")
        monkeypatch.setattr(pr, "OUTPUT_DIR",  tmp_path)

        write_outputs(process(SAMPLE_FILE))
        data = json.loads((tmp_path / "OUT.json").read_text(encoding="utf-8"))
        for entry in data:
            rd = entry["Rating_Distribution"]
            assert isinstance(rd, dict)
            for key in ("1_star", "2_star", "3_star", "4_star", "5_star"):
                assert key in rd

    def test_json_has_all_categories_list(self, tmp_path, monkeypatch):
        import process_results as pr
        monkeypatch.setattr(pr, "OUTPUT_CSV",  tmp_path / "OUT.csv")
        monkeypatch.setattr(pr, "OUTPUT_JSON", tmp_path / "OUT.json")
        monkeypatch.setattr(pr, "OUTPUT_DIR",  tmp_path)

        write_outputs(process(SAMPLE_FILE))
        data = json.loads((tmp_path / "OUT.json").read_text(encoding="utf-8"))
        for entry in data:
            assert isinstance(entry["All_Categories"], list)
            assert len(entry["All_Categories"]) >= 1

    def test_json_has_full_schema(self, tmp_path, monkeypatch):
        import process_results as pr
        monkeypatch.setattr(pr, "OUTPUT_CSV",  tmp_path / "OUT.csv")
        monkeypatch.setattr(pr, "OUTPUT_JSON", tmp_path / "OUT.json")
        monkeypatch.setattr(pr, "OUTPUT_DIR",  tmp_path)

        write_outputs(process(SAMPLE_FILE))
        data = json.loads((tmp_path / "OUT.json").read_text(encoding="utf-8"))

        expected_keys = {
            "place_id", "Name", "Address", "Street", "Neighborhood", "City",
            "Contact_No", "Website", "Geo_Coordinates", "Google_Maps_URL",
            "Plus_Code", "Category", "All_Categories", "Category_Type",
            "Rating", "Review_Count", "Rating_Distribution",
            "Price_Range", "Menu_URL", "Opening_Hours",
            "Area", "Scraped_At", "Permanently_Closed",
        }
        for entry in data:
            missing = expected_keys - entry.keys()
            assert not missing, f"JSON entry missing keys: {missing}"

    def test_csv_all_categories_is_pipe_separated(self, tmp_path, monkeypatch):
        """All_Categories in CSV must be a pipe-separated string, not a list repr."""
        import process_results as pr
        monkeypatch.setattr(pr, "OUTPUT_CSV",  tmp_path / "OUT.csv")
        monkeypatch.setattr(pr, "OUTPUT_JSON", tmp_path / "OUT.json")
        monkeypatch.setattr(pr, "OUTPUT_DIR",  tmp_path)

        write_outputs(process(SAMPLE_FILE))
        csv_df = pd.read_csv(tmp_path / "OUT.csv")
        for val in csv_df["All_Categories"].dropna():
            assert "[" not in str(val), f"List repr in CSV: {val}"
