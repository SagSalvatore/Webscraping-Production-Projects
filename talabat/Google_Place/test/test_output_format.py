"""
test_output_format.py
---------------------
Validates the structure and integrity of OUTPUT.csv and OUTPUT.json.
All tests use mocked API calls — no real network required.
"""

import sys
import json
import csv
from pathlib import Path
from unittest.mock import patch

import pytest
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.fetch_places import USER_COLS, write_outputs, parse_result

import logging
logger = logging.getLogger(__name__)

# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_ROWS = [
    {
        "row_id": 1, "slug": "mcdonalds",
        "Restaurant_Name": "McDonald's",
        "Address": "Mall of Emirates, Dubai, UAE",
        "Contact_No": "+971 4 341 0000",
        "Website": "https://mcdonalds.com/ae",
        "Geo_Lat": 25.1185, "Geo_Lng": 55.2004,
        "Google_Maps_URL": "https://maps.google.com/?cid=1",
        "status": "found", "error_msg": "",
    },
    {
        "row_id": 2, "slug": "shake_shack",
        "Restaurant_Name": "Shake Shack",
        "Address": "Dubai Mall, Dubai, UAE",
        "Contact_No": "+971 4 000 0000",
        "Website": "https://shakeshack.com",
        "Geo_Lat": 25.1972, "Geo_Lng": 55.2796,
        "Google_Maps_URL": "https://maps.google.com/?cid=2",
        "status": "found", "error_msg": "",
    },
    {
        "row_id": 3, "slug": "totally_xyz_fake",
        "Restaurant_Name": "",
        "Address": "",
        "Contact_No": "",
        "Website": "",
        "Geo_Lat": "", "Geo_Lng": "",
        "Google_Maps_URL": "",
        "status": "not_found", "error_msg": "",
    },
    {
        "row_id": 4, "slug": "nyla",
        "Restaurant_Name": "Nyla",
        "Address": "JVC, Dubai, UAE",
        "Contact_No": "",
        "Website": "",
        "Geo_Lat": 25.0547, "Geo_Lng": 55.2011,
        "Google_Maps_URL": "https://maps.google.com/?cid=4",
        "status": "found_broad", "error_msg": "",
    },
]


@pytest.fixture
def output_files(tmp_path):
    """Write outputs to a temp directory and return paths."""
    csv_path  = tmp_path / "OUTPUT.csv"
    json_path = tmp_path / "OUTPUT.json"

    with (
        patch("fetch_places.OUTPUT_CSV",  str(csv_path)),
        patch("fetch_places.OUTPUT_JSON", str(json_path)),
    ):
        write_outputs(SAMPLE_ROWS, logger)

    return csv_path, json_path


# ── CSV structure ─────────────────────────────────────────────────────────────

class TestCsvFormat:
    def test_csv_file_exists(self, output_files):
        csv_path, _ = output_files
        assert csv_path.exists()

    def test_csv_has_correct_columns(self, output_files):
        csv_path, _ = output_files
        df = pd.read_csv(csv_path)
        for col in USER_COLS:
            assert col in df.columns, f"CSV missing column: {col}"

    def test_csv_row_count_matches_input(self, output_files):
        csv_path, _ = output_files
        df = pd.read_csv(csv_path)
        assert len(df) == len(SAMPLE_ROWS)

    def test_csv_utf8_encoded(self, output_files):
        csv_path, _ = output_files
        content = csv_path.read_text(encoding="utf-8")
        assert "McDonald" in content

    def test_csv_no_extra_index_column(self, output_files):
        csv_path, _ = output_files
        df = pd.read_csv(csv_path)
        assert "Unnamed: 0" not in df.columns

    def test_csv_found_rows_have_address(self, output_files):
        csv_path, _ = output_files
        df = pd.read_csv(csv_path)
        found = df[df["status"] == "found"]
        assert (found["Address"] != "").all()

    def test_csv_not_found_rows_have_empty_address(self, output_files):
        csv_path, _ = output_files
        df = pd.read_csv(csv_path)
        not_found = df[df["status"] == "not_found"]
        assert (not_found["Address"].isna() | (not_found["Address"] == "")).all()

    def test_csv_status_values_are_valid(self, output_files):
        csv_path, _ = output_files
        df = pd.read_csv(csv_path)
        valid_statuses = {"found", "found_broad", "not_found", "error"}
        assert set(df["status"].unique()).issubset(valid_statuses)


# ── JSON structure ────────────────────────────────────────────────────────────

class TestJsonFormat:
    def test_json_file_exists(self, output_files):
        _, json_path = output_files
        assert json_path.exists()

    def test_json_is_valid(self, output_files):
        _, json_path = output_files
        content = json_path.read_text(encoding="utf-8")
        data = json.loads(content)  # raises if invalid JSON
        assert isinstance(data, list)

    def test_json_row_count_matches_input(self, output_files):
        _, json_path = output_files
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert len(data) == len(SAMPLE_ROWS)

    def test_json_entry_has_required_keys(self, output_files):
        _, json_path = output_files
        data = json.loads(json_path.read_text(encoding="utf-8"))
        required = [
            "slug", "Restaurant_Name", "Address", "Contact_No",
            "Website", "Geo_Coordinates", "Google_Maps_URL", "status",
        ]
        for entry in data:
            for key in required:
                assert key in entry, f"JSON entry missing key: {key}"

    def test_json_geo_coordinates_is_nested_object(self, output_files):
        _, json_path = output_files
        data = json.loads(json_path.read_text(encoding="utf-8"))
        found_entries = [e for e in data if e["status"] == "found"]
        for entry in found_entries:
            geo = entry["Geo_Coordinates"]
            assert isinstance(geo, dict), "Geo_Coordinates should be a dict"
            assert "lat" in geo
            assert "lng" in geo

    def test_json_found_entries_have_address(self, output_files):
        _, json_path = output_files
        data = json.loads(json_path.read_text(encoding="utf-8"))
        for entry in data:
            if entry["status"] == "found":
                assert entry["Address"] != ""

    def test_json_not_found_entries_have_empty_address(self, output_files):
        _, json_path = output_files
        data = json.loads(json_path.read_text(encoding="utf-8"))
        for entry in data:
            if entry["status"] == "not_found":
                assert entry["Address"] == ""

    def test_json_unicode_preserved(self, output_files):
        _, json_path = output_files
        raw_content = json_path.read_bytes()
        # ensure_ascii=False means Arabic/non-ASCII chars aren't escaped
        content = raw_content.decode("utf-8")
        # no \uXXXX sequences for ASCII-range chars like apostrophes
        assert "McDonald" in content


# ── CSV ↔ JSON consistency ────────────────────────────────────────────────────

class TestCsvJsonConsistency:
    def test_row_counts_match(self, output_files):
        csv_path, json_path = output_files
        df   = pd.read_csv(csv_path)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert len(df) == len(data)

    def test_same_slugs_in_both_outputs(self, output_files):
        csv_path, json_path = output_files
        df   = pd.read_csv(csv_path)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        csv_slugs  = set(df["slug"].tolist())
        json_slugs = {e["slug"] for e in data}
        assert csv_slugs == json_slugs

    def test_restaurant_names_agree(self, output_files):
        csv_path, json_path = output_files
        df   = pd.read_csv(csv_path).fillna("").set_index("slug")  # NaN → "" for comparison
        data = {e["slug"]: e for e in json.loads(json_path.read_text(encoding="utf-8"))}
        for slug in df.index:
            csv_name  = df.loc[slug, "Restaurant_Name"]
            json_name = data[slug]["Restaurant_Name"]
            assert csv_name == json_name, (
                f"Name mismatch for {slug}: CSV={csv_name!r} JSON={json_name!r}"
            )

    def test_addresses_agree(self, output_files):
        csv_path, json_path = output_files
        df   = pd.read_csv(csv_path).fillna("").set_index("slug")
        data = {e["slug"]: e for e in json.loads(json_path.read_text(encoding="utf-8"))}
        for slug in df.index:
            csv_addr  = df.loc[slug, "Address"]
            json_addr = data[slug]["Address"]
            assert str(csv_addr) == str(json_addr)
