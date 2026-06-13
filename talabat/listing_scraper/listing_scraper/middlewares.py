import os
import random
import uuid

from dotenv import load_dotenv
from scrapy import signals
from scrapy.downloadermiddlewares.retry import RetryMiddleware
from scrapy.utils.response import response_status_message

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

_STEALTH_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


class OxylabsProxyMiddleware:
    """
    Injects Oxylabs residential proxy into every request.
    Rotates the session ID every ROTATE_EVERY requests to avoid IP/session bans.
    """

    ROTATE_EVERY = 20

    def __init__(self, username: str, password: str, country: str):
        self._username = username
        self._password = password
        self._country = country
        self._req_count = 0
        self._session_id = self._new_sid()

    @classmethod
    def from_crawler(cls, crawler):
        username = (
            crawler.settings.get("OXYLABS_USERNAME")
            or os.getenv("OXYLABS_USERNAME", "")
        )
        password = (
            crawler.settings.get("OXYLABS_PASSWORD")
            or os.getenv("OXYLABS_PASSWORD", "")
        )
        country = (
            crawler.settings.get("OXYLABS_COUNTRY")
            or os.getenv("OXYLABS_COUNTRY", "ae")
        )
        if not username or not password:
            crawler.spider_cls if hasattr(crawler, "spider_cls") else None
            import logging
            logging.getLogger("OxylabsProxyMiddleware").warning(
                "Oxylabs credentials not set — running WITHOUT proxy"
            )
        return cls(username, password, country)

    def _new_sid(self) -> str:
        return uuid.uuid4().hex[:12]

    def _proxy_url(self) -> str:
        return (
            f"https://customer-{self._username}-cc-{self._country}"
            f"-sessid-{self._session_id}:{self._password}@pr.oxylabs.io:7777"
        )

    def process_request(self, request, spider):
        if not self._username or not self._password:
            return  # no-op if no credentials

        self._req_count += 1
        if self._req_count % self.ROTATE_EVERY == 0:
            self._session_id = self._new_sid()
            spider.logger.debug(f"Proxy session rotated → {self._session_id}")

        request.meta["proxy"] = self._proxy_url()

    def process_response(self, request, response, spider):
        if response.status in (403, 407, 503):
            # Hard block — rotate session immediately and retry
            self._session_id = self._new_sid()
            spider.logger.warning(
                f"[{response.status}] Proxy block detected, session rotated → {self._session_id}"
            )
        return response


class StealthHeadersMiddleware:
    """Adds browser-like headers and a random User-Agent to every request."""

    STATIC_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }

    def process_request(self, request, spider):
        ua = random.choice(_STEALTH_UAS)
        request.headers["User-Agent"] = ua
        request.headers["sec-ch-ua"] = (
            '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'
        )
        for k, v in self.STATIC_HEADERS.items():
            request.headers.setdefault(k, v)

        # Referer: previous listing page (same domain)
        if "referer" not in request.meta:
            request.headers.setdefault(
                "Referer", "https://www.talabat.com/uae/restaurants"
            )
        else:
            request.headers["Referer"] = request.meta["referer"]
            request.headers["Sec-Fetch-Site"] = "same-origin"
