"""
Shared pytest fixtures for Talabat scraper tests.
"""

import sys
from pathlib import Path

import pytest

# Ensure talabat/ root is importable
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from url_collector.config import CollectorConfig, ProxyConfig


# Single area used across all live-fetch tests
TEST_AREA_URL = "https://www.talabat.com/uae/restaurants/2652/industrial-area-18"
TEST_AREA_NAME = "Industrial Area 18"
TEST_AREA_ID = 2652

# A restaurant URL we know exists in that area (for sanity checks)
TEST_RESTAURANT_URL = (
    "https://www.talabat.com/uae/restaurant/730009/"
    "al-banosh-seafood-restaurant-muleha?aid=2652"
)


@pytest.fixture(scope="session")
def proxy_config() -> ProxyConfig:
    return ProxyConfig()


@pytest.fixture(scope="session")
def collector_config() -> CollectorConfig:
    return CollectorConfig(
        min_delay=0.5,
        max_delay=1.5,
        max_retries=2,
        timeout=20,
    )


@pytest.fixture(scope="session")
def fetcher(collector_config, proxy_config):
    """Shared async fetcher — created once per test session."""
    import asyncio
    from url_collector.fetcher import TalabatFetcher

    loop = asyncio.new_event_loop()
    f = TalabatFetcher(collector_config, proxy_config)

    yield f

    # Teardown
    async def _close():
        await f.close()

    loop.run_until_complete(_close())
    loop.close()
