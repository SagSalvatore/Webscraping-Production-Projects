import json
import time
import requests
import os

# === CONFIG ===
output_file = "elmenus_sheikh_zayed_listings_raw.json"
max_pages = 100
page = 1
all_results = []

# === AUTH TOKEN ===
auth_token = os.getenv("ELMENUS_AUTH_TOKEN", "")
web_refresh_token = os.getenv("ELMENUS_WEB_REFRESH_TOKEN", "")
if not auth_token:
    raise RuntimeError("Set ELMENUS_AUTH_TOKEN before running.")

# === COOKIES ===
cookies = {
    'payload': '1olkpy11mbuf7i4h',
    'webRefreshToken': web_refresh_token,
    'Authorization': auth_token,
    'lang': 'EN',
    'userCity': '35185821-2224-11e8-924e-0242ac110011',
    'userArea': '374b841a-2224-11e8-924e-0242ac110011',
    'userZone': 'fa681a9c-fc6a-11e8-bbd8-0a586460020d'
}

# === HEADERS ===
headers = {
    'Accept': 'application/json',
    'Authorization': f'Bearer {auth_token}',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Referer': 'https://www.elmenus.com/cairo/delivery/sheikh-zayed/features-order-online',
    'Client-Model': 'WEB',
    'Client-Version': '5',
    'X-Client-Id': '0417b144-0f3f-11e8-87cc-0242ac110002',
    'X-Device-Id': '1olkpy11mbuf7i4h',
    'lang': 'EN'
}

# === PARAMS for Sheikh Zayed ===
params_template = {
    'includeDiscount': 'true',
    'area': '374b841a-2224-11e8-924e-0242ac110011',
    'sort': 'POPULAR',
    'zone': 'fa681a9c-fc6a-11e8-bbd8-0a586460020d',
    'pageSize': '12'
}

# === SCRAPING LOOP ===
while page <= max_pages:
    print(f"📦 Fetching page {page}...")
    retries = 0
    max_retries = 3

    while retries < max_retries:
        try:
            response = requests.get(
                "https://www.elmenus.com/2.0/discovery/delivery/search",
                headers=headers,
                cookies=cookies,
                params={**params_template, "page": str(page)},
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                restaurants = data.get("data", [])

                if not restaurants:
                    print("✅ No more restaurants. Stopping.")
                    page = max_pages + 1
                    break

                all_results.extend(restaurants)
                print(f"✅ Page {page}: {len(restaurants)} listings added.")

                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(all_results, f, ensure_ascii=False, indent=2)

                page += 1
                time.sleep(1.5)
                break

            elif response.status_code == 500:
                retries += 1
                print(f"⚠️ Retry {retries}/3 for page {page} due to HTTP 500")
                time.sleep(3)

            else:
                print(f"❌ HTTP Error: {response.status_code}")
                page = max_pages + 1
                break

        except requests.exceptions.RequestException as e:
            print(f"❌ Request error on page {page}: {e}")
            retries += 1
            time.sleep(3)

    if retries == max_retries:
        print(f"❌ Skipping page {page} after 3 failed retries.")
        page += 1

print(f"\n🎉 Done! Extracted {len(all_results)} listings to {output_file}")
