"""
TalabatUaeListingSpider
========================
Reads CITY-URLS.csv (648 UAE area URLs), paginates through every listing
page of every area, and yields RestaurantBranchItem for each unique branch.

Pagination strategy (hybrid):
  - Known total pages (from __NEXT_DATA__ totalVendors/size):
      Blast ALL page requests at once in parallel.  Fast.
  - Unknown total pages (parse failed):
      Chain page by page — each page yields the next one.
      Stops automatically when a page returns zero results.
      Never misses pages regardless of how many there are.

Run from talabat/listing_scraper/:

    Full run:
        scrapy crawl talabat_uae_listing

    Full run with resume support:
        scrapy crawl talabat_uae_listing -s JOBDIR=../../.scrapy/jobs/run1

    Smoke test (first N areas only):
        scrapy crawl talabat_uae_listing -a area_limit=3
"""

import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import scrapy

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from url_collector.parser import extract_max_page, parse_listing_page
from listing_scraper.items import RestaurantBranchItem

_CSV_PATH = _ROOT / "CITY-URLS.csv"

# Hard cap — no single area should ever have more pages than this.
# DWTC has ~467 pages at 15 per page. 2000 is a safe ceiling.
_MAX_PAGES_CAP = 2000


class TalabatUaeListingSpider(scrapy.Spider):
    name = "talabat_uae_listing"
    allowed_domains = ["www.talabat.com"]

    def __init__(self, area_limit: int = 0, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._area_limit = int(area_limit)
        self._areas = self._load_areas()

    # ------------------------------------------------------------------
    # CSV loading
    # ------------------------------------------------------------------

    def _load_areas(self) -> list[dict]:
        areas = []
        with open(_CSV_PATH, newline="", encoding="utf-8-sig") as f:
            for row in csv.reader(f):
                if len(row) < 2:
                    continue
                name = row[0].strip()
                url = row[1].strip()
                area_id = self._extract_area_id(url)
                areas.append({"name": name, "url": url, "id": area_id})
        if self._area_limit > 0:
            areas = areas[: self._area_limit]
        self.logger.info(f"Loaded {len(areas)} areas from CSV")
        return areas

    @staticmethod
    def _extract_area_id(url: str) -> int:
        m = re.search(r"/restaurants/(\d+)/", url)
        return int(m.group(1)) if m else 0

    # ------------------------------------------------------------------
    # Entry point — one page-1 request per area
    # ------------------------------------------------------------------

    def start_requests(self):
        for area in self._areas:
            yield scrapy.Request(
                url=f"{area['url']}?page=1",
                callback=self.parse_listing,
                errback=self.handle_error,
                meta={
                    "area": area,
                    "page": 1,
                    "referer": "https://www.talabat.com/uae/restaurants",
                },
                priority=10,  # page-1 requests get priority to detect totals early
            )

    # ------------------------------------------------------------------
    # Listing page parser
    # ------------------------------------------------------------------

    def parse_listing(self, response):
        area = response.meta["area"]
        page = response.meta["page"]
        sequential = response.meta.get("sequential", False)

        if response.status != 200:
            self.logger.warning(f"[{response.status}] {response.url} — skipping")
            return

        result = parse_listing_page(response.text, page_num=page)

        # Empty page = end of listing for this area (or bot block on page 1)
        if not result.vendors and not result.restaurant_urls:
            if page == 1:
                self.logger.warning(
                    f"'{area['name']}' page 1 returned ZERO results — "
                    "possible block. Will be retried by Scrapy retry middleware."
                )
            else:
                self.logger.info(
                    f"'{area['name']}' ended at page {page} (empty response)"
                )
            return

        # --- Yield items ---
        now = datetime.now(timezone.utc).isoformat()
        if result.vendors:
            for v in result.vendors:
                yield RestaurantBranchItem(
                    url=self._ensure_aid(v.url, area["id"]),
                    branch_id=v.branch_id,
                    restaurant_id=v.restaurant_id,
                    branch_slug=v.branch_slug,
                    name=v.name,
                    lat=v.lat,
                    lon=v.lon,
                    area_name=area["name"],
                    area_id=area["id"],
                    page_found=page,
                    scraped_at=now,
                )
        else:
            # CSS fallback — URL only, no rich metadata
            for url in result.restaurant_urls:
                yield RestaurantBranchItem(
                    url=self._ensure_aid(url, area["id"]),
                    branch_id=self._extract_branch_id(url),
                    restaurant_id=None,
                    branch_slug=None,
                    name=None,
                    lat=None,
                    lon=None,
                    area_name=area["name"],
                    area_id=area["id"],
                    page_found=page,
                    scraped_at=now,
                )

        count = len(result.vendors) or len(result.restaurant_urls)
        self.logger.debug(f"'{area['name']}' page {page}: {count} branches")

        # ------------------------------------------------------------------
        # Pagination
        # ------------------------------------------------------------------
        if page == 1:
            total_pages = result.total_pages or extract_max_page(response.text)

            if total_pages:
                # KNOWN total → blast all remaining pages in parallel
                total_pages = min(total_pages, _MAX_PAGES_CAP)
                self.logger.info(
                    f"'{area['name']}': {total_pages} pages detected — "
                    f"queuing {total_pages - 1} parallel requests"
                )
                for next_pg in range(2, total_pages + 1):
                    yield self._page_request(area, next_pg, referer=response.url)

            else:
                # UNKNOWN total → chain one page at a time until empty
                self.logger.info(
                    f"'{area['name']}': total pages unknown — switching to sequential mode"
                )
                yield self._page_request(area, 2, referer=response.url, sequential=True)

        elif sequential:
            # Sequential chaining: this page had results, so fetch the next one.
            # The chain stops when a page returns zero results (handled above).
            yield self._page_request(area, page + 1, referer=response.url, sequential=True)

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def handle_error(self, failure):
        self.logger.error(
            f"Request failed: {failure.request.url} — {failure.value}"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _page_request(
        self,
        area: dict,
        page: int,
        referer: str = "",
        sequential: bool = False,
    ) -> scrapy.Request:
        return scrapy.Request(
            url=f"{area['url']}?page={page}",
            callback=self.parse_listing,
            errback=self.handle_error,
            meta={
                "area": area,
                "page": page,
                "referer": referer,
                "sequential": sequential,
            },
            priority=5,
            dont_filter=False,
        )

    @staticmethod
    def _extract_branch_id(url: str) -> int:
        m = re.search(r"/restaurant/(\d+)/", url)
        return int(m.group(1)) if m else 0

    @staticmethod
    def _ensure_aid(url: str, area_id: int) -> str:
        if not area_id:
            return url
        return f"{url.split('?')[0]}?aid={area_id}"
