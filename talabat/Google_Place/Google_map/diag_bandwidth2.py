"""
diag_bandwidth2.py
---------------------
Tests whether CLICKING a result link (SPA client-side navigation)
avoids re-downloading Google Maps' ~5.9MB JS bundle, compared to
page.goto() to the same detail URL (a fresh full page load).
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


def make_tracker():
    bytes_by_type = defaultdict(int)
    count_by_type = defaultdict(int)

    async def track_response(response):
        try:
            body = await response.body()
            size = len(body)
        except Exception:
            size = 0
        rtype = response.request.resource_type
        bytes_by_type[rtype] += size
        count_by_type[rtype] += 1

    return bytes_by_type, count_by_type, track_response


async def block(route):
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=PROXY)
        context = await browser.new_context(locale="en-US")
        await context.route("**/*", block)
        page = await context.new_page()

        bytes_by_type, count_by_type, tracker = make_tracker()
        page.on("response", lambda r: asyncio.ensure_future(tracker(r)))

        print("STEP 1: Load search page (baseline JS bundle download)...")
        await page.goto("https://www.google.com/maps/search/Amiro+UAE+Restaurant?hl=en", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000)
        step1_total = sum(bytes_by_type.values())
        print(f"  After search page load: {step1_total/1024/1024:.2f} MB")

        await page.wait_for_selector('div[role="feed"]', timeout=10000)
        links = await page.query_selector_all('div[role="feed"] a[aria-label]')
        print(f"  Found {len(links)} results")

        if len(links) >= 2:
            print("\nSTEP 2: CLICK on result #1 (testing SPA client-side nav)...")
            await links[0].click()
            await page.wait_for_timeout(2500)
            step2_total = sum(bytes_by_type.values())
            print(f"  After click nav: {step2_total/1024/1024:.2f} MB  (delta: {(step2_total-step1_total)/1024:.1f} KB)")

            print("\nSTEP 3: Go back, CLICK on result #2...")
            await page.go_back(wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)
            links2 = await page.query_selector_all('div[role="feed"] a[aria-label]')
            if len(links2) >= 2:
                await links2[1].click()
                await page.wait_for_timeout(2500)
                step3_total = sum(bytes_by_type.values())
                print(f"  After 2nd click nav: {step3_total/1024/1024:.2f} MB  (delta: {(step3_total-step2_total)/1024:.1f} KB)")

        await browser.close()

    print(f"\n{'='*60}")
    print("Final bandwidth by resource type:")
    for rtype, size in sorted(bytes_by_type.items(), key=lambda x: -x[1]):
        print(f"  {rtype:<15} {size/1024:>10.1f} KB   ({count_by_type[rtype]} requests)")


if __name__ == "__main__":
    asyncio.run(main())
