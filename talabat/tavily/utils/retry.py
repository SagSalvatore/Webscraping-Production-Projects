"""
utils/retry.py — Async exponential back-off with full jitter.

Algorithm: "Full Jitter" (AWS recommendation)
  sleep = random(0, min(cap, base * 2^attempt))

This spreads retries across the full window, preventing thundering herd
when many concurrent coroutines hit a rate limit simultaneously.

References:
  https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
"""

from __future__ import annotations
import asyncio
import random
from typing import Callable, Type

from openai import RateLimitError, APITimeoutError, APIConnectionError

from utils.logger import get_logger

log = get_logger(__name__)

# Exceptions that are safe to retry
_RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError)

# Default back-off config
DEFAULT_MAX_RETRIES = 6
DEFAULT_BASE_DELAY  = 1.0     # seconds
DEFAULT_CAP_DELAY   = 60.0    # seconds maximum single sleep


async def retry_async(
    coro_fn: Callable,
    *args,
    max_retries: int          = DEFAULT_MAX_RETRIES,
    base_delay:  float        = DEFAULT_BASE_DELAY,
    cap_delay:   float        = DEFAULT_CAP_DELAY,
    retryable:   tuple[Type[Exception], ...] = _RETRYABLE,
    **kwargs,
):
    """
    Call `coro_fn(*args, **kwargs)` and retry on retryable exceptions.

    Example:
        result = await retry_async(
            openai_client.chat.completions.create,
            model="gpt-4o-mini",
            messages=[...],
        )
    """
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await coro_fn(*args, **kwargs)

        except retryable as exc:
            last_exc = exc
            if attempt == max_retries:
                log.error(
                    "Max retries ({}) exhausted for {}. Last error: {}",
                    max_retries, getattr(coro_fn, "__name__", str(coro_fn)), exc,
                )
                raise

            # Full-jitter sleep: random in [0, min(cap, base * 2^attempt)]
            window = min(cap_delay, base_delay * (2 ** attempt))
            sleep  = random.uniform(0, window)

            log.warning(
                "Retryable error (attempt {}/{}): {} — sleeping {:.2f}s (full jitter)",
                attempt + 1, max_retries, type(exc).__name__, sleep,
            )
            await asyncio.sleep(sleep)

        except Exception as exc:
            # Non-retryable: re-raise immediately
            log.error("Non-retryable error in {}: {}", getattr(coro_fn, "__name__", "?"), exc)
            raise

    raise last_exc  # unreachable but satisfies type checkers
