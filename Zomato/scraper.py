"""
Zomato Abu Dhabi Restaurant Scraper — curl_cffi Edition (Fixed)
Outputs: zomato_abudhabi.json + zomato_abudhabi.csv
"""

import json
import time
import random
import pandas as pd
from curl_cffi import requests

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
BASE_URL    = "https://www.zomato.com/webroutes/search/home"
OUTPUT_JSON = "zomato_abudhabi.json"
OUTPUT_CSV  = "zomato_abudhabi.csv"
MAX_PAGES   = 50
DELAY_MIN   = 1.5
DELAY_MAX   = 3.0
DEBUG       = True   # Set False after first successful run

# ─────────────────────────────────────────────
# COOKIES
# ─────────────────────────────────────────────
COOKIES = {
    'fre': '0',
    'rd': '1380000',
    'zl': 'en',
    'fbtrack': '8ad583e2bc1a860d74d755cf0d29ce0a',
    'PHPSESSID': '19139838d095a107834f44324a0f25eb',
    'csrf': '578e667e3db46e27f3399b716ed8ee44',
    'ak_bmsc': '7B20DD6E0362E4D6E672A01922EC825C~000000000000000000000000000000~YAAQD3BWuHjE67OeAQAA6GPKuwBy8MXy2l6jW0uxbzIDfgKvQLgoapOGdiUGelLXaMnvGrwXfrc80Q/shYjiI5SeJRni0/H/zeJiXqwtJS/nfer7QhdRM0zlG0a/lYTKcU/yC+gHbIPj+8X/nCyleDWR8g4ACyE7GigwPTGCvJqt2aM4dDSDPgR9Uh3fapqUm4DxNyP3JTiCQI1twXKSPZPfmvuopb1JJKgCtgKbuo6VzY11xJR1pwP+F591Na0qRRNXi4NS0648NfwifJq9MScO1yZBEVJtC4kiZjJAOU6CCNBIci1v8go/RLBpKU3j0hxNlPxvMEi9jloeYwj5XP0Vb/JT8o5GBrACEUDkJcmcVU4Hyolz+7nUwDcsjKqyrdmx4kTR3bs/TOCp',
    'zacpol': '1',
    'fbcity': '57',
    'ltv': '57',
    'lty': '57',
    'locus': '%7B%22addressId%22%3A0%2C%22lat%22%3A24.404153%2C%22lng%22%3A54.500334%2C%22cityId%22%3A57%2C%22ltv%22%3A57%2C%22lty%22%3A%22city%22%2C%22fetchFromGoogle%22%3Afalse%2C%22dszId%22%3A5567%2C%22fen%22%3A%22Abu+Dhabi%22%7D',
    '_abck': 'FE7180D431AFA15221193762154B0E23~-1~YAAQbmo3F+d0e5SeAQAAvRzTuxAduqzb6P/Q5yfXUNMfoYOnzAas1jCGX11Ilqc1EHRTCxQlAKf4JsdmxVVdgoXOhiNrjlLakozW1geUxHA6nHIIWltcictFqd6avLiIanjaGKFnW8Sa6SSqBkgFGESuqvCELeQvhmLeQ4n5P5nOAdutUIOqwl3pH+xp7SQPXbghaluv99+fAuGcbZspjr2yNx+PjcIRQNc4CkgBRaysJe28sPuPrLfLGaIUX6OcJfVtxANjAFBQlDBNQvcPX07up0diJQpn402VuIeo+idUkSMPVTCkvrB7ruFOh2VcdNzsiPrU1lLbkgc+pk0/vdCf+1DERBE7XIMtW3uPM5fpaOHGcEqDhoNP0JS4aXUsyS1F5cw9S7wpu4f+jDky6FGt97qdoXBe7fHJHoByduc9qPFI/UB9b/ahqgxa6wQzKq1L3ep3feXQlZRA1ZtqllKsxyH5~-1~-1~-1~-1~-1',
    'AWSALBTG': 'vVkXByrJB0rm8kSpCGidOTlco8cmYUxSrHNuWEu+7iPPYULRugUAsYpD0SxsL8y2Gq9LwzldHetPXpNG22QUvVxvoshIpgZXU+b2oegtLyq7xMdd+ZfYrxP8VgyYSoSDxbUEd/u9an2t6UI0gHCcZHUhIROYgJ4WQE81DYRDVRP2',
    'AWSALBTGCORS': 'vVkXByrJB0rm8kSpCGidOTlco0cmYUxSrHNuWEu+7iPPYULRugUAsYpD0SxsL8y2Gq9LwzldHetPXpNG22QUvVxvoshIpgZXU+b2oegtLyq7xMdd+ZfYrxP8VgyYSoSDxbUEd/u9an2t6UI0gHCcZHUhIROYgJ4WQE81DYRDVRP2',
}

HEADERS = {
    'accept': '*/*',
    'accept-language': 'en-US,en;q=0.9',
    'content-type': 'application/json',
    'origin': 'https://www.zomato.com',
    'priority': 'u=1, i',
    'referer': 'https://www.zomato.com/abudhabi/restaurants',
    'sec-ch-ua': '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
    'x-zomato-csrft': '578e667e3db46e27f3399b716ed8ee44',
}


# ─────────────────────────────────────────────
# PAYLOAD BUILDER
# ─────────────────────────────────────────────
def build_payload(page: int, solr_offset: int, search_id: str) -> dict:
    filters = {
        "searchMetadata": {
            "previousSearchParams": json.dumps({
                "PreviousSearchFilter": [
                    '{"category_context":"go_out_home"}',
                    "",
                    '{"context":"dineout_home"}'
                ]
            }),
            "postbackParams": json.dumps({
                "search_id": search_id,
                "solr_offset": solr_offset,
                "page": page,
                "total_restaurants_shown": solr_offset,
                "total_results_shown": solr_offset,
            }),
            "totalResults": 4807,
            "hasMore": True,
            "getInactive": False
        },
        "dineoutAdsMetaData": {},
        "appliedFilter": [
            {"filterType": "category_sheet", "filterValue": "go_out_home",
             "isHidden": True, "isApplied": True, "postKey": '{"category_context":"go_out_home"}'},
            {"filterType": "context", "filterValue": "dineout_home",
             "isHidden": True, "isApplied": True, "postKey": '{"context":"dineout_home"}'}
        ],
        "urlParamsForAds": {}
    }
    return {
        "context": "dineout",
        "filters": json.dumps(filters),
        "addressId": 0, "entityId": 57, "entityType": "city",
        "locationType": "", "isOrderLocation": 1, "cityId": 57,
        "latitude": "24.4041530000000000", "longitude": "54.5003340000000000",
        "userDefinedLatitude": 24.404153, "userDefinedLongitude": 54.500334,
        "entityName": "Abu Dhabi", "orderLocationName": "Abu Dhabi",
        "cityName": "Abu Dhabi", "countryId": 214, "countryName": "UAE",
        "displayTitle": "Abu Dhabi", "o2Serviceable": True,
        "placeId": "5567", "cellId": "4494101349071323136",
        "deliverySubzoneId": 5567, "placeType": "DSZ", "placeName": "Abu Dhabi",
        "isO2City": True, "fetchFromGoogle": False, "fetchedFromCookie": True,
        "isO2OnlyCity": False, "address_template": [], "otherRestaurantsUrl": "",
    }


# ─────────────────────────────────────────────
# DEBUG: Print all top-level keys and nested structure
# ─────────────────────────────────────────────
def debug_structure(data: dict, depth=0, max_depth=3):
    """Recursively print key structure of response."""
    indent = "  " * depth
    for key, val in data.items():
        if isinstance(val, dict):
            print(f"{indent}[dict]  {key}  ({len(val)} keys)")
            if depth < max_depth:
                debug_structure(val, depth + 1, max_depth)
        elif isinstance(val, list):
            print(f"{indent}[list]  {key}  (len={len(val)})")
            if val and isinstance(val[0], dict) and depth < max_depth:
                print(f"{indent}  [first item keys]: {list(val[0].keys())}")
        else:
            preview = str(val)[:80] if val else ""
            print(f"{indent}[val]   {key}: {preview}")


# ─────────────────────────────────────────────
# DEEP CARD FINDER — recursively find all card lists
# ─────────────────────────────────────────────
def find_all_cards(obj, found=None, depth=0):
    """Recursively walk JSON to find any list with restaurant card objects."""
    if found is None:
        found = []
    if depth > 6:
        return found

    if isinstance(obj, dict):
        # A card object that has a restaurant "info" block
        if "info" in obj and isinstance(obj["info"], dict):
            info = obj["info"]
            # Only yield if it looks like a restaurant (has name + id)
            if info.get("name") and (info.get("id") or info.get("resId")):
                found.append(obj)
                return found  # Don't recurse further into this card

        for val in obj.values():
            find_all_cards(val, found, depth + 1)

    elif isinstance(obj, list):
        for item in obj:
            find_all_cards(item, found, depth + 1)

    return found


# ─────────────────────────────────────────────
# HAS MORE — scan entire response for pagination signal
# ─────────────────────────────────────────────
def extract_has_more_and_search_id(data: dict, current_search_id: str):
    """Walk full response to find hasMore + updated search_id."""
    has_more = False
    search_id = current_search_id

    raw = json.dumps(data)

    # Check hasMore flag anywhere in raw JSON
    if '"hasMore":true' in raw or '"hasMore": true' in raw:
        has_more = True

    # Try to extract search_id from postbackParams
    import re
    matches = re.findall(r'"search_id"\s*:\s*"([a-f0-9\-]{36})"', raw)
    if matches:
        search_id = matches[-1]  # take last occurrence (most updated)

    return has_more, search_id


# ─────────────────────────────────────────────
# PARSER — extract restaurant fields from card
# ─────────────────────────────────────────────
def parse_card(card: dict) -> dict | None:
    info = card.get("info", {})
    if not info or not info.get("name"):
        return None

    location = info.get("location", {})
    lat  = location.get("latitude")  or info.get("latitude")
    lng  = location.get("longitude") or info.get("longitude")

    # Build URL
    url_slug = info.get("url", "")
    full_url = (
        f"https://www.zomato.com{url_slug}" if url_slug.startswith("/")
        else url_slug if url_slug.startswith("http")
        else f"https://www.zomato.com/abudhabi/{info.get('nameKey', '')}"
    )

    # Rating — handle both flat and nested structures
    rating_obj = info.get("rating", {})
    avg_rating = (
        info.get("avgRating")
        or (rating_obj.get("aggregate_rating") if isinstance(rating_obj, dict) else None)
        or info.get("avgRatingV2")
    )
    votes = (
        info.get("totalRatingsString")
        or (rating_obj.get("votes") if isinstance(rating_obj, dict) else None)
        or info.get("ratingCountV2")
    )

    return {
        "restaurant_id":  info.get("id") or info.get("resId"),
        "name":           info.get("name"),
        "url":            full_url,
        "latitude":       lat,
        "longitude":      lng,
        "cuisines":       info.get("cuisines", ""),
        "avg_rating":     avg_rating,
        "votes":          votes,
        "cost_for_two":   info.get("costForTwo") or info.get("cost"),
        "locality":       location.get("localityName") or location.get("locality"),
        "city":           location.get("cityName", "Abu Dhabi"),
        "address":        location.get("address", ""),
        "is_dineout":     info.get("isDineout", True),
        "is_delivering":  info.get("isDelivery", False),
    }


# ─────────────────────────────────────────────
# MAIN SCRAPER
# ─────────────────────────────────────────────
def scrape():
    session = requests.Session()
    all_restaurants = []
    search_id = "16994bb9-5eff-4870-a2dd-2e899bd91446"
    page = 1
    solr_offset = 0

    print("=" * 55)
    print("  Zomato Abu Dhabi Scraper — curl_cffi (Fixed)")
    print("=" * 55)

    while page <= MAX_PAGES:
        payload = build_payload(page, solr_offset, search_id)

        try:
            response = session.post(
                BASE_URL,
                cookies=COOKIES,
                headers=HEADERS,
                json=payload,
                impersonate="chrome124",
                timeout=30,
            )
        except Exception as e:
            print(f"[!] Request error on page {page}: {e}")
            break

        print(f"\n[→] Page {page} | HTTP {response.status_code}")

        if response.status_code != 200:
            print(f"[!] Non-200 response. Body snippet:\n{response.text[:500]}")
            break

        try:
            data = response.json()
        except Exception:
            print(f"[!] JSON decode error:\n{response.text[:300]}")
            break

        # ── DEBUG: Print structure on first page ──────
        if DEBUG and page == 1:
            print("\n📋 TOP-LEVEL RESPONSE STRUCTURE:")
            print(f"   Root keys: {list(data.keys())}")
            print()
            debug_structure(data, max_depth=2)
            # Also save raw JSON for manual inspection
            with open("zomato_page1_raw.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print("\n💾 Raw page 1 response saved to: zomato_page1_raw.json")
            print("   (Inspect this file if 0 restaurants found)\n")

        # ── Find restaurant cards anywhere in response ─
        cards = find_all_cards(data)
        print(f"   Found {len(cards)} restaurant cards")

        if not cards:
            print("[✓] No cards found. Checking if response has any known structure...")
            # Last resort: search for any 'name' fields in a list
            sections = data.get("sections", data.get("pageData", data))
            for key in sections if isinstance(sections, dict) else {}:
                val = sections[key]
                if isinstance(val, dict) and "cards" in val:
                    print(f"   Found 'cards' under sections.{key} — add custom parser for this key.")
            break

        # Parse each card
        batch = []
        for card in cards:
            parsed = parse_card(card)
            if parsed:
                batch.append(parsed)

        all_restaurants.extend(batch)
        solr_offset += len(batch)

        has_more, search_id = extract_has_more_and_search_id(data, search_id)
        print(f"   Parsed: {len(batch)} | Total: {len(all_restaurants)} | hasMore: {has_more}")

        if not has_more:
            print("[✓] No more pages. Done.")
            break

        page += 1
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    # ── Deduplicate ───────────────────────────────────
    seen, unique = set(), []
    for r in all_restaurants:
        rid = r.get("restaurant_id")
        if rid and rid not in seen:
            seen.add(rid)
            unique.append(r)
        elif not rid:
            unique.append(r)

    print(f"\n[✓] Total unique restaurants: {len(unique)}")

    # ── Save outputs ──────────────────────────────────
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)
    print(f"[✓] JSON → {OUTPUT_JSON}")

    if unique:
        pd.DataFrame(unique).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
        print(f"[✓] CSV  → {OUTPUT_CSV}")

    return unique


if __name__ == "__main__":
    scrape()