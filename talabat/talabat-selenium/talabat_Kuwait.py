import time
import os
import re
import random
import sys
sys.stdout.reconfigure(encoding='utf-8')
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ========== UTILS ==========
def sanitize_filename(name):
    name = re.sub(r"[\\/:*?\"<>|]", "", name)
    return re.sub(r"\s+", "_", name.strip())

# ========== CONFIG ==========
options = Options()
options.add_argument("--start-maximized")
options.add_argument("--incognito")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
wait = WebDriverWait(driver, 15)

# ========== OUTPUT FOLDER ==========
output_folder = "talabat_Kuwait"
os.makedirs(output_folder, exist_ok=True)

# ========== SCRAPING ==========
base_url = "https://www.talabat.com/kuwait/restaurants"
driver.get(base_url)
time.sleep(5)

for page in range(26, 138):  # 137 pages confirmed
    try:
        print(f"\n📄 Scraping Page {page}...")
        driver.get(f"{base_url}?page={page}")
        time.sleep(random.uniform(3, 6))

        restaurant_cards = driver.find_elements(By.XPATH, "//a[@data-testid='vendor-a']")
        restaurant_urls = [card.get_attribute("href") for card in restaurant_cards if card.get_attribute("href")]

        print(f"🔗 Found {len(restaurant_urls)} restaurants on page {page}")

        for url in restaurant_urls:
            try:
                print(f"\n➡️ Visiting: {url}")
                driver.get(url)
                time.sleep(random.uniform(3, 5))

                # === Name ===
                try:
                    name = driver.find_element(By.XPATH, "//h1[@data-testid='brand-name']").text.strip()
                except:
                    name = "N/A"

                # === Location ===
                try:
                    paragraphs = driver.find_elements(By.XPATH, "//div[@class='markdown-rich-text-block']//p")
                    location = " ".join([p.text.strip() for p in paragraphs if p.text.strip()])
                except:
                    location = "N/A"

                # === Cuisines ===
                try:
                    cuisines = driver.find_element(By.XPATH, "//p[@data-testid='brand-cusine']").text.strip()
                except:
                    cuisines = "N/A"

                # === Save to file ===
                clean_name = sanitize_filename(name)
                file_path = os.path.join(output_folder, f"Page{page}_{clean_name[:40]}.txt")

                if os.path.exists(file_path):
                    print(f"⏭️ Already scraped: {file_path}")
                    continue

                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(f"Page: {page}\n")
                    f.write(f"URL: {url}\n")
                    f.write(f"Restaurant: {name}\n")
                    f.write(f"Location: {location}\n")
                    f.write(f"Cuisines: {cuisines}\n")

                print(f"✅ Saved to {file_path}")
                time.sleep(random.uniform(2, 4))

            except Exception as e:
                print(f"⚠️ Error scraping {url}: {e}")
                continue

    except Exception as e:
        print(f"🚨 Error navigating to page {page}: {e}")
        break

driver.quit()
print("\n🏁 Scraping completed.")























































































# import time
# import os
# import re
# import random
# import sys
# sys.stdout.reconfigure(encoding='utf-8')
# from selenium import webdriver
# from selenium.webdriver.chrome.service import Service
# from webdriver_manager.chrome import ChromeDriverManager
# from selenium.webdriver.common.by import By
# from selenium.webdriver.chrome.options import Options
# from selenium.webdriver.common.keys import Keys
# from selenium.webdriver.support.ui import WebDriverWait
# from selenium.webdriver.support import expected_conditions as EC
# import pandas as pd

# # ========== UTILS ==========
# def sanitize_filename(name):
#     name = re.sub(r"[\\/:*?\"<>|]", "", name)
#     return re.sub(r"\s+", "_", name.strip())

# # ========== CONFIG ==========
# options = Options()
# options.add_argument("--start-maximized")
# options.add_argument("--incognito")
# options.add_argument("--disable-blink-features=AutomationControlled")
# options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

# # ========== SCRAPING ==========
# base_url = "https://www.talabat.com/kuwait/restaurants"
# driver.get(base_url)
# time.sleep(5)

# for page in range(1, 138):  # Adjust number of pages if needed
#     try:
#         print(f"\nScraping Page {page}...")
#         driver.get(f"{base_url}?page={page}")
#         time.sleep(random.uniform(3, 6))

#         restaurant_cards = driver.find_elements(By.XPATH, "//a[@data-testid='vendor-a']")
#         restaurant_urls = [card.get_attribute("href") for card in restaurant_cards]

#         print(f"Found {len(restaurant_urls)} restaurants")

#         for url in restaurant_urls:
#             try:
#                 print(f"\nVisiting: {url}")
#                 driver.get(url)
#                 time.sleep(random.uniform(3, 5))

#                 # Extract name
#                 try:
#                     name = driver.find_element(By.XPATH, "//h1[@data-testid='brand-name']").text.strip()
#                 except:
#                     name = "N/A"

#                 # Extract location
#                 try:
#                     paragraphs = driver.find_elements(By.XPATH, "//div[@class='markdown-rich-text-block']//p")
#                     location = " ".join([p.text.strip() for p in paragraphs if p.text.strip()])
#                 except:
#                     location = "N/A"

#                 # Extract cuisines
#                 try:
#                     cuisines = driver.find_element(By.XPATH, "//p[@data-testid='brand-cusine']").text.strip()
#                 except:
#                     cuisines = "N/A"

#                 # Debug
#                 print(f"Page Number: {page}")
#                 print("Name:", name)
#                 print("Location:", location)
#                 print("Cuisines:", cuisines)

#                 # Save to file
#                 safe_name = sanitize_filename(name)
#                 file_path = f"{safe_name}_info.txt"
#                 with open(file_path, "w", encoding="utf-8") as f:
#                     f.write(f"Page: {page}\n")
#                     f.write(f"URL: {url}\n")
#                     f.write(f"Restaurant: {name}\n")
#                     f.write(f"Location: {location}\n")
#                     f.write(f"Cuisines: {cuisines}\n")

#                 print(f"Saved to {file_path}")
#                 time.sleep(random.uniform(2, 4))

#             except Exception as e:
#                 print(f"Error scraping {url}: {e}")
#                 continue

#     except Exception as e:
#         print(f"Error navigating to page {page}: {e}")
#         break

# driver.quit()
# print("\n Scraping completed.")
