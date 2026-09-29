"""
utils/autosave.py — Async AutoSave manager.

Persists partial results after every N completed items so that a crash
mid-run doesn't lose all work.

Usage:
    async with AutoSaveManager(path, flush_every=10) as saver:
        await saver.add("restaurant_name", result_dict)
        # auto-flushes every 10 items; always flushes on __aexit__

    # To resume: load existing results
    existing = AutoSaveManager.load(path)
"""

from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import Any

from utils.logger import get_logger

log = get_logger(__name__)


class AutoSaveManager:
    """
    Thread-safe (via asyncio.Lock) accumulator that flushes to JSON periodically.
    """

    def __init__(self, path: Path | str, flush_every: int = 10):
        self.path        = Path(path)
        self.flush_every = flush_every
        self._data: dict[str, Any] = {}
        self._lock       = asyncio.Lock()
        self._since_save = 0

    # ── Context manager ───────────────────────────────────────────────────────
    async def __aenter__(self):
        self._data = self.load(self.path)
        log.info("AutoSave: loaded {} existing entries from {}", len(self._data), self.path)
        return self

    async def __aexit__(self, *_):
        await self._flush(force=True)

    # ── Public API ────────────────────────────────────────────────────────────
    async def add(self, key: str, value: Any) -> None:
        """Add a result and flush if flush_every threshold is reached."""
        async with self._lock:
            self._data[key] = value
            self._since_save += 1
            if self._since_save >= self.flush_every:
                await self._flush_locked()

    def contains(self, key: str) -> bool:
        """Check if key already exists (for skip logic)."""
        return key in self._data

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def all_items(self) -> dict:
        return dict(self._data)

    def count(self) -> int:
        return len(self._data)

    # ── Internal ──────────────────────────────────────────────────────────────
    async def _flush(self, force: bool = False) -> None:
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        """Must be called while holding self._lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(self._data, ensure_ascii=False, indent=2)
        # Try atomic rename first (safe on Linux, sometimes fails on Windows)
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(self.path)
        except PermissionError:
            # Fallback: direct write (not atomic, but safe enough for autosave)
            self.path.write_text(content, encoding="utf-8")
        self._since_save = 0
        log.debug("AutoSave: flushed {} entries -> {}", len(self._data), self.path)

    @staticmethod
    def load(path: Path | str) -> dict:
        """Load existing JSON (returns {} if file absent/corrupt)."""
        p = Path(path)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("AutoSave: could not parse {}: {}", p, e)
        return {}
