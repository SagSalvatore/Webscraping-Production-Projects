import requests
import json
import time
import os

# === CONFIG ===
output_file = "foodi_all_branches.json"
latitude = 23.7463675
longitude = 90.4005959
limit = 12
max_pages = 100  # Or stop when no data

foodi_cookie = os.getenv("FOODI_COOKIE", "")
foodi_sxsrf = os.getenv("FOODI_SXSRF", "")

# === HEADERS ===
headers = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9,hi;q=0.8,es;q=0.7',
    'Connection': 'keep-alive',
    'Origin': 'https://foodibd.com',
    'Referer': 'https://foodibd.com/',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-site',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
    'sec-ch-ua': '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sxsrf': foodi_sxsrf,
    'Cookie': foodi_cookie
}

# === SCRAPING LOOP ===
all_branches = []
for page in range(1, max_pages + 1):
    params = {
        'orderType': '1',
        'userLatitude': latitude,
        'userLongitude': longitude,
        'page': page,
        'limit': limit
    }
    print(f"Fetching page {page} ...")
    try:
        res = requests.get('https://api.foodibd.com/restaurants-go/api/v1/all-branch', headers=headers, params=params)
        res.raise_for_status()
        json_data = res.json()

        # Extract only the 'branches' array
        branches = json_data.get("data", {}).get("branches", [])
        if not branches:
            print("No more branches.")
            break

        all_branches.extend(branches)
        time.sleep(1)

    except Exception as e:
        print(f"Error on page {page}: {e}")
        break

# === SAVE FINAL OUTPUT ===
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(all_branches, f, ensure_ascii=False, indent=2)

print(f"\n✅ Saved {len(all_branches)} restaurant branches to {output_file}")
