import requests
import json
import os

# Replace this with any valid Noon restaurant URL
url = "https://food.noon.com/uae-en/outlet/SWTSPCWBCM-Sweet%20&%20Spicy/"

# Extract outlet code
outlet_code = url.rstrip("/").split("/")[-1].split("-")[0]

api_url = "https://food.noon.com/_svc/mp-food-api-mpnoon/consumer/restaurant/outlet/details/guest"
cookies_str = os.getenv("NOON_COOKIE", "")
cookies = {c.split("=")[0]: c.split("=", 1)[1] for c in cookies_str.strip().split("; ") if "=" in c}
headers = {
    "authority": "food.noon.com",
    "accept": "application/json",
    "content-type": "application/json",
    "origin": "https://food.noon.com",
    "referer": url,
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}

payload = {"outletCode": outlet_code}

res = requests.post(api_url, json=payload, headers=headers, cookies=cookies)
data = res.json()["data"]

# Extract metadata
restaurant_name = data.get("name", "N/A")
address = data.get("address", "N/A")

# 🧠 Support both formats
menu_data = None
if "menus" in data and data["menus"]:
    menu_data = data["menus"][0].get("menuData", {})
elif "menu" in data:
    menu_data = data["menu"]

# Parse if valid
output = {
    "restaurant_name": restaurant_name,
    "address": address,
    "url": url,
    "menu": []
}

if menu_data and "categories" in menu_data and "items" in menu_data:
    categories = menu_data["categories"]
    items = {item["itemCode"]: item for item in menu_data["items"] if item.get("itemType") == "main"}

    for cat in categories:
        cat_items = []
        for item_code in cat.get("items", []):
            item = items.get(item_code)
            if item:
                cat_items.append({
                    "name": item["name"],
                    "description": item.get("itemDesc", ""),
                    "price": f"AED {item['price']:.2f}"
                })
        if cat_items:
            output["menu"].append({
                "category": cat["name"],
                "items": cat_items
            })

# Save to JSON
with open("sweet_and_spicy_menu.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"✅ Scraped: {restaurant_name} | Categories: {len(output['menu'])}")
