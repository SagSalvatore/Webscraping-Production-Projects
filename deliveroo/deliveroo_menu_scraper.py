import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

# === CONFIG ===
OUTPUT_FILE = "deliveroo_menu_output_24_07_06.json"
RESTAURANT_URLS = [
   "https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/hither-and-yon-restaurant?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/olives-and-salt?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-maryah-island/grand-beirut-restaurant-al-maryah?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/zayed-sports-city/drvn-pizzeria-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zahraa/freez-muroor?day=today&geohash=thqej8hymffb&time=1830",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-bateen/eggcellent-cafe?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-rayhan-north/hardees-al-mushrif?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-wahdah/that-wings-al-wahda-mall?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zaab/german-doner-kebab-manhal?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/al-fujairah-restaurant?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-nahyan/mix-n-match-al-jazira-ad4?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-rayhan-south/healthy-bowl-restaurant?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-nahyan/flip-the-bird-abn?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-nahyan/sunrise-pizza-boulevard-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zahraa/pizza-and-co-muroor?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/embassies-district/iceberg-cafe-and-grill?day=tomorrow&geohash=thqej8hymffb&time=1130",
"https://deliveroo.ae/menu/Abu%20Dhabi/abu-dhabi-gate-city/tazal-restaurants-management?day=today&geohash=thqej8hymffb&time=2015",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zafranah/khuttar-al-iraqi-muroor-road?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-dhafrah/jarful-al-danah?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zaab/gaya-cafe-previously-il-caffe-di-roma-al-manhal?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/training-day-healthy-salads-and-warm-bowls-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zahiyah/qasr-alasala-mandi-and-mathbi-restaurant?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-nahyan/mamas-cupcakes?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/in61xtyone-wtc?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-wahdah/al-ibrahimi-palace-al-wahda-mall-branch?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-khubeirah/le-pont-cafe?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-rayhan-north/hatam-mushrif-mall?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-mushrif/momocha-restaurant-mushrif-mall?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-nahyan/acaixpress-al-nahyan?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-wahdah/farfosh-fruit-fresh-juice-al-nahyan?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/eggsclusive-restaurant?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-etihad/patty-by-bod-bod?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-dhafrah/tayba-gourmet-ad?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-dhafrah/ginos-deli-abn?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/barada-bakery?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/zeeks-cafe?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zahiyah/tawa-bakery?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-nahyan/restaurant-pure-diet-house?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-maryah-island/texas-road-house-galleria-mall?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-dhafrah/gandofly-sea-food-restaurant?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zahraa/crispy-chicken-al-mushrif?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-maryah-island/lpm-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/chatore?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zahiyah/chef-n-wok-restaurant?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/abu-dhabi-gate-city/krave-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zaab/andoks-al-karamah?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-reem-island/sushibay-restaurant?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/spicy-fresh-chicken?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-manhal/waragz-al-khalidiya?day=tomorrow&geohash=thqej8hymffb&time=0045",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-reem-island/bangkok-city-reem-mall?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/marina-village/1881-restaurant?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-wahdah/debonairs-pizza-wahda-mall?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-seef-village/yamanote-atelier-adms?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-nahyan/under500-villagio-ad6?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-wahdah/gateau-gourmet-cafe?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/papa-zou-mina?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/maraheb-restaurant-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-dhafrah/chai-junoon-cafe?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-dhafrah/bakeology-lab-cafe-al-danah?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zafranah/markhor-restaurant?day=today&geohash=thqej8hymffb&time=1645",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-rayhan-south/malfoof-wa-haris-mushrif?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-bateen/1918cafe?day=tomorrow&geohash=thqej8hymffb&time=0745",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/jutt-karahi-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-maryah-island/pf-changs-galleria-mall?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zahiyah/dennys-abu-dhabi-mall?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-reem-island/nazira-kitchen-al-reem?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/fyoozhen-wtc-mall?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/malabar-avil-milk?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-dhafrah/shawarma-hartna-branch?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zahiyah/burger-bliss-water-front?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/poke-and-co-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/delektia?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/hapi-ice-cream-editions-wtc?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/the-palestinian-bakery-najda-auh?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-wahdah/pasta-kart-al-wahda-mall?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-manhal/vine-communities-cafe?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-etihad/healthy-and-delicious-al-mushrif?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-wahdah/croissant-bakery?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/zanzan-eat-restaurant?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/vansha-ghar-restaurant?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-nahyan/mission-katsu-curry-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-musalla/salt-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-nahyan/holy-crepe?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-dhafrah/wingman-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zahiyah/la-bistro-cafe?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-reem-island/k-seoul?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zahiyah/sahten?day=today&geohash=thqej8hymffb&time=1645",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zahraa/tellini-restaurant-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-nahyan/proper-sliders-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-etihad/sabores-restaurant-al-nahyan?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-maryah-island/antonia-chic?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-khubeirah/tatbilah-a-and-j-restaurant?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/naughty-bird-skyline-university-shj?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-etihad/pasta-and-wrap-al-mushrif?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/al-husien-pastry?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/zayed-sports-city/zereshk-iranian-food?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-manhal/morning-kick-restaurant?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-maryah-island/bb-social-dining-al-maryah-island?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/jubnah-and-labnah-pastries-and-restaurant-auh?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-zafranah/springs-cafe?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-danah/duchin-wtc?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/hudayriyat-island/petal-speciality-coffee-hudayriat?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/abu-dhabi-hills/boon-coffee-roasters-abu-dhabi?day=today&geohash=thqej8hymffb&time=ASAP",
"https://deliveroo.ae/menu/Abu%20Dhabi/al-seef-village/soil?day=today&geohash=thqej8hymffb&time=ASAP",




]

async def scrape_restaurant(page, url):
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_selector("h2", timeout=20000)

        title = await page.title()
        sections = await page.query_selector_all("div[id^='layout-'] div[data-testid='layout-head']")

        full_menu = []

        for section in sections:
            # Get category name
            h2 = await section.query_selector("h2")
            category = await h2.text_content() if h2 else "Uncategorized"
            category = category.strip()

            # Get the menu card block (sibling of category container)
            sibling_handle = await section.evaluate_handle("node => node.parentElement.nextElementSibling")
            sibling = sibling_handle.as_element()
            if not sibling:
                continue

            cards = await sibling.query_selector_all("li:has(div[class^='MenuItemCard'])")
            items = []

            for card in cards:
                name_el = await card.query_selector("div.notranslate p")
                name = await name_el.text_content() if name_el else ""

                desc_el = await card.query_selector("div[class*='MenuItemCard'] > span")
                description = await desc_el.text_content() if desc_el else ""

                # Fix price extraction from AED-containing spans
                price_el = await card.query_selector("span:text('AED')")
                price = await price_el.text_content() if price_el else ""

                name = name.strip() if name else ""
                description = description.strip() if description else name
                price = price.strip().replace('\xa0', ' ') if price else ""

                if name:
                    items.append({
                        "name": name,
                        "description": description,
                        "price": price
                    })

            if items:
                full_menu.append({
                    "category": category,
                    "items": items
                })

        return {
            "url": url,
            "restaurant_name": title,
            "cuisines": "",
            "menu": full_menu
        }

    except Exception as e:
        print(f"❌ Error scraping {url}: {e}")
        return {
            "url": url,
            "restaurant_name": "Unknown",
            "cuisines": "",
            "menu": []
        }

async def run():
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        for url in RESTAURANT_URLS:
            print(f"\n🌍 Visiting: {url}")
            result = await scrape_restaurant(page, url)
            results.append(result)

        Path(OUTPUT_FILE).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"\n✅ Done. Saved to {OUTPUT_FILE}")
        await browser.close()

asyncio.run(run())
