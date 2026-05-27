"""
Async HTTP client with retry logic, rate limiting, and TLS fingerprint bypass.
Uses curl_cffi for anti-bot bypass capabilities.
"""
import asyncio
import random
from typing import Any, Optional
from urllib.parse import urljoin

from curl_cffi.requests import AsyncSession
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from loguru import logger

import sys
sys.path.insert(0, str(__file__).rsplit("\\", 2)[0])
from config.settings import (
    DEFAULT_HEADERS,
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    REQUESTS_PER_SECOND,
    USER_AGENTS,
)


class AsyncHttpClient:
    """
    Async HTTP client with built-in retry logic and rate limiting.
    Uses curl_cffi for TLS fingerprint impersonation.
    """
    
    # Browser impersonation options (curl_cffi supports these)
    IMPERSONATE_OPTIONS = [
        "chrome120",
        "chrome119", 
        "chrome110",
        "edge101",
        "safari15_5",
    ]
    
    def __init__(
        self,
        base_url: str = "",
        timeout: int = DEFAULT_TIMEOUT,
        rate_limit: float = REQUESTS_PER_SECOND,
        proxy: Optional[str] = None,
        impersonate: Optional[str] = None,
    ):
        """
        Initialize the HTTP client.
        
        Args:
            base_url: Base URL for relative requests
            timeout: Request timeout in seconds
            rate_limit: Max requests per second
            proxy: Proxy URL (optional)
            impersonate: Browser to impersonate (optional)
        """
        self.base_url = base_url
        self.timeout = timeout
        self.rate_limit = rate_limit
        self.proxy = proxy
        self.impersonate = impersonate or random.choice(self.IMPERSONATE_OPTIONS)
        self._session: Optional[AsyncSession] = None
        self._last_request_time = 0
        self._lock = asyncio.Lock()
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self._ensure_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
    
    async def _ensure_session(self):
        """Ensure a session exists."""
        if self._session is None:
            self._session = AsyncSession(
                impersonate=self.impersonate,
                timeout=self.timeout,
                proxies={"http": self.proxy, "https": self.proxy} if self.proxy else None,
            )
    
    async def close(self):
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None
    
    async def _rate_limit_wait(self):
        """Wait to respect rate limiting."""
        async with self._lock:
            now = asyncio.get_event_loop().time()
            time_since_last = now - self._last_request_time
            min_interval = 1.0 / self.rate_limit
            
            if time_since_last < min_interval:
                await asyncio.sleep(min_interval - time_since_last)
            
            self._last_request_time = asyncio.get_event_loop().time()
    
    def _get_headers(self, extra_headers: Optional[dict] = None) -> dict:
        """Get request headers with a random user agent."""
        headers = DEFAULT_HEADERS.copy()
        headers["User-Agent"] = random.choice(USER_AGENTS)
        if extra_headers:
            headers.update(extra_headers)
        return headers
    
    def _build_url(self, url: str) -> str:
        """Build full URL from relative or absolute URL."""
        if url.startswith(("http://", "https://")):
            return url
        return urljoin(self.base_url, url)
    
    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    async def get(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        **kwargs,
    ) -> "Response":
        """
        Make a GET request with retry logic.
        
        Args:
            url: URL to request (can be relative if base_url is set)
            params: Query parameters
            headers: Extra headers
            **kwargs: Additional arguments for curl_cffi
            
        Returns:
            Response object
        """
        await self._ensure_session()
        await self._rate_limit_wait()
        
        full_url = self._build_url(url)
        request_headers = self._get_headers(headers)
        
        logger.debug(f"GET {full_url}")
        
        response = await self._session.get(
            full_url,
            params=params,
            headers=request_headers,
            **kwargs,
        )
        
        logger.debug(f"Response: {response.status_code}")
        return response
    
    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    async def post(
        self,
        url: str,
        data: Optional[dict] = None,
        json: Optional[dict] = None,
        headers: Optional[dict] = None,
        **kwargs,
    ) -> "Response":
        """
        Make a POST request with retry logic.
        
        Args:
            url: URL to request
            data: Form data
            json: JSON data
            headers: Extra headers
            **kwargs: Additional arguments
            
        Returns:
            Response object
        """
        await self._ensure_session()
        await self._rate_limit_wait()
        
        full_url = self._build_url(url)
        request_headers = self._get_headers(headers)
        
        logger.debug(f"POST {full_url}")
        
        response = await self._session.post(
            full_url,
            data=data,
            json=json,
            headers=request_headers,
            **kwargs,
        )
        
        logger.debug(f"Response: {response.status_code}")
        return response
    
    async def get_json(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        **kwargs,
    ) -> Any:
        """
        Make a GET request and return JSON response.
        
        Args:
            url: URL to request
            params: Query parameters
            headers: Extra headers
            
        Returns:
            Parsed JSON response
        """
        response = await self.get(url, params=params, headers=headers, **kwargs)
        return response.json()
    
    async def get_text(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        **kwargs,
    ) -> str:
        """
        Make a GET request and return text response.
        
        Args:
            url: URL to request
            params: Query parameters
            headers: Extra headers
            
        Returns:
            Text response
        """
        response = await self.get(url, params=params, headers=headers, **kwargs)
        return response.text


class Response:
    """Wrapper for curl_cffi response to provide consistent interface."""
    
    def __init__(self, response):
        self._response = response
    
    @property
    def status_code(self) -> int:
        return self._response.status_code
    
    @property
    def text(self) -> str:
        return self._response.text
    
    @property
    def content(self) -> bytes:
        return self._response.content
    
    def json(self) -> Any:
        return self._response.json()
    
    @property
    def headers(self) -> dict:
        return dict(self._response.headers)
    
    @property
    def url(self) -> str:
        return str(self._response.url)
