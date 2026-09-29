import sys
from pathlib import Path

import pytest

TAV_DIR = Path(__file__).resolve().parent.parent
ROOT = TAV_DIR.parent
sys.path.insert(0, str(TAV_DIR))
sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: hits real Tavily/OpenAI APIs (small, cheap calls)")


@pytest.fixture
def event_loop_policy():
    import asyncio
    return asyncio.DefaultEventLoopPolicy()
