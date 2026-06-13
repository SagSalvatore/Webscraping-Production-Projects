import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from talabat/ root
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

# Add talabat/ root to sys.path so we can import url_collector.parser
_TALABAT_ROOT = str(Path(__file__).resolve().parents[2])
if _TALABAT_ROOT not in sys.path:
    sys.path.insert(0, _TALABAT_ROOT)

BOT_NAME = "listing_scraper"
SPIDER_MODULES = ["listing_scraper.spiders"]
NEWSPIDER_MODULE = "listing_scraper.spiders"

# Respect robots.txt? Talabat blocks crawlers there — disable for scraping
ROBOTSTXT_OBEY = False

# ------------------------------------------------------------------
# Concurrency & throttling
# ------------------------------------------------------------------
CONCURRENT_REQUESTS = 16
CONCURRENT_REQUESTS_PER_DOMAIN = 8
DOWNLOAD_DELAY = 1.0          # base delay; AutoThrottle adjusts this

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.5
AUTOTHROTTLE_MAX_DELAY = 15.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 4.0
AUTOTHROTTLE_DEBUG = False

# ------------------------------------------------------------------
# Retry settings
# ------------------------------------------------------------------
RETRY_ENABLED = True
RETRY_TIMES = 4
RETRY_HTTP_CODES = [403, 429, 500, 502, 503, 504]
RETRY_BACKOFF_BASE = 2.0

# ------------------------------------------------------------------
# Middlewares — order matters
# ------------------------------------------------------------------
DOWNLOADER_MIDDLEWARES = {
    # Disable default UA middleware
    "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
    # Our stealth headers (runs first)
    "listing_scraper.middlewares.StealthHeadersMiddleware": 300,
    # Oxylabs proxy injection
    "listing_scraper.middlewares.OxylabsProxyMiddleware": 350,
    # Built-in retry (after proxy assignment)
    "scrapy.downloadermiddlewares.retry.RetryMiddleware": 550,
    "scrapy.downloadermiddlewares.httpcompression.HttpCompressionMiddleware": 590,
}

# ------------------------------------------------------------------
# Pipelines
# ------------------------------------------------------------------
ITEM_PIPELINES = {
    "listing_scraper.pipelines.DedupeRestaurantUrlPipeline": 200,
    "listing_scraper.pipelines.JsonLinesPipeline": 300,
}

# ------------------------------------------------------------------
# Oxylabs credentials (read from .env, override here if needed)
# ------------------------------------------------------------------
OXYLABS_USERNAME = os.getenv("OXYLABS_USERNAME", "")
OXYLABS_PASSWORD = os.getenv("OXYLABS_PASSWORD", "")
OXYLABS_COUNTRY = os.getenv("OXYLABS_COUNTRY", "ae")

# ------------------------------------------------------------------
# Output
# ------------------------------------------------------------------
_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "urls"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

RESTAURANT_URLS_OUTPUT = str(_OUTPUT_DIR / "talabat_restaurant_urls.jsonl")
AREA_PROGRESS_OUTPUT  = str(_OUTPUT_DIR / "area_progress.json")
SUMMARY_OUTPUT        = str(_OUTPUT_DIR / "talabat_restaurant_urls_summary.json")

# Resume support — uncomment ONE of these before running the full crawl.
# Scrapy saves the pending request queue here so you can Ctrl+C and resume.
#
# Option A: fixed job name (always resumes the same run)
# JOBDIR = str(Path(__file__).resolve().parents[3] / ".scrapy" / "jobs" / "uae_full_run")
#
# Option B: set from command line:
#   scrapy crawl talabat_uae_listing -s JOBDIR=../../.scrapy/jobs/run1

# ------------------------------------------------------------------
# Feed export (alternative to pipeline — kept OFF to avoid duplicates)
# ------------------------------------------------------------------
# FEEDS = {RESTAURANT_URLS_OUTPUT: {"format": "jsonlines", "overwrite": False}}

# ------------------------------------------------------------------
# HTTP cache (useful for development/testing, disable in production)
# ------------------------------------------------------------------
# HTTPCACHE_ENABLED = True
# HTTPCACHE_EXPIRATION_SECS = 3600
# HTTPCACHE_DIR = ".scrapy/httpcache"

# ------------------------------------------------------------------
# Misc
# ------------------------------------------------------------------
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"
LOG_LEVEL = "INFO"
TELNETCONSOLE_ENABLED = False
COOKIES_ENABLED = True
