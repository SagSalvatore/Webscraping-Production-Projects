"""
prototype_scraper.py
----------------------
DIY Google Maps scraper prototype using async Playwright + Oxylabs
residential proxy (UAE), as an alternative to paying Apify per-place.

Phase 1: search results page -> extract place name + URL for each result
Phase 2: place detail page   -> extract address, phone, website, maps URL

Validates against the user's exact example: "49er's Steak House uae restaurant"
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(Path(r"C:\Users\SagarSingh\Downloads\Google_Place\.env"), override=True)

OXY_USER = os.getenv("OXYLABS_USERNAME")
OXY_PASS = os.getenv("OXYLABS_PASSWORD")
OXY_HOST = os.getenv("OXYLABS_PROXY_HOST")
OXY_PORT = os.getenv("OXYLABS_PROXY_PORT")
OXY_COUNTRY = os.getenv("OXYLABS_COUNTRY", "ae")

PROXY = {
    "server": f"http://{OXY_HOST}:{OXY_PORT}",
    "username": f"customer-{OXY_USER}-country-{OXY_COUNTRY.upper()}",
    "password": OXY_PASS,
}


async def search_places(page, query: str) -> list:
    """Phase 1: search Google Maps, return [{name, url}, ...] for each result."""
    url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}?hl=en"
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(3000)

    # Results feed
    try:
        await page.wait_for_selector('div[role="feed"]', timeout=15000)
    except Exception:
        pass  # might be a single-result direct redirect to place page

    results = []
    links = await page.query_selector_all('div[role="feed"] a[aria-label]')
    for link in links:
        name = await link.get_attribute("aria-label")
        href = await link.get_attribute("href")
        if name and href:
            results.append({"name": name, "url": href})

    # If no feed (single direct result), current URL might already be a place page
    if not results and "/maps/place/" in page.url:
        results.append({"name": query, "url": page.url})

    return results


def force_english(url: str) -> str:
    if "hl=" in url:
        return re.sub(r"hl=[^&]*", "hl=en", url)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}hl=en"


async def scrape_place_detail(page, place_url: str) -> dict:
    """Phase 2: visit a place URL, extract address/phone/website/maps_url."""
    place_url = force_english(place_url)
    await page.goto(place_url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(2500)

    result = {"address": None, "phone": None, "website": None, "maps_url": page.url}

    # Address -- data-item-id is a stable Google-assigned semantic attribute,
    # unlike the auto-generated/minified class names which can change.
    addr_el = await page.query_selector('button[data-item-id="address"]')
    if addr_el:
        text = await addr_el.get_attribute("aria-label")
        result["address"] = re.sub(r"^Address:\s*", "", text).strip() if text else None

    # Phone
    phone_el = await page.query_selector('button[data-item-id^="phone:tel:"]')
    if phone_el:
        text = await phone_el.get_attribute("aria-label")
        result["phone"] = re.sub(r"^Phone:\s*", "", text).strip() if text else None

    # Website
    site_el = await page.query_selector('a[data-item-id="authority"]')
    if site_el:
        result["website"] = await site_el.get_attribute("href")

    return result


async def main():
    query = "49er's Steak House uae restaurant"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=PROXY)
        context = await browser.new_context(locale="en-US")
        page = await context.new_page()

        print(f"PHASE 1 -- searching: {query!r}")
        results = await search_places(page, query)
        print(f"Found {len(results)} result(s):")
        for r in results:
            print(f"  {r['name']!r} -> {r['url'][:100]}")

        print(f"\nPHASE 2 -- scraping detail for first result...")
        if results:
            detail = await scrape_place_detail(page, results[0]["url"])
            print(json.dumps({**results[0], **detail}, indent=2, ensure_ascii=False))
        else:
            print("No results found -- selectors may need adjustment.")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
