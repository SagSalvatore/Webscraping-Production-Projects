# hungerplaywright_scraper_hungerstation_fixed.py

import os
import re
import asyncio
import random
from urllib.parse import quote
from playwright.async_api import async_playwright

# === Oxylabs credentials ===
# Set these in your local environment before running.
username = os.getenv("OXYLABS_USERNAME", "")
password = os.getenv("OXYLABS_PASSWORD", "")
country = os.getenv("OXYLABS_COUNTRY", "sa")

# === Output directory ===
output_dir = "Riyadh_king-faisal-neighborhood"
os.makedirs(output_dir, exist_ok=True)

# === Target URL ===
BASE_URL = "https://hungerstation.com/sa-en/restaurants/riyadh/king-faisal-neighborhood?page="

async def run():
    if not username or not password:
        raise RuntimeError("Set OXYLABS_USERNAME and OXYLABS_PASSWORD before running this script.")

    current_page = 1
    max_empty = 4
    empty_streak = 0

    async with async_playwright() as p:
        session_id = random.randint(10000, 99999)
        proxy_user = f"customer-{username}-cc-{country}-sessid-{session_id}"

        browser = await p.chromium.launch(headless=False, slow_mo=40)
        context = await browser.new_context(
            proxy={
                "server": "http://pr.oxylabs.io:7777",
                "username": proxy_user,
                "password": password
            },
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
            viewport={"width": 1280, "height": 800},
            ignore_https_errors=True
        )
        page = await context.new_page()

        while True:
            url = BASE_URL + str(current_page)
            print(f"\n🌐 Visiting Page {current_page}: {url}")

            try:
                response = await page.goto(url, timeout=60000)
                await page.wait_for_load_state("networkidle", timeout=30000)
                await page.wait_for_timeout(random.randint(4000, 6000))  # 👈 Human-like delay

                if response is None or not response.ok:
                    raise Exception("Bad HTTP response")

            except Exception as e:
                print(f"❌ Failed to load page {current_page}: {type(e).__name__} - {e}")
                print("⚠️ Retrying next page with a new session...")

                # Create new proxy session
                session_id = random.randint(10000, 99999)
                proxy_user = f"customer-{username}-cc-{country}-sessid-{session_id}"

                try:
                    await browser.close()
                except:
                    pass

                browser = await p.chromium.launch(headless=False, slow_mo=40)
                context = await browser.new_context(
                    proxy={
                        "server": "http://pr.oxylabs.io:7777",
                        "username": proxy_user,
                        "password": password
                    },
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    locale="en-US",
                    viewport={"width": 1280, "height": 800},
                    ignore_https_errors=True
                )
                page = await context.new_page()
                current_page += 1
                continue

            # Parse restaurant cards
            cards = await page.query_selector_all("a[href*='/restaurant/']")
            if not cards:
                print(f"⚠️ Page {current_page} returned no restaurants.")
                empty_streak += 1
                if empty_streak >= max_empty:
                    print("⛔ Max empty pages reached. Exiting.")
                    break
                current_page += 1
                continue

            print(f"🍽️ Found {len(cards)} restaurants.")
            empty_streak = 0

            for i, card in enumerate(cards, start=1):
                try:
                    name = "N/A"
                    cuisine = "N/A"
                    location = "N/A"

                    name_el = await card.query_selector("h1")
                    if name_el:
                        name = await name_el.inner_text()
                        name = name.strip() if name else "N/A"

                    cuisine_el = await card.query_selector("p")
                    if cuisine_el:
                        cuisine = await cuisine_el.inner_text()
                        cuisine = cuisine.strip() if cuisine else "N/A"

                    location_el = await card.query_selector("div[class*=address]")
                    if location_el:
                        location = await location_el.inner_text()
                        location = location.strip() if location else "N/A"

                    base_name = name if name != "N/A" else f"Unnamed_{current_page}_{i}"
                    safe_name = re.sub(r'[\\/*?:"<>|]', "_", base_name)[:50]
                    filename = os.path.join(output_dir, f"{safe_name}_{current_page}_{i}.txt")

                    with open(filename, "w", encoding="utf-8") as f:
                        f.write(f"Restaurant Name: {name}\n")
                        f.write(f"Cuisine: {cuisine}\n")
                        f.write(f"Location: {location}\n")
                        f.write(f"Page: {current_page}\n")
                        f.write(f"URL: {url}\n")

                    print(f"🍴 Restaurant: {name}")
                    print(f"📝 Saved: {filename}")
                    await asyncio.sleep(random.uniform(0.3, 0.6))

                except Exception as e:
                    print(f"⚠️ Error saving restaurant: {e}")

            current_page += 1
            await asyncio.sleep(random.uniform(2.5, 4.0))

        await browser.close()

# Start the scraper
if __name__ == "__main__":
    asyncio.run(run())
