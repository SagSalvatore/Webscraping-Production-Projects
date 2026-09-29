"""
diag_bandwidth.py
--------------------
Diagnoses where proxy bandwidth is actually going for one full
search+detail cycle, broken down by resource type, so the fix targets
the real biggest consumers instead of guessing.
"""
import asyncio
import os
import sys
from collections import defaultdict
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

BLOCKED_RESOURCE_TYPES = {"image", "font", "media", "stylesheet"}

bytes_by_type = defaultdict(int)
count_by_type = defaultdict(int)
bytes_by_domain = defaultdict(int)


async def block_and_track(route):
    req = route.request
    if req.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def track_response(response):
    try:
        body = await response.body()
        size = len(body)
    except Exception:
        size = 0
    rtype = response.request.resource_type
    bytes_by_type[rtype] += size
    count_by_type[rtype] += 1
    domain = response.url.split("/")[2] if "//" in response.url else response.url
    bytes_by_domain[domain] += size


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=PROXY)
        context = await browser.new_context(locale="en-US")
        await context.route("**/*", block_and_track)
        page = await context.new_page()
        page.on("response", lambda r: asyncio.ensure_future(track_response(r)))

        print("Loading search page...")
        await page.goto("https://www.google.com/maps/search/Ami+Bakehouse+UAE+Restaurant?hl=en", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000)

        try:
            await page.wait_for_selector('div[role="feed"]', timeout=10000)
            links = await page.query_selector_all('div[role="feed"] a[aria-label]')
            if links:
                href = await links[0].get_attribute("href")
                print(f"\nLoading detail page for first result...")
                await page.goto(href, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(2500)
        except Exception as e:
            print("no feed:", e)

        await page.wait_for_timeout(1000)
        await browser.close()

    print(f"\n{'='*60}")
    print("Bandwidth by resource type:")
    total = sum(bytes_by_type.values())
    for rtype, size in sorted(bytes_by_type.items(), key=lambda x: -x[1]):
        print(f"  {rtype:<15} {size/1024:>10.1f} KB   ({count_by_type[rtype]} requests)")
    print(f"\nTOTAL: {total/1024/1024:.2f} MB")
    print(f"\n{'='*60}")
    print("Bandwidth by domain (top 10):")
    for domain, size in sorted(bytes_by_domain.items(), key=lambda x: -x[1])[:10]:
        print(f"  {domain:<40} {size/1024:>10.1f} KB")


if __name__ == "__main__":
    asyncio.run(main())
