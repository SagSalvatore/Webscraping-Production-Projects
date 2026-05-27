"""
Gateau Finland Store Scraper

Scrapes store locations from https://www.gateau.fi/myymalat/
Uses hidden API endpoint that returns structured JSON data.

API returns all store data directly including:
- name, streetAddress, postalCode, city, country
- latitude, longitude
- email, phone (in additionalContactInfomation)
- openingHours
"""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from curl_cffi.requests import AsyncSession
from loguru import logger

# Configuration
API_BASE_URL = "https://www.gateau.fi/api/cda/content"
STORE_LISTING_URL = "https://www.gateau.fi/myymalat/"
OUTPUT_DIR = Path(__file__).parent.parent / "output"


def get_headers():
    """Get request headers."""
    return {
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
        'cache-control': 'no-store, no-cache',
    }


async def fetch_store_listing_api(session: AsyncSession) -> list[dict]:
    """Fetch the main store listing page via API to get store URLs."""
    params = {
        'contentUrl': STORE_LISTING_URL,
        'currentPageUrl': '/',
    }
    
    logger.info("Fetching store listing from API")
    
    try:
        response = await session.get(
            API_BASE_URL,
            params=params,
            headers=get_headers(),
            impersonate="chrome"
        )
        
        if response.status_code == 200:
            data = response.json()
            
            # Extract store URLs from navigation menu
            stores = []
            if data and len(data) > 0:
                nav = data[0].get('navigation', {})
                menu_items = nav.get('mainMenuItems', [])
                
                # Find Myymälät (Stores) menu item
                for item in menu_items:
                    if item.get('url') == '/myymalat/':
                        children = item.get('children', [])
                        for child in children:
                            store_url = child.get('url', '')
                            store_name = child.get('name', '')
                            if store_url and '/myymalat/' in store_url:
                                stores.append({
                                    'name': store_name,
                                    'url': f"https://www.gateau.fi{store_url}" if not store_url.startswith('http') else store_url
                                })
                        break
            
            logger.info(f"Found {len(stores)} store URLs from navigation")
            return stores
        else:
            logger.error(f"API returned status {response.status_code}")
            return []
    except Exception as e:
        logger.error(f"Error fetching store listing: {e}")
        return []


async def fetch_store_detail(session: AsyncSession, store_url: str) -> dict:
    """Fetch store detail via API."""
    params = {
        'contentUrl': store_url,
        'currentPageUrl': '/myymalat/',
    }
    
    logger.debug(f"Fetching store detail: {store_url}")
    
    try:
        response = await session.get(
            API_BASE_URL,
            params=params,
            headers=get_headers(),
            impersonate="chrome"
        )
        
        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0:
                return data[0]
        
        return {}
    except Exception as e:
        logger.error(f"Error fetching store detail: {e}")
        return {}


def extract_phone_from_html(html: str) -> str:
    """Extract phone number from additionalContactInfomation HTML."""
    if not html:
        return ""
    
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', html)
    
    # Look for phone patterns
    # Finnish format: 050 310 2779 or +358 50 310 2779
    phone_match = re.search(r'(?:Puh\.?\s*)?(\+?\d[\d\s-]{8,})', text)
    if phone_match:
        return phone_match.group(1).strip()
    
    return text.strip()


def parse_store_data(api_data: dict, store_url: str) -> dict:
    """Parse store information from API response."""
    store = {
        "name": api_data.get("name", ""),
        "street_address": api_data.get("streetAddress", ""),
        "postal_code": api_data.get("postalCode", ""),
        "city": api_data.get("city", ""),
        "country": api_data.get("country", "Finland"),
        "latitude": api_data.get("latitude", 0.0),
        "longitude": api_data.get("longitude", 0.0),
        "email": api_data.get("email", ""),
        "url": store_url,
    }
    
    # Build full address
    addr_parts = []
    if store["street_address"]:
        addr_parts.append(store["street_address"])
    if store["postal_code"] and store["city"]:
        addr_parts.append(f"{store['postal_code']} {store['city']}")
    elif store["city"]:
        addr_parts.append(store["city"])
    store["address"] = ", ".join(addr_parts)
    
    # Extract phone from additionalContactInfomation
    additional_info = api_data.get("additionalContactInfomation", "")
    store["phone"] = extract_phone_from_html(additional_info)
    
    # Format opening hours
    opening_hours = api_data.get("openingHours", {})
    if opening_hours:
        hours_list = []
        for day, hours in opening_hours.items():
            hours_list.append(f"{day}: {hours}")
        store["opening_hours"] = "; ".join(hours_list)
    else:
        store["opening_hours"] = ""
    
    return store


def normalize_store(store: dict) -> dict:
    """Normalize store data to standard format."""
    return {
        "name": store.get("name", "").strip(),
        "city": store.get("city", "").strip(),
        "postal_code": store.get("postal_code", "").strip(),
        "street_address": store.get("street_address", "").strip(),
        "address": store.get("address", "").strip(),
        "country": store.get("country", "Finland").strip(),
        "latitude": store.get("latitude", 0.0),
        "longitude": store.get("longitude", 0.0),
        "url": store.get("url", ""),
        "phone": store.get("phone", "").strip(),
        "email": store.get("email", "").strip(),
        "opening_hours": store.get("opening_hours", "").strip(),
        "operator": "Gateau",
    }


async def scrape_stores():
    """Main scraping function."""
    logger.info("Starting Gateau Finland store scraper")
    
    async with AsyncSession() as session:
        # Step 1: Get store URLs from listing page API
        store_info = await fetch_store_listing_api(session)
        
        if not store_info:
            logger.error("No store URLs found")
            return []
        
        # Step 2: Fetch each store's details
        stores = []
        for info in store_info:
            url = info['url']
            logger.info(f"Fetching: {info['name']}")
            
            api_data = await fetch_store_detail(session, url)
            
            if api_data:
                store = parse_store_data(api_data, url)
                stores.append(normalize_store(store))
                
                # Save API response for debugging
                store_slug = url.split('/')[-2]
                debug_file = OUTPUT_DIR / "json" / f"gateau_{store_slug}.json"
                debug_file.parent.mkdir(parents=True, exist_ok=True)
                debug_file.write_text(json.dumps(api_data, indent=2, ensure_ascii=False), encoding='utf-8')
            else:
                logger.warning(f"No data returned for {info['name']}")
            
            # Small delay to be polite
            await asyncio.sleep(0.3)
        
        logger.info(f"Successfully scraped {len(stores)} stores")
        return stores


def save_to_excel(stores: list[dict]):
    """Save stores to Excel file."""
    if not stores:
        logger.warning("No stores to save")
        return
    
    df = pd.DataFrame(stores)
    
    # Reorder columns
    column_order = [
        "name", "street_address", "postal_code", "city", "country",
        "address", "latitude", "longitude", "phone", "email", 
        "opening_hours", "url", "operator"
    ]
    df = df[[c for c in column_order if c in df.columns]]
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_dir = OUTPUT_DIR / "excel"
    excel_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = excel_dir / f"gateau_{timestamp}.xlsx"
    df.to_excel(output_file, index=False)
    logger.info(f"Saved {len(stores)} stores to {output_file}")
    
    # Print summary
    print("\n" + "="*60)
    print("GATEAU FINLAND SCRAPING RESULTS")
    print("="*60)
    print(f"Total stores scraped: {len(stores)}")
    print(f"\nStores with addresses: {sum(1 for s in stores if s.get('address'))}")
    print(f"Stores with phone: {sum(1 for s in stores if s.get('phone'))}")
    print(f"Stores with email: {sum(1 for s in stores if s.get('email'))}")
    print(f"Stores with coordinates: {sum(1 for s in stores if s.get('latitude'))}")
    print(f"\nOutput file: {output_file}")
    print("="*60)
    
    # Show sample data
    print("\nStore data:")
    print(df[['name', 'street_address', 'postal_code', 'city']].to_string())


async def main():
    """Main entry point."""
    stores = await scrape_stores()
    save_to_excel(stores)


if __name__ == "__main__":
    asyncio.run(main())
