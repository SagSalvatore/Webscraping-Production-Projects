import asyncio
import json
import math
import os
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from tqdm.asyncio import tqdm

from .config import CollectorConfig, ProxyConfig
from .fetcher import TalabatFetcher
from .parser import ListingPageResult, discover_areas, parse_listing_page


class UrlCollector:
    """
    Orchestrates async collection of all Talabat UAE restaurant URLs.

    Strategy:
    1. Fetch page 1 of main UAE listing to discover total pages.
    2. If area discovery is enabled, also fetch the home page to get all area URLs.
    3. Run concurrent page fetches (semaphore-controlled) across all listing URLs.
    4. Checkpoint progress every N pages; resume from checkpoint on restart.
    5. Deduplicate and save final JSON.
    """

    def __init__(self, cfg: CollectorConfig, proxy: ProxyConfig):
        self.cfg = cfg
        self.proxy = proxy
        self.fetcher = TalabatFetcher(cfg, proxy)

        self._output_dir = os.path.abspath(cfg.output_dir)
        self._checkpoint_path = os.path.join(self._output_dir, cfg.checkpoint_file)
        self._output_path = os.path.join(self._output_dir, cfg.output_file)

        # State
        self._all_urls: set[str] = set()
        self._completed_pages: set[str] = set()  # "listing_url::page_num"
        self._failed_pages: list[dict] = []
        self._areas: list[dict] = []
        self._lock = asyncio.Lock()

        os.makedirs(self._output_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, discover_areas_flag: bool = True) -> dict:
        logger.info("=" * 60)
        logger.info("Talabat UAE URL Collector — starting")
        logger.info(f"Proxy: {'ENABLED (' + self.proxy.country.upper() + ')' if self.proxy.enabled else 'DISABLED'}")
        logger.info(f"Concurrency: {self.cfg.max_concurrent}")
        logger.info("=" * 60)

        self._load_checkpoint()

        # Step 1 — probe page 1 of main listing to get total page count
        main_base = self.cfg.base_listing_url
        probe_result = await self._probe_listing(main_base)

        listing_sources: list[tuple[str, int]] = []  # (base_url, total_pages)

        if probe_result and probe_result.total_pages:
            listing_sources.append((main_base, probe_result.total_pages))
            logger.info(f"Main listing: ~{probe_result.total_restaurants} restaurants, {probe_result.total_pages} pages")
        else:
            # Unknown total pages — paginate until empty
            listing_sources.append((main_base, 9999))
            logger.info("Total page count unknown — will paginate until empty")

        # Step 2 — optional area discovery for extra coverage
        if discover_areas_flag:
            await self._discover_and_add_areas(listing_sources)

        # Step 3 — collect all pages concurrently
        await self._collect_all(listing_sources)

        # Step 4 — save final output
        result = self._save_final()
        await self.fetcher.close()
        return result

    # ------------------------------------------------------------------
    # Probing & area discovery
    # ------------------------------------------------------------------

    async def _probe_listing(self, base_url: str) -> Optional[ListingPageResult]:
        """Fetch page 1 to determine total pages."""
        page_key = f"{base_url}::1"
        html = await self.fetcher.fetch(f"{base_url}?page=1")
        if not html:
            return None
        result = parse_listing_page(html, page_num=1)

        # Register URLs from this probe page
        async with self._lock:
            for url in result.restaurant_urls:
                self._all_urls.add(url)
            self._completed_pages.add(page_key)

        return result

    async def _discover_and_add_areas(self, listing_sources: list):
        """Fetch UAE home page, discover area URLs, add them as extra listing sources."""
        logger.info("Discovering UAE areas …")
        html = await self.fetcher.fetch(self.cfg.base_listing_url)
        if not html:
            logger.warning("Area discovery skipped — could not fetch listing page")
            return

        self._areas = discover_areas(html)
        if not self._areas:
            logger.info("No extra areas discovered (will rely on main listing)")
            return

        # Known areas already in the listing_sources base URL should be skipped
        existing_bases = {src[0] for src in listing_sources}
        added = 0
        for area in self._areas:
            area_url = area["url"]
            if area_url not in existing_bases:
                # Probe page 1 to get total pages for this area
                html_area = await self.fetcher.fetch(f"{area_url}?page=1")
                if html_area:
                    probe = parse_listing_page(html_area, page_num=1)
                    total = probe.total_pages or 50  # conservative default
                    listing_sources.append((area_url, total))
                    async with self._lock:
                        for url in probe.restaurant_urls:
                            self._all_urls.add(url)
                        self._completed_pages.add(f"{area_url}::1")
                    added += 1
                    logger.debug(f"Area added: {area['name']} ({total} pages)")

        logger.info(f"Added {added} area listing sources")

    # ------------------------------------------------------------------
    # Concurrent page collection
    # ------------------------------------------------------------------

    async def _collect_all(self, listing_sources: list[tuple[str, int]]):
        """Build task list and run all page fetches concurrently."""
        tasks = []
        for base_url, total_pages in listing_sources:
            for page_num in range(2, total_pages + 1):  # page 1 already probed
                page_key = f"{base_url}::{page_num}"
                if page_key in self._completed_pages:
                    continue
                tasks.append((base_url, page_num))

        if not tasks:
            logger.info("All pages already completed (checkpoint)")
            return

        logger.info(f"Collecting {len(tasks)} pages across {len(listing_sources)} listing sources")

        sem = asyncio.Semaphore(self.cfg.max_concurrent)
        progress = tqdm(total=len(tasks), desc="Pages", unit="pg")

        async def bounded_fetch(base_url: str, page_num: int):
            async with sem:
                await self._fetch_page(base_url, page_num)
                progress.update(1)

        await asyncio.gather(*[bounded_fetch(b, p) for b, p in tasks])
        progress.close()

    async def _fetch_page(self, base_url: str, page_num: int):
        page_key = f"{base_url}::{page_num}"
        url = f"{base_url}?page={page_num}"
        referer = f"{base_url}?page={page_num - 1}"

        html = await self.fetcher.fetch(url, referer=referer)

        if html is None:
            async with self._lock:
                self._failed_pages.append({
                    "base_url": base_url,
                    "page": page_num,
                    "timestamp": _now_iso(),
                })
            return

        result = parse_listing_page(html, page_num=page_num)

        if not result.restaurant_urls:
            # Empty page signals end of listing for this base URL
            logger.debug(f"Empty page {page_num} for {base_url} — end of listing")
            return

        async with self._lock:
            before = len(self._all_urls)
            for u in result.restaurant_urls:
                self._all_urls.add(u)
            new_count = len(self._all_urls) - before
            self._completed_pages.add(page_key)

            if len(self._completed_pages) % self.cfg.checkpoint_interval == 0:
                self._save_checkpoint()

        logger.debug(
            f"Page {page_num}: +{new_count} new URLs "
            f"(total unique: {len(self._all_urls)})"
        )

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    def _save_checkpoint(self):
        data = {
            "saved_at": _now_iso(),
            "total_unique_urls": len(self._all_urls),
            "completed_pages": list(self._completed_pages),
            "failed_pages": self._failed_pages,
            "urls": list(self._all_urls),
        }
        _write_json(self._checkpoint_path, data)
        logger.debug(f"Checkpoint saved → {self._checkpoint_path}")

    def _load_checkpoint(self):
        if not os.path.exists(self._checkpoint_path):
            return
        try:
            data = _read_json(self._checkpoint_path)
            self._all_urls = set(data.get("urls", []))
            self._completed_pages = set(data.get("completed_pages", []))
            self._failed_pages = data.get("failed_pages", [])
            logger.info(
                f"Resumed from checkpoint: {len(self._all_urls)} URLs, "
                f"{len(self._completed_pages)} completed pages"
            )
        except Exception as exc:
            logger.warning(f"Could not load checkpoint: {exc}")

    # ------------------------------------------------------------------
    # Final output
    # ------------------------------------------------------------------

    def _save_final(self) -> dict:
        urls_list = sorted(self._all_urls)
        summary = {
            "platform": "talabat",
            "country": "uae",
            "scraped_at": _now_iso(),
            "total_unique_urls": len(urls_list),
            "total_pages_completed": len(self._completed_pages),
            "total_pages_failed": len(self._failed_pages),
            "areas_discovered": len(self._areas),
            "failed_pages": self._failed_pages,
            "urls": urls_list,
        }
        _write_json(self._output_path, summary)
        # Remove checkpoint after successful completion
        if os.path.exists(self._checkpoint_path):
            os.remove(self._checkpoint_path)

        logger.success(f"Done! {len(urls_list)} unique restaurant URLs → {self._output_path}")
        logger.info(f"Failed pages: {len(self._failed_pages)}")
        return summary


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
