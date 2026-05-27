# GOFOOD BULLETPROOF SCROLLER ✅
import sys
import asyncio
import os
import re
import random
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding='utf-8')

async def scrape_gofood():
    city = "jakarta"
    url = f"https://gofood.co.id/en/{city}/restaurants/near_me"
    output_folder = f"restaurants_{city}"
    os.makedirs(output_folder, exist_ok=True)

    username = os.getenv("OXYLABS_USERNAME", "")
    password = os.getenv("OXYLABS_PASSWORD", "")
    country = os.getenv("OXYLABS_COUNTRY", "")
    if not username or not password or not country:
        raise RuntimeError("Set OXYLABS_USERNAME, OXYLABS_PASSWORD, and OXYLABS_COUNTRY before running.")
    session_id = random.randint(10000, 99999)

    proxy_config = {
        "server": "http://pr.oxylabs.io:7777",
        "username": f"customer-{username}-cc-{country}-sessid-{session_id}",
        "password": password,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, proxy=proxy_config)
        context = await browser.new_context(locale="en-US")
        page = await context.new_page()

        print(f"🔗 Navigating to {url}...")
        await page.goto(url, timeout=90000, wait_until="load")
        await page.wait_for_timeout(5000)
        print("✅ Page loaded successfully.")

        # 🔁 Human-like deep scroll loop
        print("📜 Simulating long, slow full scroll...")

        previous_height = 0
        scroll_attempt = 0
        seen_urls = set()

        while True:
            scroll_height = await page.evaluate("document.body.scrollHeight")
            if scroll_height == previous_height:
                break
            previous_height = scroll_height

            await page.evaluate("""
                () => {
                    window.scrollBy({ top: 1000, behavior: 'smooth' });
                }
            """)
            await page.wait_for_timeout(random.randint(2000, 3000))
            scroll_attempt += 1
            anchors = await page.query_selector_all("div.my-6.grid a")
            for a in anchors:
                href = await a.get_attribute("href")
                if href and href.startswith("/en"):
                    seen_urls.add("https://gofood.co.id" + href)
            print(f"🔄 Scroll {scroll_attempt} — Listings collected: {len(seen_urls)}")

        print(f"✅ Finished scrolling. Total listings: {len(seen_urls)}")

        # ⚠️ Optional: Now click Load more if button exists
        try:
            load_more_btn = await page.query_selector("button:has-text('Load more')")
            if load_more_btn:
                await page.evaluate("(btn) => btn.click()", load_more_btn)
                print("🔘 Clicked Load more button")
                await page.wait_for_timeout(7000)
                # Scroll again to reveal more
                for i in range(5):
                    await page.evaluate("() => window.scrollBy({ top: 800, behavior: 'smooth' })")
                    await page.wait_for_timeout(2000)
        except:
            print("⚠️ Load more button not clickable or missing")

        print(f"✅ Collected {len(seen_urls)} restaurant URLs.")

        # 🔍 Scrape each restaurant
        scraped_count = 0
        for restro_url in seen_urls:
            try:
                await page.goto(restro_url, timeout=70000, wait_until="load")
                await page.wait_for_timeout(2500)

                name = await page.inner_text("h1") if await page.query_selector("h1") else "Unnamed Restaurant"
                cuisines = await page.inner_text("p.text-gf-content-secondary") if await page.query_selector("p.text-gf-content-secondary") else "Not found"

                try:
                    await page.click("div.pl-2 div.cursor-pointer", timeout=3000)
                    await page.wait_for_selector("div.text-gf-content-muted.gf-body-s", timeout=5000)
                    location = await page.inner_text("div.text-gf-content-muted.gf-body-s")
                except:
                    location = "Not found"

                filename = re.sub(r"[^\w\s-]", "", name).replace(" ", "_").lower()
                file_path = os.path.join(output_folder, f"{filename}.txt")
                counter = 1
                while os.path.exists(file_path):
                    file_path = os.path.join(output_folder, f"{filename}_{counter}.txt")
                    counter += 1

                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(f"Name: {name}\nLocation: {location}\nURL: {restro_url}\nCuisines: {cuisines}\n")

                scraped_count += 1
                print(f"✅ Scraped: {name} (Saved to {file_path})")

            except Exception as e:
                print(f"⚠️ Failed to scrape {restro_url}: {e}")

        print(f"\n🎉 Total scraped: {scraped_count} → Saved in '{output_folder}'")
        await asyncio.sleep(999999)

asyncio.run(scrape_gofood())
















