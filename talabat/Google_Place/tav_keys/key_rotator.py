"""
key_rotator.py — Rotating pool of Tavily API keys.



Key-distribution strategy: ROUND-ROBIN on every search.
  • _idx advances by 1 after every successful call (wraps around)
  • On 429 / credit exhaustion, that key is blacklisted and skipped
  • All 23 keys are used proportionally — no single key takes all the load

Usage:
    rotator = TavilyKeyRotator.from_csv(Path(""), timeout=25)
    result  = await rotator.search('"Starbucks" UAE restaurant', max_results=3)
"""
from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Any

from tavily import AsyncTavilyClient

_EXHAUST_SIGNALS = ("429", "credit", "limit", "quota", "too many", "exceeded")


class TavilyKeyRotator:
    """
    Round-robin pool of Tavily API keys with automatic blacklisting on exhaustion.

    Every successful search advances the key pointer by 1 (modulo active keys),
    so load is spread evenly across all 23 keys.  On a 429 / credit error the
    offending key is added to _blacklisted and skipped going forward.
    """

    def __init__(self, keys: list[str], timeout: float = 25.0) -> None:
        if not keys:
            raise ValueError("TavilyKeyRotator requires at least one key.")
        self._keys        = keys
        self._timeout     = timeout
        self._rr_idx      = 0          # round-robin pointer (wraps)
        self._blacklisted : set[int] = set()
        self._lock        = asyncio.Lock()
        self.searches_made = 0
        self.rotations    = 0

    # ── Constructors ──────────────────────────────────────────────────────────

    @classmethod
    def from_csv(cls, path: Path | str, timeout: float = 25.0) -> "TavilyKeyRotator":
        """Load keys from tav_keys.csv (S.No, Keys columns)."""
        path = Path(path)
        keys: list[str] = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                k = row.get("Keys", "").strip()
                if k:
                    keys.append(k)
        if not keys:
            raise ValueError(f"No keys found in {path}")
        print(
            f"[TavilyKeyRotator] Loaded {len(keys)} API keys from {path.name} "
            f"(round-robin, timeout={timeout}s)"
        )
        return cls(keys, timeout=timeout)

    # ── Internal: pick next active key ───────────────────────────────────────

    def _next_active_idx(self) -> int:
        """
        Return the next non-blacklisted key index (round-robin).
        Caller MUST hold self._lock.
        Raises RuntimeError if all keys are blacklisted.
        """
        n = len(self._keys)
        for _ in range(n):
            idx = self._rr_idx % n
            self._rr_idx += 1
            if idx not in self._blacklisted:
                return idx
        raise RuntimeError(
            f"All {n} Tavily API keys are exhausted / blacklisted."
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def search(self, query: str, **kwargs: Any) -> dict:
        """
        Fire one Tavily search using the next round-robin key.
        On 429 / credit exhaustion the key is blacklisted and the call retries.
        On any other exception it propagates immediately.
        timeout is passed as an integer to the Tavily search() call.
        """
        while True:
            async with self._lock:
                idx = self._next_active_idx()   # advances _rr_idx

            client = AsyncTavilyClient(api_key=self._keys[idx])
            try:
                result = await client.search(
                    query, timeout=int(self._timeout), **kwargs
                )
                self.searches_made += 1
                return result
            except Exception as exc:
                err_msg = str(exc).lower()
                if any(sig in err_msg for sig in _EXHAUST_SIGNALS):
                    async with self._lock:
                        if idx not in self._blacklisted:
                            self._blacklisted.add(idx)
                            self.rotations += 1
                            active_left = len(self._keys) - len(self._blacklisted)
                            print(
                                f"[key_rotator] Key #{idx + 1} exhausted — "
                                f"{active_left}/{len(self._keys)} keys still active"
                            )
                    continue   # retry with next round-robin key
                raise          # non-rate-limit error: propagate

    # ── Stats / helpers ───────────────────────────────────────────────────────

    @property
    def current_key_num(self) -> int:
        """Key number that will be used on the NEXT search (1-based)."""
        return (self._rr_idx % len(self._keys)) + 1

    @property
    def keys_remaining(self) -> int:
        return len(self._keys) - len(self._blacklisted)

    @property
    def all_exhausted(self) -> bool:
        return len(self._blacklisted) >= len(self._keys)

    def status_line(self) -> str:
        return (
            f"TavilyKeyRotator | active={self.keys_remaining}/{len(self._keys)} keys | "
            f"searches={self.searches_made} | blacklisted={len(self._blacklisted)}"
        )
