"""Tests for config.py — name cleaning and pre-classification."""
import pytest
from config import clean_name, pre_classify, KNOWN_MNC, KNOWN_LOCAL_CHAIN, RESTAURANT_TYPES


class TestCleanName:
    def test_strips_deliveroo_suffix(self):
        # "Dubai Hills" is a location tag → correctly stripped to core brand name
        raw = "French Bakery - Dubai Hills delivery from Dubai Hills - Order with Deliveroo"
        result = clean_name(raw)
        assert "delivery from" not in result
        assert "Order with Deliveroo" not in result
        assert "French Bakery" in result

    def test_strips_order_with(self):
        raw = "Pizza Hut - Mushrif delivery from Al Mushrif - Order with Deliveroo"
        result = clean_name(raw)
        assert "Order with Deliveroo" not in result
        assert "delivery from" not in result

    def test_strips_uae_suffix(self):
        raw = "Bharna Restaurant in Mussafah, UAE"
        result = clean_name(raw)
        assert "UAE" not in result

    def test_plain_name_unchanged(self):
        assert clean_name("McDonald's") == "McDonald's"
        assert clean_name("IHOP") == "IHOP"

    def test_strips_location_branch(self):
        raw = "Zaatar w Zeit - Silicon Oasis delivery from Silicon Oasis - Order with Deliveroo"
        result = clean_name(raw)
        assert "delivery" not in result.lower()

    def test_empty_string(self):
        assert clean_name("") == ""

    def test_whitespace_only(self):
        assert clean_name("   ") == ""


class TestPreClassify:
    """Tests that known chains are classified without any API call."""

    def test_mcdonalds_is_mnc(self):
        result = pre_classify("McDonald's")
        assert result is not None
        classification, outlet_type, chained_type, _ = result
        assert classification == "MNC Chain"
        assert outlet_type == "Chain"
        assert chained_type == "MNC Chain"

    def test_kfc_is_mnc(self):
        assert pre_classify("KFC")[0] == "MNC Chain"

    def test_kfc_with_location(self):
        assert pre_classify("KFC | Al Karama Mohebi")[0] == "MNC Chain"

    def test_shake_shack_is_mnc(self):
        assert pre_classify("Shake Shack")[0] == "MNC Chain"

    def test_albaik_is_local_chain(self):
        result = pre_classify("ALBAIK - Al Majaz")
        assert result is not None
        assert result[0] == "Local Chain"
        assert result[2] == "Local Chain"

    def test_chicking_is_local_chain(self):
        result = pre_classify("ChicKing - Al Nahdha")
        assert result is not None
        assert result[0] == "Local Chain"

    def test_unknown_returns_none(self):
        assert pre_classify("Pandecia Bakery Cafe") is None
        assert pre_classify("Aura By Sree") is None

    def test_paul_bakery_is_mnc(self):
        # PAUL is a French patisserie chain — MNC
        result = pre_classify("PAUL Bakery & Restaurant")
        assert result is not None
        assert result[0] == "MNC Chain"

    def test_case_insensitive(self):
        # Lookup is lowercase fragment matching, should be case-insensitive
        assert pre_classify("mcdonald's") is not None
        assert pre_classify("MCDONALD'S") is not None

    @pytest.mark.parametrize("name", list(KNOWN_MNC.keys()))
    def test_all_mnc_fragments_return_result(self, name):
        """Every fragment in KNOWN_MNC must return a non-None result."""
        result = pre_classify(name)
        assert result is not None, f"pre_classify('{name}') returned None"
        assert result[0] == "MNC Chain"

    @pytest.mark.parametrize("name", list(KNOWN_LOCAL_CHAIN.keys()))
    def test_all_local_chain_fragments_return_result(self, name):
        result = pre_classify(name)
        assert result is not None
        assert result[0] == "Local Chain"


class TestRestaurantTypes:
    def test_restaurant_types_not_empty(self):
        assert len(RESTAURANT_TYPES) > 0

    def test_qsr_in_types(self):
        assert any("QSR" in t for t in RESTAURANT_TYPES)

    def test_cafe_in_types(self):
        assert any("Café" in t or "Coffee" in t for t in RESTAURANT_TYPES)
