"""
tavily_search.py — builds UAE-targeted search queries and normalizes
Tavily's response, on top of tav_keys/key_rotator.py's round-robin pool.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))          # for sibling "import config"
sys.path.insert(0, str(_THIS_DIR.parent))   # for "tav_keys.key_rotator"

from tav_keys.key_rotator import TavilyKeyRotator  # noqa: E402

import config  # noqa: E402

# Errors key_rotator itself doesn't already retry-on (those are network/
# timeout blips, not key exhaustion) -- worth a tenacity retry.
_TRANSIENT_SIGNALS = ("timeout", "timed out", "connection", "temporarily")


class TransientSearchError(Exception):
    pass


def display_name(clean_name: str) -> str:
    """Title-case, matching apify/scripts/1_prepare_search_terms.py's display_name()."""
    return " ".join(w.capitalize() for w in clean_name.split())


def build_query(restaurant_name: str, area_name: str | None = None) -> str:
    """
    Bare display name + "UAE Restaurant" suffix -- the exact format that was
    manually verified to work well for the Apify pipeline (a bare name like
    "Wanderlust" often returns 0 useful results; quoting it or padding with
    "address phone contact" narrows results further, not wider). The area
    name is deliberately NOT included here: it caused the pipeline to
    surface an unrelated landmark/complex sharing that area's name instead
    of the actual restaurant (see validation.py). area_name is accepted for
    call-signature compatibility but intentionally unused.
    """
    return f"{display_name(restaurant_name)} UAE Restaurant"


class TavilySearcher:
    def __init__(self, rotator: TavilyKeyRotator | None = None) -> None:
        self.rotator = rotator or TavilyKeyRotator.from_csv(
            config.TAVILY_KEYS_CSV, timeout=config.TAVILY_TIMEOUT_SECONDS
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(config.RETRY_MAX_ATTEMPTS),
        wait=wait_random_exponential(
            multiplier=config.RETRY_MIN_WAIT, max=config.RETRY_MAX_WAIT
        ),
        retry=retry_if_exception_type(TransientSearchError),
    )
    async def search(self, restaurant_name: str, area_name: str | None = None) -> dict[str, Any]:
        query = build_query(restaurant_name, area_name)
        try:
            raw = await self.rotator.search(
                query,
                search_depth="advanced",
                max_results=config.TAVILY_MAX_RESULTS,
                include_answer="advanced",
                country="united arab emirates",
            )
        except Exception as exc:
            msg = str(exc).lower()
            if any(sig in msg for sig in _TRANSIENT_SIGNALS):
                logger.warning(f"[tavily] transient error for '{restaurant_name}': {exc}")
                raise TransientSearchError(str(exc)) from exc
            logger.error(f"[tavily] non-retryable error for '{restaurant_name}': {exc}")
            raise

        results = raw.get("results", []) or []
        return {
            "query": query,
            "answer": raw.get("answer"),
            "results": [
                {
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "content": r.get("content"),
                    "score": r.get("score"),
                }
                for r in results
            ],
        }
