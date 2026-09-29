"""
platform_scrape.py — two jobs, deliberately split into two phases:

  Phase 1 (called inline from pipeline.py, cheap, no network calls):
    find_platform_urls() scans Tavily results for known food-delivery
    listing platforms (Zomato, Deliveroo, noon Food) and, for Zomato,
    validates the URL's city-slug prefix is a real UAE emirate (Zomato is
    global -- zomato.com/bangalore/... must not be confused with
    zomato.com/dubai/...). This is a pure string check, no HTTP request,
    so it can't itself trigger rate limits and costs nothing to run on
    every single name.

  Phase 2 (run separately afterwards via scrape_platform_urls.py, once
    all Tavily searches for a batch are done): fetch each collected URL
    and pull its schema.org Restaurant JSON-LD block -- DETERMINISTIC
    structured data (address, phone, geo, rating) straight from the
    platform's own SEO markup, far more reliable than asking an LLM to
    eyeball a truncated search snippet. No JS rendering needed (the
    JSON-LD is present in the raw server-rendered HTML) -- confirmed
    working 2026-07-04 against a real Zomato UAE listing.

Keeping these separate means the main search+extract loop never blocks
on (or gets 429'd by) a platform's own rate limiting, and phase 2 can pace
requests per-platform instead of interleaved with everything else.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
import config  # noqa: E402
from validation import name_supported_by_results  # noqa: E402

PLATFORM_DOMAINS = {
    "zomato.com": "zomato",
    "deliveroo.ae": "deliveroo",
    "deliveroo.com": "deliveroo",
    "food.noon.com": "noon_food",
}

# Zomato is global (India, UK, etc.) -- only these city-slugs are UAE.
# Checked directly on the URL path, before any HTTP request is made, so
# noise (zomato.com/bangalore/..., zomato.com/mumbai/...) never even
# reaches the phase-2 scraper.
ZOMATO_UAE_CITY_SLUGS = {
    "dubai", "abudhabi", "sharjah", "ajman", "fujairah",
    "ras-al-khaimah", "umm-al-quwain", "al-ain",
}
_ZOMATO_CITY_RE = re.compile(r"zomato\.com/([a-z-]+)/", re.IGNORECASE)

_LDJSON_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def is_uae_zomato_url(url: str) -> bool:
    """True only if the city-slug right after zomato.com/ is a real UAE
    emirate/city. Cheap string check -- no network call."""
    m = _ZOMATO_CITY_RE.search(url.lower())
    return bool(m and m.group(1) in ZOMATO_UAE_CITY_SLUGS)


def find_platform_urls(search_payload: dict) -> list[dict]:
    """Scan Tavily results for URLs on a known platform domain. Returns
    one entry per platform (first URL seen), so we don't hit the same
    site 3x for menu/reviews/home variants of the same listing.

    Zomato URLs are filtered to UAE-city-slugs only, right here, before
    they're ever collected -- a same-named business in another Zomato
    market shouldn't even make it into the candidate list. Deliveroo/noon
    URLs are collected as-is (per user instruction) for phase-2 scraping.
    """
    found: dict[str, str] = {}
    for r in search_payload.get("results", []):
        url = r.get("url") or ""
        for domain, platform in PLATFORM_DOMAINS.items():
            if domain not in url or platform in found:
                continue
            if platform == "zomato" and not is_uae_zomato_url(url):
                continue
            found[platform] = url
    return [{"platform": p, "url": u} for p, u in found.items()]


def _flatten_address(addr: dict | str | None) -> str | None:
    if not addr:
        return None
    if isinstance(addr, str):
        return addr.strip() or None
    parts = [
        addr.get("streetAddress"),
        addr.get("addressLocality"),
        addr.get("addressRegion"),
        addr.get("addressCountry"),
    ]
    ordered: list[str] = []
    for p in parts:
        if not p:
            continue
        # Skip if this part is already contained in what we've built so far
        # (streetAddress often already ends with the same locality text that
        # addressLocality repeats verbatim, e.g. "...Jumeirah Lake Towers,
        # Dubai" then addressLocality="Jumeirah Lake Towers, Dubai" again).
        already_covered = any(p in existing or existing in p for existing in ordered)
        if not already_covered:
            ordered.append(p)
    return ", ".join(ordered) if ordered else None


# Rough UAE bounding box (matches the emirate_from_latlon logic used
# elsewhere in this project for the Apify pipeline).
_UAE_LAT_RANGE = (22.0, 26.5)
_UAE_LON_RANGE = (51.0, 56.5)


def _looks_like_uae(ld: dict) -> bool:
    """Zomato/Deliveroo/noon operate outside the UAE too (Zomato is in
    India, Deliveroo in the UK, etc.) -- a same-named business elsewhere
    can otherwise slip past the name-token check. Verify country/geo
    before trusting the listing."""
    addr = ld.get("address")
    if isinstance(addr, dict):
        country = (addr.get("addressCountry") or "").strip().lower()
        if country:
            return country in ("uae", "united arab emirates", "ae")

    geo = ld.get("geo") or {}
    try:
        lat = float(geo.get("latitude"))
        lon = float(geo.get("longitude"))
    except (TypeError, ValueError):
        return False
    return _UAE_LAT_RANGE[0] <= lat <= _UAE_LAT_RANGE[1] and _UAE_LON_RANGE[0] <= lon <= _UAE_LON_RANGE[1]


def _extract_restaurant_ldjson(html: str) -> dict | None:
    for block in _LDJSON_RE.findall(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for c in candidates:
            if isinstance(c, dict) and c.get("@type") in ("Restaurant", "FoodEstablishment", "LocalBusiness"):
                return c
    return None


class _RetryableStatus(Exception):
    pass


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_random_exponential(multiplier=2, max=30),
    retry=retry_if_exception_type(_RetryableStatus),
)
async def fetch_platform_listing(url: str, client: httpx.AsyncClient) -> dict | None:
    """Fetch a platform URL and return normalized fields from its
    Restaurant JSON-LD, or None if unavailable/not-UAE/no match.
    429s are retried with backoff+jitter (previously silently gave up and
    fell back to the LLM path, wasting the more-reliable structured hit)."""
    try:
        resp = await client.get(url, headers=_HEADERS, timeout=15, follow_redirects=True)
    except httpx.HTTPError as exc:
        logger.warning(f"[platform_scrape] fetch failed for {url}: {exc}")
        return None

    if resp.status_code == 429:
        logger.warning(f"[platform_scrape] {url} rate-limited (429), retrying with backoff")
        raise _RetryableStatus(f"429 from {url}")

    if resp.status_code != 200:
        logger.warning(f"[platform_scrape] {url} returned HTTP {resp.status_code}")
        return None

    ld = _extract_restaurant_ldjson(resp.text)
    if not ld:
        return None

    if not _looks_like_uae(ld):
        logger.warning(f"[platform_scrape] {url} listing isn't in the UAE (country/geo check failed) -- skipping")
        return None

    telephone_raw = ld.get("telephone") or ""
    first_phone = telephone_raw.split(",")[0].strip() if telephone_raw else None
    geo = ld.get("geo") or {}
    rating = ld.get("aggregateRating") or {}

    return {
        "name": ld.get("name"),
        "address": _flatten_address(ld.get("address")),
        "phone": first_phone or None,
        "phone_all": telephone_raw or None,
        "latitude": geo.get("latitude"),
        "longitude": geo.get("longitude"),
        "rating": rating.get("ratingValue"),
        "review_count": rating.get("ratingCount"),
    }


async def scrape_and_validate(
    url: str,
    platform: str,
    candidate_names: list[str],
    client: httpx.AsyncClient,
) -> dict | None:
    """
    Phase-2 entry point: fetch one collected platform URL, parse its
    JSON-LD, and validate it actually matches one of the Talabat names
    that surfaced it (name-token overlap -- same guardrail used for the
    LLM path). `candidate_names` is a list because the same real URL can
    be found by more than one near-duplicate Talabat name variant.

    Returns the normalized listing dict (with which candidate name it
    matched) or None if the fetch/parse/match failed.
    """
    try:
        listing = await fetch_platform_listing(url, client)
    except Exception as exc:
        logger.warning(f"[platform_scrape] permanent failure for {url}: {exc}")
        return None
    if not listing:
        return None

    listing_name = listing.get("name") or ""
    matched_name = next(
        (
            n for n in candidate_names
            if name_supported_by_results(n, {"results": [{"title": listing_name, "content": ""}]})
        ),
        None,
    )
    if not matched_name:
        logger.warning(
            f"[platform_scrape] {platform} listing name {listing_name!r} doesn't match "
            f"any of {candidate_names} -- skipping"
        )
        return None

    listing["platform"] = platform
    listing["source_url"] = url
    listing["matched_talabat_name"] = matched_name
    return listing
