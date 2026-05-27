import asyncio
import json
from datetime import datetime
from playwright.async_api import async_playwright
from selectolax.parser import HTMLParser
import os

# === INPUT URLS TO SCRAPE ===
urls = [
    "https://www.elmenus.com/cairo/salad-master-ywll6",
"https://www.elmenus.com/cairo/belal-o2ry",
"https://www.elmenus.com/cairo/popcity-lqxro",
"https://www.elmenus.com/cairo/abu-auf-dn84",
"https://www.elmenus.com/cairo/dukes-kq9o",
"https://www.elmenus.com/cairo/blaze-resto-8vzw",
"https://www.elmenus.com/cairo/haty-el-ma-mon-qgld",
"https://www.elmenus.com/cairo/heart-attack-zmq2y",
"https://www.elmenus.com/cairo/el-sharkawy-el-asly-zmk6k",
"https://www.elmenus.com/cairo/haty-el-sheikh-xxwv",
"https://www.elmenus.com/cairo/spicy-crepe-el-sefarat-k7adg",
"https://www.elmenus.com/cairo/cave-r7d57",
"https://www.elmenus.com/cairo/bido-9kll",
"https://www.elmenus.com/cairo/la-poire-y6ma",
"https://www.elmenus.com/cairo/la-poire-cafe-339d",
"https://www.elmenus.com/cairo/aleppo-s-shawerma-7zag",
"https://www.elmenus.com/cairo/burger-king-3la7",
"https://www.elmenus.com/cairo/hunger-station-28gqy",
"https://www.elmenus.com/cairo/halaket-el-samak-9zm6",
"https://www.elmenus.com/cairo/hadramout-tabakh-el-rayes-wqg48",
"https://www.elmenus.com/cairo/hamam-abdo-o7lkn",
"https://www.elmenus.com/cairo/city-crepe-28k3y",
"https://www.elmenus.com/cairo/chickana-r739x",
"https://www.elmenus.com/cairo/pizza-time-lq8ao",
"https://www.elmenus.com/cairo/asmak-anwar-el-hussin-a4p6",
"https://www.elmenus.com/cairo/pastaweesy-8qk8",
"https://www.elmenus.com/cairo/baladena-4n3x",
"https://www.elmenus.com/cairo/cinnabon-bakery-cafe-8zw2",
"https://www.elmenus.com/cairo/tseppas-rdzw",
"https://www.elmenus.com/cairo/sushi-way-lqv4g",
"https://www.elmenus.com/cairo/haty-el-baraka-28zxy",
"https://www.elmenus.com/cairo/cheesecake-company-956ql",
"https://www.elmenus.com/cairo/chicken-planet-do757",
"https://www.elmenus.com/cairo/the-bogo-s-gmnv4",
"https://www.elmenus.com/cairo/chicken-fila-qgyg",
"https://www.elmenus.com/cairo/tarboush-afandi-vyq6g",
"https://www.elmenus.com/cairo/khodlak-break-44738",
"https://www.elmenus.com/cairo/la-pomme-pastries-rwl7",
"https://www.elmenus.com/cairo/batates-zalabya-zpnk",
"https://www.elmenus.com/cairo/wok-cook-r7zvx",
"https://www.elmenus.com/cairo/kanary-w5vk",
"https://www.elmenus.com/cairo/city-crepe-ahmed-fakhry-do6ax",
"https://www.elmenus.com/cairo/taco-s-g38n",
"https://www.elmenus.com/cairo/ashraf-farghaly-a8r6",
"https://www.elmenus.com/cairo/el-sweesy-fresh-fish-gmwxg",
"https://www.elmenus.com/cairo/pizza-station-mypvo",
"https://www.elmenus.com/cairo/butcher-s-burger-g2l8",
"https://www.elmenus.com/cairo/diet-house-7rqg",
"https://www.elmenus.com/cairo/kansas-fried-chicken-qrr7",
"https://www.elmenus.com/cairo/tbs-the-bakery-shop-ndo7",
]

OUTPUT_FILE = "elmenus_visual_locations2.json"
FAILED_FILE = "elmenus_failed_urls.txt"
results = []

# === LOAD EXISTING DATA ===
if os.path.exists(OUTPUT_FILE):
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        results = json.load(f)
existing_urls = set(entry["url"] for entry in results)

# === ACCURATE LOCATION EXTRACTOR ===
def extract_location(html):
    tree = HTMLParser(html)
    p_tags = tree.css("p.info-value")
    for p in reversed(p_tags):
        if p.css_first("a.address-link"):
            text_part = p.text(deep=False).strip()
            a_part = p.css_first("a").text(strip=True)
            return f"{text_part} {a_part}".strip()
    return "N/A"

# === SCRAPE LOCATION FUNCTION ===
async def scrape_location(context, url):
    if url in existing_urls:
        print(f"⏩ Skipping already scraped: {url}")
        return None

    page = await context.new_page()
    try:
        print(f"🌍 Visiting: {url}")
        await page.goto(url, timeout=90000, wait_until="domcontentloaded")

        # Force scroll to trigger lazy load
        await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(3000)

        # Close popup if shown
        try:
            await page.locator("button:has-text('Discard Basket')").click(timeout=5000)
            print("🧺 Basket popup closed.")
        except:
            pass

        try:
            await page.wait_for_selector("p.info-value >> a.address-link", timeout=15000)
        except:
            print("⚠️ address-link not found — trying fallback anyway...")

        html = await page.content()
        location = extract_location(html)

        print(f"📍 Location: {location}")
        return {
            "url": url,
            "location": location
        }

    except Exception as e:
        print(f"❌ Failed on {url}: {e}")
        with open(FAILED_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} - {url}\n")
        return None
    finally:
        await page.close()

# === MAIN SCRAPER RUNNER ===
async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )

        for url in urls:
            result = await scrape_location(context, url)
            if result:
                results.append(result)
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)

        await browser.close()
        print(f"\n✅ Done. Updated {OUTPUT_FILE}")

if __name__ == "__main__":
    asyncio.run(run())
