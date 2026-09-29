"""
config.py — shared settings for the Tavily + OpenAI "Not Found" recovery pipeline.
"""
from pathlib import Path

from dotenv import load_dotenv
import os

ROOT = Path(r"C:\Users\SagarSingh\Downloads\Google_Place")
load_dotenv(ROOT / ".env", override=True)

OPENAI_API_KEY = os.getenv("OPEN_AI_API", "")
if not OPENAI_API_KEY:
    raise RuntimeError("OPEN_AI_API not set in .env")

TAVILY_KEYS_CSV = ROOT / "tav_keys" / "TAV.csv"

TAV_DIR = ROOT / "tav"
OUTPUT_DIR = TAV_DIR / "output"
SAMPLE_DIR = OUTPUT_DIR / "sample"
PRODUCTION_DIR = OUTPUT_DIR / "production"
CHECKPOINT_DIR = TAV_DIR / "checkpoints"
PRODUCTION_CHECKPOINT_FILE = CHECKPOINT_DIR / "production_checkpoint.json"

NOT_FOUND_CSV = ROOT / "map" / "output" / "NotFound_Branches.csv"

# OpenAI extraction model. gpt-4o-mini is cheap/fast and fine for structured
# extraction from search snippets -- this is a classification/extraction
# task, not open-ended reasoning.
OPENAI_MODEL = "gpt-4o-mini"

# Tier-3 OpenAI rate limit for this model class is ~4200 RPM. We stay well
# under that with a concurrency cap + tenacity backoff on 429s.
OPENAI_MAX_CONCURRENCY = 40
OPENAI_RPM_CAP = 3500

# Tavily: round-robin across N keys already spreads load: N concurrent
# in-flight searches is a safe starting point (one per key at a time).
TAVILY_MAX_CONCURRENCY = 18
TAVILY_TIMEOUT_SECONDS = 25
TAVILY_MAX_RESULTS = 5

# Retry policy shared by both API clients.
RETRY_MAX_ATTEMPTS = 5
RETRY_MIN_WAIT = 1
RETRY_MAX_WAIT = 20

# OpenAI approximate per-token pricing (USD), gpt-4o-mini, for cost tracking.
OPENAI_INPUT_COST_PER_1M = 0.15
OPENAI_OUTPUT_COST_PER_1M = 0.60

# Hard safety stop for production runs. Observed real cost across today's
# testing: ~$0.00037-0.00044/name -> full 5,145-name run projects to
# ~$1.91-$2.25. Ceiling set well under the user's stated $3 budget, leaving
# headroom for in-flight requests already started when the check trips.
PRODUCTION_COST_CEILING_USD = 2.70

DEFAULT_SAMPLE_SIZE = 150
DEFAULT_SAMPLE_SEED = 42
