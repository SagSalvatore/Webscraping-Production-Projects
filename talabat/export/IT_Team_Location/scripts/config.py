"""Shared config for the area-sanitization pipeline."""
import os
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # .../IT_Team_Location
PROJECT = ROOT.parent.parent.parent     # .../Webscraping-Production-Projects

INPUT_JSON = ROOT / "ri-db.restaurants_id.json"
MAPPING_CSV = ROOT.parent / "location_sanitization" / "maping_address.csv"

OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"
CACHE_DIR = ROOT / "cache"
for _d in (OUTPUT_DIR, LOG_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# The key in talabat/.env is named OPEN_AI_API (not OPENAI_API_KEY).
load_dotenv(PROJECT / "talabat" / ".env")
OPENAI_KEY = os.getenv("OPEN_AI_API") or os.getenv("OPENAI_API_KEY")

MODEL = "gpt-5-mini"

# Rate limiting. Account limit is 4200 RPM; stay well under it and let the
# semaphore + tenacity absorb bursts rather than riding the ceiling.
RPM_LIMIT = 4200
MAX_CONCURRENCY = 40
REQUEST_TIMEOUT = 60

# Only DISTINCT area strings are sent to the model (5,035 distinct vs 17,164
# rows), and results are cached to disk so a re-run costs nothing.
LLM_CACHE = CACHE_DIR / "llm_area_cache.json"
