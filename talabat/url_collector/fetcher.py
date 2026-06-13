import asyncio
import random
import time
import uuid
from typing import Optional

from curl_cffi.requests import AsyncSession
from loguru import logger

from .config import CollectorConfig, ProxyConfig


class TalabatFetcher:
    """
    Async HTTP fetcher using curl_cffi Chrome TLS impersonation.
    Manages Oxylabs proxy session rotation and per-request rate limiting.
    """

    BASE_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }

    def __init__(self, cfg: CollectorConfig, proxy: ProxyConfig):
        self.cfg = cfg
        self.proxy = proxy
        self._request_count = 0
        self._session_id: str = self._new_session_id()
        self._session: Optional[AsyncSession] = None

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _new_session_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def _maybe_rotate_session(self):
        if self._request_count > 0 and self._request_count % self.cfg.rotate_session_every == 0:
            self._session_id = self._new_session_id()
            logger.debug(f"Rotated proxy session → {self._session_id}")

    def _build_proxies(self) -> Optional[dict]:
        if not self.proxy.enabled:
            return None
        url = self.proxy.get_url(self._session_id)
        return {"http": url, "https": url}

    async def _get_session(self) -> AsyncSession:
        if self._session is None:
            self._session = AsyncSession(impersonate=self.cfg.impersonate)
        return self._session

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Core fetch with retry + backoff
    # ------------------------------------------------------------------

    async def fetch(self, url: str, referer: Optional[str] = None) -> Optional[str]:
        """
        Fetch a URL, returning HTML text.
        Returns None after max_retries exhausted.
        """
        headers = dict(self.BASE_HEADERS)
        if referer:
            headers["Referer"] = referer
            headers["Sec-Fetch-Site"] = "same-origin"

        proxies = self._build_proxies()
        self._maybe_rotate_session()

        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                await self._random_delay()
                session = await self._get_session()

                kwargs = {
                    "headers": headers,
                    "timeout": self.cfg.timeout,
                    "allow_redirects": True,
                }
                if proxies:
                    kwargs["proxies"] = proxies

                resp = await session.get(url, **kwargs)
                self._request_count += 1

                if resp.status_code == 200:
                    logger.debug(f"[{resp.status_code}] {url}")
                    return resp.text
                elif resp.status_code == 429:
                    wait = self.cfg.retry_backoff ** attempt * 10
                    logger.warning(f"Rate limited on {url}. Waiting {wait:.0f}s …")
                    await asyncio.sleep(wait)
                elif resp.status_code in (403, 503):
                    logger.warning(f"[{resp.status_code}] Blocked on attempt {attempt}: {url}")
                    # Rotate session on block
                    self._session_id = self._new_session_id()
                    await asyncio.sleep(self.cfg.retry_backoff ** attempt * 5)
                else:
                    logger.warning(f"[{resp.status_code}] Unexpected on attempt {attempt}: {url}")
                    await asyncio.sleep(self.cfg.retry_backoff ** attempt)

            except Exception as exc:
                wait = self.cfg.retry_backoff ** attempt
                logger.warning(f"Attempt {attempt} failed for {url}: {exc}. Retrying in {wait:.1f}s …")
                await asyncio.sleep(wait)
                # Re-create session on connection error
                await self.close()

        logger.error(f"Exhausted retries for {url}")
        return None

    async def _random_delay(self):
        delay = random.uniform(self.cfg.min_delay, self.cfg.max_delay)
        await asyncio.sleep(delay)
