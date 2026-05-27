import os
import json
import time
import random
import re
import difflib
from seleniumwire import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc
from selectolax.parser import HTMLParser

AUTOSAVE_PATH = "hungerstation_selenium_autosave3.json"
FAILED_PATH = "hungerstation_failed_restaurants.json"

def human_delay(a=2, b=4):
    time.sleep(random.uniform(a, b))

def close_popup(driver):
    try:
        for _ in range(3):
            try:
                popup_btn = WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.absolute.top-4.right-4 button"))
                )
                driver.execute_script("arguments[0].click();", popup_btn)
                print("🧹 Popup closed via JS.")
                return
            except:
                time.sleep(1)
        print("⚠️ Popup not found or failed to close after retries.")
    except Exception as e:
        print(f"⚠️ Popup closing error: {e}")

def normalize_name(name):
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.replace("restaurants", "restaurant")
    return name

def fuzzy_match(name1, name2, threshold=0.7):
    clean1 = normalize_name(name1)
    clean2 = normalize_name(name2)
    ratio = difflib.SequenceMatcher(None, clean1, clean2).ratio()
    return ratio >= threshold

def scrape_items_from_html_by_section(section_html, category_name):
    tree = HTMLParser(section_html)
    items = []
    for button in tree.css("button.menu-item"):
        item_block = HTMLParser(button.html)
        name = item_block.css_first("h3.menu-item-title")
        name_text = name.text(strip=True) if name else item_block.text(strip=True).split("\n")[0]
        desc = item_block.css_first("p.menu-item-description")
        price = item_block.css_first("p.text-greenBadge.text-base.mx-2")
        calories = item_block.css_first("p.text-secondary.text-base.mx-2")

        items.append({
            "category": category_name,
            "item_name": name_text if name_text else "Unknown",
            "description": desc.text(strip=True) if desc else "",
            "price": price.text(strip=True) if price else "",
            "calories": calories.text(strip=True) if calories else ""
        })
    return items

def load_autosave():
    if os.path.exists(AUTOSAVE_PATH):
        with open(AUTOSAVE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_autosave(data):
    with open(AUTOSAVE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_failed(failed_list):
    with open(FAILED_PATH, "w", encoding="utf-8") as f:
        json.dump(failed_list, f, ensure_ascii=False, indent=2)

def get_clean_hungerstation_urls(driver):
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//a")))
    all_links = driver.find_elements(By.XPATH, "//a[starts-with(@href, 'https://hungerstation.com/sa-en/restaurant')]")
    clean_links = []

    seen = set()
    for link in all_links:
        href = link.get_attribute("href")
        if (
            href and
            "/restaurant" in href and
            "/restaurants" not in href and
            "google.com" not in href and
            href not in seen
        ):
            seen.add(href)
            clean_links.append(href)
        if len(clean_links) >= 5:
            break

    return clean_links

def extract_main_name(rest_name):
    return rest_name.split(" ")[0].strip()  # only first token before any cuisine list

def main():
    options = uc.ChromeOptions()
    options.add_argument("--incognito")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-popup-blocking")

    driver = uc.Chrome(version_main=137, options=options)

    driver.set_window_size(1280, 1024)

    restaurants = [
                                "Logmat Sitti Arabic - Saudi - Breakfast",
    "Lorenzo Pizza Beverages - Pizza - Pasta",
    "Lou Burger Burgers",
    "Louzan Desserts - Bakery - Coffee",
    "Loval Sweets Desserts",
    "Love Manaqish Bakery - Breakfast, Fast Food - Pizza",
    "Loved Desserts - Beverages - Coffee",
    "Lover's Pizza Pizza, Fast Food - Pizza",
    "Low Calories Sandwich - Healthy, Sandwich - Grill - Arabic - Indian - Healthy",
    "Lozian Desserts",
    "Lqma Desserts",
    "Lu Canz Cafe Desserts - Beverages - Coffee",
    "Lubian Wooden Shawaya Arabic - Saudi",
    "Lucky 7 Burgers Fast Food - Burgers",
    "Luigi Sandwich - Fast Food - Italian - International - Pizza - Pasta - Salads",
    "Lulu Crave Desserts - Beverages",
    "Lulus Recipe Desserts - Bakery",
    "Luqimat & More Desserts",
    "Luqmat Almunqousha Pizza - Breakfast - Pastries",
    "Luqya Desserts",
    "Lutfu Zade Baklava Desserts",
    "Lutz Desserts",
    "Luxury Upgrading Desserts - Coffee - International",
    "Lyaly Alsham Shawrma Shawarma",
    "M B Grill Lebanese",
    "M B J Fries Sandwich - Fast Food - American",
    "M&M Cafe Desserts - Coffee",
    "MADPICOM Arabic, Arabic - Saudi, Fast Food - Arabic",
    "MAIDE Sandwich - Grill - Beverages",
    "MAMOUL &Ghraybeh Desserts",
    "MARVAT Desserts - Arabic - Bakery - Beverages - Pastries",
    "MASOUB DARI Saudi - Breakfast",
    "MATHAQ DAREEN Sandwich - Arabic - Breakfast - Pastries",
    "MAUI Seafood - Sushi - Vegetarian",
    "MAZENCITO PIZZERIA Italian - Pizza - Pasta",
    "MCC Desserts - Beverages - Coffee",
    "MEAT UP Fast Food",
    "MERZE Cuisine:",
    "MILLIONAIRE RESTAURANT Arabic",
    "MISTER ONE Sandwich - Arabic",
    "MMMM Burger Fast Food - International - Burgers",
    "MOLHLB Arabic - Saudi",
    "MON 10 Desserts, Desserts - Bakery - Pastries",
    "MONTANA Desserts",
    "MOVENPICK Desserts - International - Ice Cream",
    "MOؤMEN Fast Food - Arabic",
    "MR BEAST BURGER Sandwich - Fast Food - American - Burgers",
    "MR.CRAB Sandwich - Seafood",
    "MRA Restaurant Indian",
    "MUBAHARS Asian - Beverages - Indonesian - Noodles",
    "MUGHLAI Indian",
    "MUM Desserts",
    "MUMBO Fast Food - Beverages - Burgers",
    "MUNIKH CHOCOLATE Desserts",
    "Maathir Coffee Shop Desserts - Coffee",
    "Mabroukah Sandwich - Arabic - American - Seafood",
    "Macan Cafe Desserts - Beverages - Coffee",
    "Macarona Sandwich - American - Burgers - Breakfast",
    "Macelliao Burger Burgers",
    "Macroni Italian",
    "Mad Dough Sandwich - Beverages - Pizza",
    "Mad pizza Pizza, Fast Food - Desserts - Italian - Healthy - Pizza, Fast Food - American - Pizza",
    "Madbi And Maqlouba Arabic",
    "Madbi al dera Arabic - Meat",
    "Madfoon Alsaddah Fast Food - Arabic",
    "Madghout Alhashi Restaurant Arabic - Saudi",
    "Madghout Beti Arabic",
    "Madghout qaren dabi Arabic",
    "Madghut Bn Gaber Grill - Arabic - Saudi",
    "Madhaq Alahramat Fast Food - Arabic - Egyptian",
    "Madhaq Aljazira Sandwich - Fast Food",
    "Madhaq Zman Arabic - Beverages - Breakfast",
    "Madhbi Al Sultan Arabic",
    "Madjoud House Arabic - Saudi",
    "Madura Asian",
    "Maein Arabic - Seafood - Breakfast",
    "Maestro Diet Healthy",
    "Maestro Pizza Fast Food - Italian - Beverages - International - Pizza",
    "Maesub Doha Arabic - Breakfast",
    "Maesub Wa Arikat Alnasim Arabic",
    "Magellan Asian - Seafood",
    "Magloba Asglan Arabic",
    "Maha Alsayed sweets Desserts",
    "Mahboob Sandwich - Arabic - Beverages - Shawarma",
    "Mahfour Desserts - Arabic - International",
    "Mahkoor Tea Desserts - Coffee",
    "Mahra Grill - Indian - International",
    "Makan Indian",
    "Makbos Zaman Arabic - Saudi",
    "Makbous Alqariah Restaurant Arabic - Saudi",
    "Makhbuzat Sara Desserts",
    "Makhsos Sandwich - Arabic - Shawarma - Breakfast",
    "Maki House Beverages - Sushi - Japanese - Salads",
    "Makkah hotel Indian",
    "Makki Burger Fast Food - American - Burgers",
    "Maktoom ALdaar Arabic",
    "Malaga Beverages - Coffee",
    "Malek Al Shawerma Sandwich - Fast Food - Shawarma",
    "Malfofa & Matrosa Pastries",
    "Malik Al Manja Sandwich - Shawarma - Juices",
    "Mallah bakery Bakery - Pastries",
    "Maloom Sandwich - Fast Food - Arabic - Breakfast",
    "Mama Ayoosh Desserts - Bakery",
    "Mama Mona Desserts",
    "Mama Sereh Arabic",
    "Mama Zenah Kabsa Saudi",
    "Mama nura Pastries",
    "Mamma Bunz cafe Desserts - Bakery - Beverages - Coffee",
    "Mamma Rona Italian - American - Pasta - Noodles",
    "Mamola Desserts - Arabic - Bakery - Coffee",
    "Mamolty Al Lazizah Desserts",
    "Managesh Al Reef Desserts - Pizza - Pastries - Meat",
    "Manaich Bakery - Pastries",
    "Manakesh Factory Fast Food - Desserts - Beverages - Pastries",
    "Manakish House Pizza - Pastries",
    "Manakish sandwich Sandwich - Bakery",
    "Manaqich Mayan Arabic - Pizza",
    "Manaqish Sandwich Fast Food - Pizza",
    "Mandi Al Hijaz Arabic - Saudi",
    "Mandi Al Marakh Fast Food - Arabic",
    "Mandi Al Sadah Arabic - Saudi",
    "Mandi Alhejaz Restaurant Arabic",
    "Mandi Alsharq Arabic - Saudi",
    "Mandi And Shaabyat zaman Saudi",
    "Mandi World Saudi, Arabic",
    "Mandi and Haneeth Asir Grill - Arabic - Saudi - Breakfast",
    "Mango Jizan Fruits & Vegetables",
    "Mango Talaat Desserts - Beverages",
    "Mangosha and Sandwicha Arabic - Pizza - Pastries",
    "Mangousha Wa Shai Fast Food - Arabic",
    "Manoosha Sandwich - Arabic - Lebanese - Pastries",
    "Manosha Lanoush Pastries",
    "Manosha Tannorin Arabic - Pizza - Pastries",
    "Manoshat Mansora Mart Arabic - Breakfast - Pastries",
    "Manousha Flavor Arabic - Pastries",
    "Manousha house Arabic - Bakery - Breakfast",
    "Manoushe Hut Fast Food - Arabic - Pastries",
    "Manousheat Haretna Arabic - Pizza",
    "Manqal Kebab Arabic",
    "Manqousheh House Sandwich - Bakery - Pizza",
    "Manqoushet Al Reef Sandwich - Pizza - Pastries",
    "Manqusha Jabal Lebanon Arabic - Pizza - Pastries",
    "Manqusha Layaly Wadi Al Sham Pizza - Pastries",
    "Mansho Grill Grill - Arabic",
    "Manti Mantu Arabic - Pastries",
    "Manto & Shatta Bakery - Pastries",
    "Manto Al Baraka Arabic",
    "Manto Rose Desserts - Coffee - Juices, Beverages - Coffee",
    "Manuel Bakery Fast Food",
    "Manushah Chef Halab Arabic - Pastries",
    "Maqlyat Hadramout Pastries",
    "Maraq & Meat Fast Food - Arabic",
    "Marble Slab Creamery Desserts - Ice Cream",
    "Marhaba Grill - Indian - Beverages, Arabic - Pastries, Arabic - Lebanese - Breakfast - Pastries, Arabic - Bakery - Pizza - Lebanese - Breakfast - Pastries, Arabic - Bakery, Desserts - Arabic - Bakery, Arabic - Breakfast",
    "Marhaba Bl Bait AlYamani Arabic",
    "Marina Fishes Arabic - Seafood",
    "Marino's Fast Food - Seafood",
    "Marjouha Grill - Shawarma - Lebanese",
    "Markh and Farkh Kitchens and Restaurants Arabic - Saudi - Meat",
    "Maro Bakery Desserts - Pastries",
    "Marrakech Moroccan Restaurant Desserts - Arabic - Beverages - Juices - Breakfast",
    "Marsa Al Hadrami Fish Restaurant Seafood",
    "Marsa Hadramout For Fishes Arabic - Seafood - Saudi",
    "Marsa Matrouh Roastery",
    "Maryam coffee Coffee",
    "Maryan Sweet Desserts",
    "Masala Restaurant Indian",
    "Masala zone Indian",
    "Masami Sushi Asian - Japanese",
    "Mashawi Al Khayma Sandwich - Fast Food - Grill - Arabic - Meat",
    "Mashoqah Shawarma - Breakfast",
    "Mashwiaat jamr Sandwich - Grill",
    "Mashwiat Awl Hikayatna Sandwich - Fast Food - Shawarma",
    "Mashwiyat Abdulwahab Hussein ALhalbi Grill - Arabic",
    "Masoub Al Soultan Arabic - Saudi - Breakfast",
    "Masoub Al Thamarat Fast Food - Arabic - Saudi - Breakfast",
    "Masoub Al Wisam Arabic",
    "Masoub Aldakkah Arabic - Breakfast",
    "Masoub Algadri Arabic - Saudi - Breakfast",
    "Masoub Altaweel Sandwich - Arabic - Saudi - Breakfast",
    "Masoub Alttaj Arabic - Saudi - Breakfast",
    "Masoub Asl Al Sultana Arabic - Beverages - Breakfast",
    "Masoub Baladi Arabic",
    "Masoub Diwan Arabic - Breakfast",
    "Masoub Jeddah Almomayaz Fast Food - Arabic - Breakfast",
    "Masoub Najd Arabic",
    "Masoub Nassib Arabic",
    "Masoub and Arika Al Nasim Arabic",
    "Masoubku Fast Food - Arabic - Breakfast",
    "Masouby ghayr Saudi - Breakfast",
    "Massoub And Motabaq Alhindawiah Arabic",
    "Master Sandwich Burgers",
    "Masub wafol aldayyafi Arabic - Breakfast",
    "Masuob Thread Fast Food - Arabic - Saudi - Breakfast",
    "Maswoub and Mutbaq Alwaziria Fast Food - Arabic - Breakfast",
    "Mataem Almathaq Albukhari Arabic - Beverages - Saudi",
    "Mataem Masoob Albadia Arabic - Breakfast",
    "Matahari Asian - Indonesian",
    "Matal Al Sadah Arabic - Beverages - Saudi",
    "Mateam Altaem Hikaya Sandwich - Fast Food - Shawarma",
    "Mathaq Al-Manoushah Arabic - Bakery - Vegetarian - Lebanese",
    "Mathaq Alfalafel Sandwich - Arabic - Falafel",
    "Mathaq Noon Desserts - Arabic - Pizza - Saudi",
    "Mausobkom Sandwich - Fast Food - Arabic - Saudi",




    ]

    autosaved_data = load_autosave()
    scraped_names = {entry['restaurant'].lower().strip() for entry in autosaved_data}
    failed_restaurants = []

    for rest_name in restaurants:
        if rest_name.lower().strip() in scraped_names:
            print(f"⏩ Skipping {rest_name}, already scraped.")
            continue

        print(f"\n🔍 Searching: {rest_name}")
        driver.get("https://www.google.com/")
        human_delay()

        try:
            search_input = WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.NAME, "q")))
            search_input.clear()
            search_input.send_keys(f"{rest_name} site:hungerstation.com")
            search_input.send_keys(Keys.RETURN)
        except:
            print(f"⚠️ Google search failed for: {rest_name}")
            failed_restaurants.append(rest_name)
            continue

        human_delay(2, 3)
        urls = get_clean_hungerstation_urls(driver)
        valid_url = None

        for url in urls:
            try:
                print(f"🧪 Checking: {url}")
                driver.get(url)
                time.sleep(2)
                close_popup(driver)

                WebDriverWait(driver, 8).until(
                    EC.presence_of_element_located((By.XPATH, "//section[@class='overflow-hidden']//h1"))
                )
                h1_text = driver.find_element(By.XPATH, "//section[@class='overflow-hidden']//h1").text.strip()
                main_name = extract_main_name(rest_name)

                if main_name.lower() in h1_text.lower():
                    print(f"✅ h1 matched (partial): '{main_name}' in '{h1_text}'")
                    valid_url = url
                    break
                else:
                    print(f"❌ No match: '{main_name}' not in '{h1_text}'")
            except Exception as e:
                print(f"⚠️ Invalid attempt: {e}")
                continue

        if not valid_url:
            print(f"❌ No valid menu found for: {rest_name}")
            failed_restaurants.append(rest_name)
            continue

        print(f"✅ Valid page: {valid_url}")
        driver.get(valid_url)
        time.sleep(2)
        close_popup(driver)
        html = driver.page_source
        tree = HTMLParser(html)

        all_items = []
        for section in tree.css("section[data-role='item-category']"):
            cat_title = section.css_first("h2")
            category_name = cat_title.text(strip=True) if cat_title else "Unknown Category"
            items = scrape_items_from_html_by_section(section.html, category_name)
            all_items.extend(items)

        print(f"🍽 Total menu items scraped: {len(all_items)}")
        autosaved_data.append({
            "restaurant": rest_name,
            "url": valid_url,
            "menu_items": all_items
        })
        save_autosave(autosaved_data)
        print(f"💾 Autosaved: {rest_name}")

    save_failed(failed_restaurants)
    driver.quit()
    print("\n✅ DONE: Data saved to autosave JSON.")

if __name__ == "__main__":
    main()
