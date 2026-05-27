import sys
sys.stdout.reconfigure(encoding='utf-8')

import time
import random
import os
import re
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ========= REGION SETUP =========
region = "Rakmall"
zone_url = "https://food.noon.com/zone/RAK%20Mall/?page=1"

# ========= FOLDER SETUP =========
SAVE_FOLDER = os.getenv("NOON_SELENIUM_SAVE_FOLDER", "noon_selenium_output")
os.makedirs(SAVE_FOLDER, exist_ok=True)

def sanitize_filename(name):
    name = re.sub(r"[\\/:*?\"<>|]", "", name)
    return re.sub(r"\s+", "_", name.strip())

# ========= SELENIUM SETUP =========
options = Options()
options.add_argument("--start-maximized")
options.add_argument("--incognito")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument("--disable-infobars")
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36")

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
wait = WebDriverWait(driver, 20)

# ========= OPEN PAGE =========
print("\n🌐 Loading Noon homepage...")
driver.get("https://food.noon.com/")
time.sleep(5)

print(f"➡️ Navigating to {region} region...")
driver.execute_script(f"window.location.href='{zone_url}'")
time.sleep(7)

# ========= MAIN LOOP =========
print("🔍 Starting scrape — every card, no skip!")

def scroll_to_load_all_cards():
    try:
        container = wait.until(EC.presence_of_element_located((By.XPATH, "//div[@class='sc-6b86f655-5 isgSBB']")))
        last_count = 0
        while True:
            cards = driver.find_elements(By.XPATH, "//div[@class='sc-6b86f655-5 isgSBB']/div")
            if len(cards) == last_count:
                break
            last_count = len(cards)
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", container)
            time.sleep(1.2)
    except Exception as e:
        print(f"⚠️ Scroll error: {e}")

scroll_to_load_all_cards()

cards = driver.find_elements(By.XPATH, "//div[@class='sc-6b86f655-5 isgSBB']/div")
print(f"🔢 Found {len(cards)} cards. Beginning extraction...")

for i in range(len(cards)):
    try:
        cards = driver.find_elements(By.XPATH, "//div[@class='sc-6b86f655-5 isgSBB']/div")
        card = cards[i]

        driver.execute_script("arguments[0].scrollIntoView();", card)
        ActionChains(driver).move_to_element(card).pause(0.5).click().perform()
        time.sleep(3)

        # ==== Extract Details ====
        try:
            title = wait.until(EC.presence_of_element_located((By.XPATH, "//h1"))).text.strip()
        except:
            title = f"Unknown_{i}"

        try:
            cuisines = driver.find_element(By.XPATH, "//p[@class='cuisines']").text.strip()
        except:
            cuisines = "Not Found"

        location = "Not Found"

        # ==== Try info icon ====
        try:
            print("🔁 Trying 'Info' icon...")
            info_button = driver.find_element(By.XPATH, "//img[@alt='info']")
            if info_button.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView(true);", info_button)
                driver.execute_script("arguments[0].click();", info_button)
                time.sleep(2)
        except:
            # ==== Try +More Info absolute XPath ====
            try:
                print("✅ Clicking 'More Info' using absolute XPath...")
                more_info_btn = driver.find_element(By.XPATH, "/html/body/div[1]/section/div/div/div[2]/div/button")
                driver.execute_script("arguments[0].scrollIntoView(true);", more_info_btn)
                driver.execute_script("arguments[0].click();", more_info_btn)
                time.sleep(2)
            except:
                print("📛 Neither Info icon nor More Info button clickable")

        # ==== Extract location using YOUR XPATH ====
        try:
            location_element = wait.until(EC.presence_of_element_located((By.XPATH, "/html/body/div[3]/div[2]/div/div/div[4]/div/p")))
            location = location_element.text.strip()
        except Exception as e:
            print(f"📛 Final location extraction failed: {e}")
            location = "Not Found"

        url = driver.current_url
        url_id = url.split("/")[-1]
        filename = sanitize_filename(f"{region}_{title}_{url_id}_{i}") + ".txt"

        with open(os.path.join(SAVE_FOLDER, filename), "w", encoding="utf-8") as f:
            f.write(f"Restaurant Name: {title}\n")
            f.write(f"Cuisines: {cuisines}\n")
            f.write(f"Location: {location}\n")
            f.write(f"URL: {url}\n")

        print(f"[{i+1}] ✅ Saved: {title}")
        driver.back()
        time.sleep(2)
        scroll_to_load_all_cards()

    except Exception as e:
        print(f"[{i+1}] ❌ Error while scraping: {e}")
        try:
            driver.back()
        except:
            pass
        time.sleep(2)
        continue

driver.quit()
print("🎉 Scraping complete for full page!")


# The code below is commented out to avoid execution in this environment.





























# import sys
# sys.stdout.reconfigure(encoding='utf-8')

# import time
# import random
# import os
# import re
# from selenium import webdriver
# from selenium.webdriver.chrome.service import Service
# from selenium.webdriver.chrome.options import Options
# from selenium.webdriver.common.by import By
# from selenium.webdriver.common.action_chains import ActionChains
# from webdriver_manager.chrome import ChromeDriverManager
# from selenium.webdriver.support.ui import WebDriverWait
# from selenium.webdriver.support import expected_conditions as EC

# # ========= FOLDER SETUP =========
# SAVE_FOLDER = os.getenv("NOON_SELENIUM_SAVE_FOLDER", "noon_selenium_output")
# os.makedirs(SAVE_FOLDER, exist_ok=True)

# def sanitize_filename(name):
#     name = re.sub(r"[\\/:*?\"<>|]", "", name)
#     return re.sub(r"\s+", "_", name.strip())

# # ========= SELENIUM SETUP =========
# options = Options()
# options.add_argument("--start-maximized")
# options.add_argument("--incognito")
# options.add_argument("--disable-blink-features=AutomationControlled")
# options.add_argument("--disable-infobars")
# options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
# wait = WebDriverWait(driver, 20)

# # ========= CONTROL VARIABLES =========
# START_PAGE = 1
# BASE_URL = "https://food.noon.com/zone/Al%20Muwaiji/"
# scraped_files = set(os.listdir(SAVE_FOLDER))


# # ========= LOOP THROUGH PAGES =========
# current_page = START_PAGE
# while True:
#     print(f"\n📄 Opening Page: {current_page}")
#     driver.get(BASE_URL + str(current_page))
#     time.sleep(5)

#     try:
#         scroll_container = wait.until(EC.presence_of_element_located((By.XPATH, "//div[@class='sc-6b86f655-5 isgSBB']")))
#         for _ in range(15):
#             driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", scroll_container)
#             time.sleep(random.uniform(1.0, 1.5))
#     except Exception as e:
#         print(f"❌ Scroll error: {e}")
#         continue

#     try:
#         restaurant_cards = driver.find_elements(By.XPATH, "//div[@class='sc-6b86f655-5 isgSBB']/div")
#         print(f"🔍 Found {len(restaurant_cards)} restaurants on page {current_page}")
#     except Exception as e:
#         print(f"❌ Could not locate restaurant cards: {e}")
#         continue

#     for i in range(len(restaurant_cards)):
#         try:
#             wait.until(EC.presence_of_element_located((By.XPATH, "//div[@class='sc-6b86f655-5 isgSBB']")))
#             scroll_container = driver.find_element(By.XPATH, "//div[@class='sc-6b86f655-5 isgSBB']")
#             driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", scroll_container)
#             time.sleep(1)

#             restaurant_cards = driver.find_elements(By.XPATH, "//div[@class='sc-6b86f655-5 isgSBB']/div")

#             if i >= len(restaurant_cards):
#                 print(f"[{i+1}] ⚠️ Card index {i} out of range. Skipping.")
#                 continue

#             card = restaurant_cards[i]
#             driver.execute_script("arguments[0].scrollIntoView();", card)
#             time.sleep(random.uniform(1.2, 2))
#             ActionChains(driver).move_to_element(card).click().perform()
#             time.sleep(3)

#             # === Extract Title ===
#             try:
#                 title = wait.until(EC.presence_of_element_located((By.XPATH, "//h1"))).text.strip()
#             except:
#                 title = "Not Found"

#             filename = sanitize_filename(title) + ".txt"
#             if filename in scraped_files:
#                 print(f"[{i+1}] ⏭️ Already scraped: {title}")
#                 driver.back()
#                 time.sleep(2)
#                 continue

#             # === Extract Cuisines ===
#             try:
#                 cuisines = driver.find_element(By.XPATH, "//p[@class='cuisines']").text.strip()
#             except:
#                 cuisines = "Not Found"

#             # === Extract Location using JavaScript (from your provided path) ===
#             try:
#                 info_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//img[@alt='info']")))
#                 driver.execute_script("arguments[0].click();", info_button)
#                 time.sleep(2)

#                 location = driver.execute_script("""
#                     try {
#                         let el = document.querySelector("div[class='sc-224c8ea1-7 dlwfFR'] div div p");
#                         return el ? el.innerText.trim() : "Not Found";
#                     } catch (e) {
#                         return "Not Found";
#                     }
#                 """)
#             except Exception as e:
#                 print(f"📛 JS Location not found: {e}")
#                 location = "Not Found"

#             url = driver.current_url

#             # === Save to .txt file ===
#             with open(os.path.join(SAVE_FOLDER, filename), "w", encoding="utf-8") as f:
#                 f.write(f"Restaurant Name: {title}\n")
#                 f.write(f"Cuisines: {cuisines}\n")
#                 f.write(f"Location: {location}\n")
#                 f.write(f"URL: {url}\n")

#             print(f"[{i+1}] ✅ Saved: {title}")
#             scraped_files.add(filename)

#             try:
#                 driver.back()
#             except:
#                 print("🔄 Session expired, refreshing page.")
#                 driver.get(BASE_URL + str(current_page))
#                 time.sleep(5)
#                 continue

#             time.sleep(random.uniform(2, 3))

#         except Exception as e:
#             print(f"[{i+1}] ❌ Error clicking restaurant: {e}")
#             try:
#                 driver.back()
#             except:
#                 pass
#             time.sleep(2)
#             continue

#     # ===== Next Page Navigation =====
#     try:
#         next_btn = driver.find_element(By.XPATH, "//a[@aria-label='Next page']")
#         if "disabled" in next_btn.get_attribute("class"):
#             print("✅ All pages complete.")
#             break
#         else:
#             current_page += 1
#     except:
#         print("✅ No next page button. Done.")
#         break

# driver.quit()
# print("🎉 Done scraping Noon Food!")









