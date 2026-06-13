import json
import math
import re
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup
from loguru import logger


@dataclass
class VendorEntry:
    """Rich vendor data from __NEXT_DATA__ — one physical branch."""
    url: str                       # clean URL, no ?aid
    branch_id: int                 # branchId — unique per physical location (used in URL)
    restaurant_id: int             # restaurantId/id — parent chain (shared across branches)
    branch_slug: str
    name: str
    lat: Optional[float]
    lon: Optional[float]


@dataclass
class ListingPageResult:
    restaurant_urls: list[str]      # kept for CSS-fallback compat
    vendors: list[VendorEntry]      # rich data (populated when __NEXT_DATA__ works)
    total_restaurants: Optional[int]
    page_size: Optional[int]
    current_page: int
    total_pages: Optional[int]


def parse_listing_page(html: str, page_num: int = 1) -> ListingPageResult:
    """
    Parse a Talabat restaurant listing page.
    Tries __NEXT_DATA__ JSON first; falls back to CSS link extraction.
    """
    soup = BeautifulSoup(html, "lxml")

    # --- Attempt 1: __NEXT_DATA__ (richest source) ---
    result = _parse_next_data(soup, page_num)
    if result and result.restaurant_urls:
        return result

    # --- Attempt 2: CSS selector fallback ---
    logger.warning(f"__NEXT_DATA__ parse failed on page {page_num}, falling back to CSS")
    return _parse_css_links(soup, page_num)


# ------------------------------------------------------------------
# __NEXT_DATA__ extraction
# ------------------------------------------------------------------

def _parse_next_data(soup: BeautifulSoup, page_num: int) -> Optional[ListingPageResult]:
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script or not script.string:
        return None

    try:
        data = json.loads(script.string)
    except json.JSONDecodeError as exc:
        logger.warning(f"__NEXT_DATA__ JSON parse error: {exc}")
        return None

    page_props = data.get("props", {}).get("pageProps", {})
    if not page_props:
        return None

    # Collect restaurant entries from known key patterns
    # Talabat UAE actual structure: pageProps.data.vendors
    restaurants = (
        _deep_get(page_props, "data", "vendors")           # real Talabat UAE key
        or _deep_get(page_props, "initialListingState", "restaurants")
        or _deep_get(page_props, "restaurantsState", "restaurants")
        or page_props.get("restaurants")
        or _deep_get(page_props, "pageData", "restaurants")
        or []
    )

    # Build VendorEntry list — deduplicate by branch_id (preferred) or URL (fallback)
    seen_bids: set[int] = set()
    seen_urls: set[str] = set()
    vendor_entries: list[VendorEntry] = []
    urls: list[str] = []

    for r in restaurants:
        url = _build_restaurant_url(r)
        if not url:
            continue
        bid = r.get("branchId") or 0
        if bid:
            if bid in seen_bids:
                continue
            seen_bids.add(bid)
        else:
            # No branch_id available — dedup by URL
            if url in seen_urls:
                continue
            seen_urls.add(url)
        vendor_entries.append(VendorEntry(
            url=url,
            branch_id=bid,
            restaurant_id=r.get("restaurantId") or r.get("id") or 0,
            branch_slug=r.get("branchSlug") or "",
            name=r.get("name") or "",
            lat=r.get("latitude"),
            lon=r.get("longitude"),
        ))
        urls.append(url)

    # Fallback: scan raw JSON string for /restaurant/ hrefs (no rich metadata)
    if not urls:
        raw = script.string
        found = re.findall(r'"(/uae/restaurant/\d+/[^"]+)"', raw)
        urls = list(dict.fromkeys(f"https://www.talabat.com{u.split('?')[0]}" for u in found))

    # Pagination info
    total_restaurants = _find_total_count(page_props)
    page_size = _find_page_size(page_props, restaurants)
    total_pages = (
        math.ceil(total_restaurants / page_size)
        if total_restaurants and page_size
        else None
    )

    logger.debug(
        f"__NEXT_DATA__ page {page_num}: {len(urls)} URLs, "
        f"total={total_restaurants}, pages={total_pages}"
    )
    return ListingPageResult(
        restaurant_urls=urls,
        vendors=vendor_entries,
        total_restaurants=total_restaurants,
        page_size=page_size,
        current_page=page_num,
        total_pages=total_pages,
    )


def _build_restaurant_url(entry: dict) -> Optional[str]:
    """Build a full Talabat restaurant URL from a vendor/restaurant data entry."""

    def _clean(u: str) -> str:
        return u.split("?")[0]

    def _abs(u: str) -> str:
        return u if u.startswith("http") else f"https://www.talabat.com{u}"

    # Method 1: branchUrl (actual Talabat vendor entry field)
    for key in ("branchUrl", "menuUrl"):
        val = entry.get(key)
        if val and "/restaurant/" in str(val):
            return _abs(_clean(val))

    # Method 2: branchId + branchSlug (Talabat UAE vendor structure)
    bid = entry.get("branchId")
    bslug = entry.get("branchSlug")
    if bid and bslug:
        return f"https://www.talabat.com/uae/restaurant/{bid}/{bslug}"

    # Method 3: id/restaurantId + urlSlug/restaurantSlug (legacy / other formats)
    rid = entry.get("id") or entry.get("restaurantId")
    slug = (
        entry.get("urlSlug")
        or entry.get("restaurantSlug")
        or entry.get("branchSlug")
        or entry.get("slug")
        or entry.get("nameSlug")
    )
    if rid and slug:
        return f"https://www.talabat.com/uae/restaurant/{rid}/{slug}"

    # Method 4: any path/url field containing /restaurant/
    for key in ("path", "url", "link"):
        val = entry.get(key)
        if val and "/restaurant/" in str(val):
            return _abs(_clean(val))

    return None


def _find_total_count(page_props: dict) -> Optional[int]:
    candidates = [
        _deep_get(page_props, "data", "totalVendors"),          # Talabat UAE actual key
        _deep_get(page_props, "initialListingState", "pagination", "total"),
        _deep_get(page_props, "initialListingState", "totalCount"),
        _deep_get(page_props, "restaurantsState", "totalCount"),
        page_props.get("totalCount"),
        page_props.get("totalRestaurants"),
        _deep_get(page_props, "pageData", "totalCount"),
    ]
    for c in candidates:
        if isinstance(c, int) and c > 0:
            return c
    return None


def _find_page_size(page_props: dict, restaurants: list) -> Optional[int]:
    candidates = [
        _deep_get(page_props, "data", "size"),                   # Talabat UAE actual key
        _deep_get(page_props, "initialListingState", "pagination", "pageSize"),
        _deep_get(page_props, "initialListingState", "pageSize"),
        page_props.get("pageSize"),
    ]
    for c in candidates:
        if isinstance(c, int) and c > 0:
            return c
    # Infer from actual results count
    if restaurants:
        return len(restaurants)
    return None


# ------------------------------------------------------------------
# CSS fallback
# ------------------------------------------------------------------

def _parse_css_links(soup: BeautifulSoup, page_num: int) -> ListingPageResult:
    links = soup.select('a[href*="/restaurant/"]')
    urls: list[str] = []
    seen: set[str] = set()
    for a in links:
        href = a.get("href", "")
        if not re.search(r"/restaurant/\d+/", href):
            continue
        clean = href.split("?")[0]
        if not clean.startswith("http"):
            clean = f"https://www.talabat.com{clean}"
        if clean not in seen:
            seen.add(clean)
            urls.append(clean)

    logger.debug(f"CSS fallback page {page_num}: {len(urls)} URLs (no rich vendor metadata)")
    return ListingPageResult(
        restaurant_urls=urls,
        vendors=[],   # no rich metadata available via CSS fallback
        total_restaurants=None,
        page_size=len(urls) if urls else None,
        current_page=page_num,
        total_pages=None,
    )


# ------------------------------------------------------------------
# Pagination max-page extraction (for Scrapy spider use)
# ------------------------------------------------------------------

def extract_max_page(html: str) -> Optional[int]:
    """
    Extract the last page number from listing page HTML.
    Collects candidates from ALL available sources and returns the maximum,
    so a high pagination-link value always wins over a low __NEXT_DATA__ estimate.
    Returns None if no page information found at all.
    """
    soup = BeautifulSoup(html, "lxml")
    candidates: list[int] = []

    # Source 1: __NEXT_DATA__ total count / page size
    nd = _parse_next_data(soup, page_num=1)
    if nd and nd.total_pages and nd.total_pages > 0:
        candidates.append(nd.total_pages)

    # Source 2: max page= value across all <a href="...?page=N"> links
    for a in soup.select('a[href*="page="]'):
        m = re.search(r"[?&]page=(\d+)", a.get("href", ""))
        if m:
            candidates.append(int(m.group(1)))

    # Source 3: bare numeric text inside pagination containers
    for selector in [
        '[class*="pagination"] a',
        '[data-testid*="pagination"] a',
        'nav a',
    ]:
        for el in soup.select(selector):
            txt = el.get_text(strip=True)
            if txt.isdigit():
                candidates.append(int(txt))

    return max(candidates) if candidates else None


# ------------------------------------------------------------------
# Area discovery from UAE home page
# ------------------------------------------------------------------

def discover_areas(html: str) -> list[dict]:
    """
    Parse the Talabat UAE home/listing page to discover area IDs and slugs.
    Returns list of {area_id, slug, name, url}.
    """
    soup = BeautifulSoup(html, "lxml")
    areas: list[dict] = []

    # Try __NEXT_DATA__ first
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if script and script.string:
        try:
            data = json.loads(script.string)
            areas_raw = (
                _deep_get(data, "props", "pageProps", "areas")
                or _deep_get(data, "props", "pageProps", "initialData", "areas")
                or _deep_get(data, "props", "pageProps", "pageData", "areas")
                or []
            )
            for a in areas_raw:
                area_id = a.get("id") or a.get("areaId")
                slug = a.get("slug") or a.get("nameSlug") or a.get("urlSlug")
                name = a.get("name") or a.get("nameEn")
                if area_id and slug:
                    areas.append({
                        "area_id": area_id,
                        "slug": slug,
                        "name": name or slug,
                        "url": f"https://www.talabat.com/uae/restaurants/{area_id}/{slug}",
                    })
        except Exception as exc:
            logger.warning(f"Area discovery from __NEXT_DATA__ failed: {exc}")

    # CSS fallback: find all /uae/restaurants/{id}/{slug} links
    if not areas:
        links = soup.select('a[href*="/uae/restaurants/"]')
        seen: set[str] = set()
        for a in links:
            href = a.get("href", "")
            m = re.search(r"/uae/restaurants/(\d+)/([a-z0-9-]+)", href)
            if m:
                area_id, slug = int(m.group(1)), m.group(2)
                key = f"{area_id}-{slug}"
                if key not in seen:
                    seen.add(key)
                    areas.append({
                        "area_id": area_id,
                        "slug": slug,
                        "name": slug.replace("-", " ").title(),
                        "url": f"https://www.talabat.com/uae/restaurants/{area_id}/{slug}",
                    })

    logger.info(f"Discovered {len(areas)} UAE areas")
    return areas


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _deep_get(d: dict, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d
