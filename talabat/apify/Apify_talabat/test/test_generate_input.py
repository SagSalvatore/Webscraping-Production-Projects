"""
test_generate_input.py
-----------------------
Unit tests for generate_input.py — verifies query generation,
cost estimation, and input structure. No API calls, no file I/O.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from generate_input import (
    AREAS, CATEGORIES, build_search_strings,
    build_actor_input, estimate_cost,
)


class TestBuildSearchStrings:
    def test_total_query_count(self):
        """Should produce areas × categories queries."""
        queries = build_search_strings()
        assert len(queries) == len(AREAS) * len(CATEGORIES)

    def test_query_count_is_64(self):
        """16 areas × 4 categories = 64."""
        assert len(build_search_strings()) == 64

    def test_each_query_has_required_keys(self):
        for q in build_search_strings():
            assert "query"    in q, "Missing 'query'"
            assert "area"     in q, "Missing 'area'"
            assert "category" in q, "Missing 'category'"
            assert "lat"      in q, "Missing 'lat'"
            assert "lng"      in q, "Missing 'lng'"

    def test_queries_contain_dubai(self):
        for q in build_search_strings():
            assert "Dubai" in q["query"], f"Query missing 'Dubai': {q['query']}"

    def test_queries_contain_uae(self):
        for q in build_search_strings():
            assert "UAE" in q["query"], f"Query missing 'UAE': {q['query']}"

    def test_lat_lng_are_numeric(self):
        for q in build_search_strings():
            assert isinstance(q["lat"], float), f"lat not float: {q}"
            assert isinstance(q["lng"], float), f"lng not float: {q}"

    def test_lat_within_uae_range(self):
        for q in build_search_strings():
            assert 22.0 < q["lat"] < 27.0, f"lat {q['lat']} outside UAE: {q['area']}"

    def test_lng_within_uae_range(self):
        for q in build_search_strings():
            assert 51.0 < q["lng"] < 57.0, f"lng {q['lng']} outside UAE: {q['area']}"

    def test_all_four_categories_present(self):
        queries   = build_search_strings()
        cat_slugs = {q["category"] for q in queries}
        expected  = {"restaurant", "fast_food", "cafe_bakery", "delivery"}
        assert expected == cat_slugs

    def test_custom_areas_and_categories(self):
        custom_areas = [("Test Area, Dubai, UAE", 25.0, 55.0)]
        custom_cats  = [("pizza", "pizza_test")]
        queries = build_search_strings(custom_areas, custom_cats)
        assert len(queries) == 1
        assert "pizza" in queries[0]["query"]
        assert "Test Area" in queries[0]["query"]


class TestBuildActorInput:
    def setup_method(self):
        self.queries = build_search_strings()
        self.input   = build_actor_input(self.queries, max_results=40)

    def test_search_strings_count_matches_queries(self):
        assert len(self.input["searchStringsArray"]) == len(self.queries)

    def test_max_results_set(self):
        assert self.input["maxCrawledPlacesPerSearch"] == 40

    def test_language_is_english(self):
        assert self.input["language"] == "en"

    def test_country_code_is_uae(self):
        assert self.input["countryCode"] == "ae"

    def test_max_reviews_is_zero(self):
        """Reviews disabled for cost saving."""
        assert self.input["maxReviews"] == 0

    def test_max_images_is_zero(self):
        assert self.input["maxImages"] == 0

    def test_proxy_config_present(self):
        assert "proxyConfig" in self.input

    def test_proxy_uses_residential(self):
        proxy = self.input["proxyConfig"]
        assert proxy.get("useApifyProxy") is True
        assert "RESIDENTIAL" in proxy.get("apifyProxyGroups", [])

    def test_cost_saving_flags_off(self):
        assert self.input["includeOpeningHours"]     is False
        assert self.input["includePeopleAlsoSearch"] is False
        assert self.input["scrapeDirectories"]       is False
        assert self.input["additionalInfo"]          is False
        assert self.input["includeHistogram"]        is False

    def test_custom_max_results(self):
        inp = build_actor_input(self.queries, max_results=25)
        assert inp["maxCrawledPlacesPerSearch"] == 25


class TestEstimateCost:
    def test_returns_all_required_keys(self):
        est = estimate_cost(64, 40)
        for key in ["queries", "gross_results", "unique_estimated",
                    "proxy_data_gb", "proxy_cost_usd",
                    "compute_cus", "compute_cost_usd",
                    "total_cost_usd", "wall_clock_min", "budget_remaining"]:
            assert key in est, f"Missing key: {key}"

    def test_gross_results_is_queries_times_max(self):
        est = estimate_cost(64, 40)
        assert est["gross_results"] == 64 * 40

    def test_total_fits_in_5_dollar_budget(self):
        """Default config (64 queries × 40 results) must cost less than $5."""
        est = estimate_cost(64, 40)
        assert est["total_cost_usd"] < 5.00, (
            f"Default config costs ${est['total_cost_usd']} — exceeds $5 budget"
        )

    def test_budget_remaining_positive_for_default(self):
        """There should be some credit left after the default run."""
        est = estimate_cost(64, 40)
        assert est["budget_remaining"] > 0, (
            f"No budget remaining: ${est['budget_remaining']}"
        )

    def test_conservative_run_is_cheaper(self):
        est_default     = estimate_cost(64, 40)
        est_conservative = estimate_cost(48, 25)
        assert est_conservative["total_cost_usd"] < est_default["total_cost_usd"]

    def test_wall_clock_time_is_positive(self):
        est = estimate_cost(64, 40)
        assert est["wall_clock_min"] > 0

    def test_zero_queries_returns_zero_cost(self):
        est = estimate_cost(0, 40)
        assert est["total_cost_usd"] == 0.0
        assert est["gross_results"]  == 0
