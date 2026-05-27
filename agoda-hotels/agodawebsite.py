import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
import re
import time
import pandas as pd
from playwright.sync_api import sync_playwright

# === ✅ CONFIG ===
URL = "https://www.agoda.com/en-in/search?city=1390&checkIn=2026-04-09&los=10&rooms=1&adults=2&children=0&locale=en-in&ckuid=ca286329-1085-4665-af76-27fe1a9205f9&prid=0&gclid=EAIaIQobChMIpf7TlI3gkwMVjZhmAh2BFhQOEAMYASAAEgLxEfD_BwE&currency=INR&correlationId=a4c80fec-2cb7-401e-931a-09ccc42c6222&analyticsSessionId=2210872737550343550&pageTypeId=5&realLanguageId=15&languageId=1&origin=IN&stateCode=UP&cid=1922866&tag=f2a21800-104b-483b-8816-bded473311a5&userId=ca286329-1085-4665-af76-27fe1a9205f9&whitelabelid=1&loginLvl=0&storefrontId=3&currencyId=27&currencyCode=INR&htmlLanguage=en-in&cultureInfoName=en-in&machineName=sg-pc-6g-geo-web-user-7684d67649-fsz64&trafficGroupId=5&sessionId=nnswv5rkybfwqk4k1yalctrg&trafficSubGroupId=9&aid=82361&useFullPageLogin=true&cttp=4&isRealUser=true&mode=production&browserFamily=Chrome&cdnDomain=agoda.net&checkOut=2026-04-19&priceCur=INR&textToSearch=Dhaka&travellerType=1&familyMode=off&ds=IeK5s0TuTfG90xGA&hotelStarRating=5%2C101&hotelAccom=34&productType=-1"
OUTPUT_FILE = "agoda_Bangla_hotels.xlsx"



def scroll_page_to_load_all(page, page_number):
    print(f"🔁 Scrolling full page {page_number} to load all cards...")
    seen = set()
    same_count = 0
    for scroll_num in range(60):
        page.evaluate("window.scrollBy(0, 1000)")
        time.sleep(3)
        cards = page.locator("//a[@aria-label][.//h3]")
        current = set(cards.evaluate_all("els => els.map(el => el.getAttribute('aria-label'))"))
        new = len(current - seen)
        print(f"🔁 Page {page_number} | Scroll #{scroll_num + 1} | Cards: {len(current)} | New: {new}")
        if new == 0:
            same_count += 1
        else:
            same_count = 0
            seen.update(current)
        if same_count >= 5:
            break
    print(f"✅ Finished scrolling page {page_number}")

def scrape_cards_on_page(page, start_idx, hotels_data):
    cards = page.locator("//a[@aria-label][.//h3]")
    count = cards.count()
    print(f"🏨 Total hotel cards found: {count}")
    for i in range(count):
        try:
            card = cards.nth(i)
            title = card.locator("h3").inner_text(timeout=3000).strip()
            location = card.locator("span:below(h3)").first.inner_text(timeout=3000).strip()
            try:
                rating = card.locator("div[data-testid='rating-container']").inner_text(timeout=2000).strip()
            except:
                rating = "N/A"
            url = card.get_attribute("href")
            if url and not url.startswith("http"):
                url = "https://www.agoda.com" + url
            
            # Add hotel data to list
            hotels_data.append({
                'Title': title,
                'Locations': location,
                'Ratings': rating,
                'Source url': url
            })
            
            print(f"[{start_idx + i + 1}] ✅ Added: {title}")
        except Exception as e:
            print(f"[{start_idx + i + 1}] ❌ Error: {e}")
    
    return count

def scrape_agoda():
    hotels_data = []  # List to store all hotel data
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        print("🌐 Opening Agoda search page...")
        page.goto(URL, timeout=90000)
        page.wait_for_timeout(8000)
        print("✅ Page loaded successfully.")

        total_saved = 0
        page_num = 1

        while True:
            scroll_page_to_load_all(page, page_num)
            cards_count = scrape_cards_on_page(page, total_saved, hotels_data)
            total_saved += cards_count

            try:
                next_btn = page.locator("//button[@id='paginationNext']")
                if next_btn.is_visible() and next_btn.is_enabled():
                    print("➡️ Clicking Next...")
                    next_btn.click()
                    page.wait_for_timeout(8000)
                    page.evaluate("window.scrollTo(0, 0)")
                    page_num += 1
                else:
                    print("⛔ No more pages.")
                    break
            except:
                print("⛔ Next button not found or disabled.")
                break

        browser.close()
        
        # Create Excel file with collected data
        if hotels_data:
            df = pd.DataFrame(hotels_data)
            df.to_excel(OUTPUT_FILE, index=False, engine='openpyxl')
            print(f"📊 Excel file saved: {OUTPUT_FILE} with {len(hotels_data)} hotels")
        else:
            print("❌ No data collected to save.")
        
        print("🏁 All done.")

if __name__ == "__main__":
    scrape_agoda()
