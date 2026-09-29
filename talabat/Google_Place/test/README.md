# Test Suite — Google Places Fetcher

## Structure

```
test/
├── conftest.py              # shared fixtures (mock responses, sample slugs)
├── test_parser.py           # unit tests: slug_to_name(), parse_result()
├── test_edge_cases.py       # mocked edge cases: not-found, errors, chains
├── test_api_connection.py   # integration tests: real API calls (needs key)
├── test_output_format.py    # CSV & JSON output structure validation
└── test_data/
    ├── sample_5.csv         # 5-restaurant sample for quick manual runs
    └── mock_api_response.json  # reference response shapes used in tests
```

---

## Setup

```bash
pip install pytest requests pandas python-dotenv tqdm tenacity
```

For integration tests, ensure your `.env` file has:
```
GOOGLE_API_KEY=AIzaSy...your_key_here...
```

---

## Running Tests

### All unit + edge case tests (no API key needed)
```bash
pytest test/ -v --ignore=test/test_api_connection.py
```

### Only unit tests (parser logic)
```bash
pytest test/test_parser.py -v
```

### Only edge case tests (mocked API)
```bash
pytest test/test_edge_cases.py -v
```

### Only output format tests
```bash
pytest test/test_output_format.py -v
```

### Integration tests (requires real API key + billing enabled)
```bash
pytest test/test_api_connection.py -v -m integration
```

### Full suite (all tests)
```bash
pytest test/ -v
```

### With coverage report
```bash
pip install pytest-cov
pytest test/ --cov=fetch_places --cov-report=term-missing -v
```

---

## Test Categories

| File | Needs API Key | Speed | What it tests |
|------|:---:|:---:|---|
| `test_parser.py` | No | Fast | `slug_to_name()`, `parse_result()` field extraction |
| `test_edge_cases.py` | No | Fast | Fallback search, error handling, missing fields, chains |
| `test_output_format.py` | No | Fast | CSV columns, JSON structure, CSV↔JSON consistency |
| `test_api_connection.py` | **Yes** | Slow | Live API: connectivity, UAE bounding box, real restaurants |

---

## Edge Cases Covered

| Scenario | Test |
|---|---|
| Restaurant not found in Dubai → falls back to broad UAE search | `test_edge_cases.py::TestFallbackToBroadSearch` |
| Both searches miss → status `not_found`, empty fields | `test_edge_cases.py::TestFallbackToBroadSearch` |
| Chain restaurant (multiple locations) | `test_edge_cases.py::TestChainRestaurants` |
| Generic name slug (eagle, goat) | `test_edge_cases.py::TestGenericNames` |
| HTTP 403 (bad API key) | `test_edge_cases.py::TestErrorHandling` |
| Network timeout after retries | `test_edge_cases.py::TestErrorHandling` |
| Missing phone → empty string (not null) | `test_edge_cases.py::TestMissingFields` |
| Missing website → empty string | `test_edge_cases.py::TestMissingFields` |
| Duplicate slugs in CSV (pf_changs vs p_f_changs) | `test_edge_cases.py::TestDuplicateSlugs` |
| Fake/nonexistent restaurant | `test_api_connection.py::test_fake_restaurant_not_found` |
| Sharjah restaurant found via Dubai location bias | `test_api_connection.py::test_sharjah_restaurant_found_with_bias` |

---

## Sample Data

`test_data/sample_5.csv` contains 5 restaurants for quick manual validation:
- `mcdonalds` — global chain, should always resolve
- `pf_changs` — popular Dubai mall chain
- `baskin_robbins` — global ice cream chain
- `shake_shack` — Dubai malls
- `totally_xyz_fake_place_99` — intentional not-found

To run the main script against just these 5 (for a quick sanity check), temporarily copy them into a test input:
```bash
python fetch_places.py  # uses RESTRO_LIST.csv (first 500)
```
