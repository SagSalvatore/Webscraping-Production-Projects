"""
Arnold's Finland Store Scraper

Scrapes store locations from https://arnolds.fi/en/locations/

The store data is embedded as JSON in the page's JavaScript:
jQuery(document).ready(function($){
  var arnoldsMap = {
    locations: [{"id":..., "title":..., "address":..., "postcode":..., "city":..., ...}]
  }
}

We extract this JSON from the static HTML.
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
# Use Finnish page which has the embedded JSON data
STORE_URL = "https://arnolds.fi/kahvilat/"
OUTPUT_DIR = Path(__file__).parent.parent / "output"


def get_headers():
    """Get request headers."""
    return {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,fi;q=0.8',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
    }


async def fetch_page(session: AsyncSession, url: str) -> str:
    """Fetch a page's HTML content."""
    try:
        response = await session.get(
            url,
            headers=get_headers(),
            impersonate="chrome"
        )
        
        if response.status_code == 200:
            return response.text
        else:
            logger.warning(f"Got status {response.status_code} for {url}")
            return ""
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return ""


def extract_locations_json(html: str) -> list[dict]:
    """Extract locations JSON from the embedded JavaScript."""
    
    # Find the start of arnoldsMap locations
    start_marker = 'locations: ['
    start_idx = html.find(start_marker)
    
    if start_idx == -1:
        # Try alternate format
        start_marker = 'locations:['
        start_idx = html.find(start_marker)
    
    if start_idx == -1:
        logger.error("Could not find locations marker in page")
        return []
    
    # Move to start of array
    start_idx = html.find('[', start_idx)
    
    # Find the end of the array by counting brackets
    bracket_count = 0
    end_idx = start_idx
    for i, char in enumerate(html[start_idx:]):
        if char == '[':
            bracket_count += 1
        elif char == ']':
            bracket_count -= 1
            if bracket_count == 0:
                end_idx = start_idx + i + 1
                break
    
    json_str = html[start_idx:end_idx]
    logger.debug(f"Extracted JSON string of length {len(json_str)}")
    
    try:
        locations = json.loads(json_str)
        logger.info(f"Found {len(locations)} locations in JSON")
        return locations
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse locations JSON: {e}")
        return []


def extract_phone_from_content(content: str) -> str:
    """Extract phone number from HTML content."""
    # Pattern for Finnish phone numbers
    phone_match = re.search(r'(?:tel:)?\+?358?\s*(\d[\d\s-]+)', content)
    if phone_match:
        phone = phone_match.group(1).strip()
        # Clean up
        phone = re.sub(r'\s+', ' ', phone)
        return phone
    
    # Try simpler pattern
    phone_match = re.search(r'(\d{3,4}[\s-]?\d{6,7})', content)
    if phone_match:
        return phone_match.group(1)
    
    return ""


def extract_hours_from_content(content: str) -> str:
    """Extract opening hours from HTML content."""
    # Look for clock icon followed by hours
    hours_match = re.search(r'clock[^>]*>.*?((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Ma|Ti|Ke|To|Pe|La|Su)[^<]+)', content, re.IGNORECASE | re.DOTALL)
    if hours_match:
        hours = hours_match.group(1)
        # Clean up
        hours = re.sub(r'\s+', ' ', hours).strip()
        return hours
    
    # Try to find day patterns directly
    hours_match = re.search(r'((?:Mon|Ma)[\w\s:-]+(?:Sun|Su)[\w\s:-]+\d+)', content, re.IGNORECASE)
    if hours_match:
        return hours_match.group(1).strip()
    
    return ""


def parse_location(loc: dict) -> dict:
    """Parse a location entry from the JSON."""
    content = loc.get('content', '')
    
    store = {
        "name": f"Arnold's {loc.get('title', '')}",
        "street_address": loc.get('address', ''),
        "postal_code": loc.get('postcode', ''),
        "city": loc.get('city', ''),
        "latitude": loc.get('lat', 0.0),
        "longitude": loc.get('lng', 0.0),
        "phone": extract_phone_from_content(content),
        "opening_hours": extract_hours_from_content(content),
    }
    
    # Build full address
    if store['street_address'] and store['postal_code'] and store['city']:
        store['address'] = f"{store['street_address']}, {store['postal_code']} {store['city']}"
    else:
        store['address'] = f"{store['street_address']} {store['postal_code']} {store['city']}".strip()
    
    return store


def normalize_store(store: dict) -> dict:
    """Normalize store data to standard format."""
    return {
        "name": store.get("name", "").strip(),
        "city": store.get("city", "").strip(),
        "postal_code": store.get("postal_code", "").strip(),
        "street_address": store.get("street_address", "").strip(),
        "address": store.get("address", "").strip(),
        "country": "Finland",
        "latitude": float(store.get("latitude", 0) or 0),
        "longitude": float(store.get("longitude", 0) or 0),
        "url": STORE_URL,
        "phone": store.get("phone", "").strip(),
        "email": "",
        "opening_hours": store.get("opening_hours", "").strip(),
        "operator": "Arnold's",
    }


async def scrape_stores():
    """Main scraping function."""
    logger.info("Starting Arnold's store scraper")
    
    async with AsyncSession() as session:
        # Fetch the page
        logger.info(f"Fetching: {STORE_URL}")
        html = await fetch_page(session, STORE_URL)
        
        if not html:
            logger.error("Failed to fetch store page")
            return []
        
        # Save for debugging
        debug_file = OUTPUT_DIR / "arnolds.html"
        debug_file.parent.mkdir(parents=True, exist_ok=True)
        debug_file.write_text(html, encoding='utf-8')
        
        # Extract locations from embedded JSON
        locations = extract_locations_json(html)
        
        if not locations:
            logger.error("No locations found")
            return []
        
        # Parse and normalize all locations
        stores = []
        for loc in locations:
            # Skip inactive stores
            if loc.get('inactive'):
                continue
            
            store = parse_location(loc)
            stores.append(normalize_store(store))
        
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
        "address", "latitude", "longitude", "phone", "opening_hours", "url", "operator"
    ]
    df = df[[c for c in column_order if c in df.columns]]
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_dir = OUTPUT_DIR / "excel"
    excel_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = excel_dir / f"arnolds_{timestamp}.xlsx"
    df.to_excel(output_file, index=False)
    logger.info(f"Saved {len(stores)} stores to {output_file}")
    
    # Also save JSON for reference
    json_file = OUTPUT_DIR / "json" / f"arnolds_{timestamp}.json"
    json_file.parent.mkdir(parents=True, exist_ok=True)
    df.to_json(json_file, orient='records', force_ascii=False, indent=2)
    
    # Print summary
    print("\n" + "="*60)
    print("ARNOLD'S SCRAPING RESULTS")
    print("="*60)
    print(f"Total stores scraped: {len(stores)}")
    print(f"\nStores with addresses: {sum(1 for s in stores if s.get('address'))}")
    print(f"Stores with coordinates: {sum(1 for s in stores if s.get('latitude'))}")
    print(f"Stores with phone: {sum(1 for s in stores if s.get('phone'))}")
    print(f"\nOutput file: {output_file}")
    print("="*60)
    
    # Show sample data
    print("\nFirst 10 stores:")
    print(df[['name', 'street_address', 'postal_code', 'city']].head(10).to_string())
    print(f"\n... and {len(df) - 10} more stores" if len(df) > 10 else "")


async def main():
    """Main entry point."""
    stores = await scrape_stores()
    save_to_excel(stores)


if __name__ == "__main__":
    asyncio.run(main())
