"""
Picnic Finland Store Scraper

Scrapes store locations from https://picnic.fi/en/find-the-nearest-picnic/

Uses Playwright to render the JavaScript-loaded page and extract store data from:
- div.store-locator__infobox elements
- Store name: div.infobox__title.store-location
- Address: div.infobox__row.store-address
- City: div.store-products-services
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger

try:
    from playwright.async_api import async_playwright
except ImportError:
    logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
    raise

# Configuration
STORE_URL = "https://picnic.fi/en/find-the-nearest-picnic/"
OUTPUT_DIR = Path(__file__).parent.parent / "output"


async def scrape_stores_with_playwright():
    """Scrape stores using Playwright for JavaScript rendering."""
    logger.info("Starting Picnic store scraper with Playwright")
    
    stores = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        logger.info(f"Navigating to: {STORE_URL}")
        await page.goto(STORE_URL, wait_until="networkidle")
        
        # Wait for store list to load
        logger.info("Waiting for store list to load...")
        try:
            await page.wait_for_selector("#storeLocator_storeList", timeout=15000)
        except:
            logger.warning("Store list selector not found, trying alternative...")
            await asyncio.sleep(3)
        
        # Get all store infobox elements
        store_elements = await page.query_selector_all(".store-locator__infobox")
        logger.info(f"Found {len(store_elements)} store elements")
        
        for i, store_el in enumerate(store_elements):
            try:
                store = {
                    "name": "",
                    "address": "",
                    "city": "",
                    "postal_code": "",
                    "street_address": "",
                }
                
                # Extract store name from store-location class
                name_el = await store_el.query_selector(".store-location")
                if name_el:
                    store["name"] = await name_el.inner_text()
                
                # Extract address from store-address class
                addr_el = await store_el.query_selector(".store-address")
                if addr_el:
                    store["address"] = await addr_el.inner_text()
                
                # Extract city from store-products-services
                city_el = await store_el.query_selector(".store-products-services")
                if city_el:
                    store["city"] = await city_el.inner_text()
                
                # Parse address for postal code
                if store["address"]:
                    addr = store["address"]
                    # Finnish postal codes are 5 digits
                    postal_match = re.search(r'(\d{5})', addr)
                    if postal_match:
                        store["postal_code"] = postal_match.group(1)
                        # Extract street address (everything before postal code)
                        street_part = addr[:postal_match.start()].strip().rstrip(',')
                        store["street_address"] = street_part
                
                if store["name"]:
                    stores.append(store)
                    logger.debug(f"Store {i+1}: {store['name']} - {store['address']}")
                    
            except Exception as e:
                logger.warning(f"Error extracting store {i+1}: {e}")
                continue
        
        # Take screenshot for verification
        screenshot_path = OUTPUT_DIR / "picnic_screenshot.png"
        await page.screenshot(path=str(screenshot_path), full_page=False)
        logger.info(f"Screenshot saved to {screenshot_path}")
        
        await browser.close()
    
    logger.info(f"Successfully scraped {len(stores)} stores")
    return stores


def normalize_store(store: dict) -> dict:
    """Normalize store data to standard format."""
    return {
        "name": f"Picnic {store.get('name', '')}".strip(),
        "city": store.get("city", "").strip(),
        "postal_code": store.get("postal_code", "").strip(),
        "street_address": store.get("street_address", "").strip(),
        "address": store.get("address", "").strip(),
        "country": "Finland",
        "latitude": 0.0,
        "longitude": 0.0,
        "url": STORE_URL,
        "phone": "",
        "email": "",
        "operator": "Picnic",
    }


def save_to_excel(stores: list[dict]):
    """Save stores to Excel file."""
    if not stores:
        logger.warning("No stores to save")
        return
    
    # Normalize all stores
    normalized = [normalize_store(s) for s in stores]
    
    df = pd.DataFrame(normalized)
    
    # Reorder columns
    column_order = [
        "name", "street_address", "postal_code", "city", "country",
        "address", "phone", "email", "url", "operator"
    ]
    df = df[[c for c in column_order if c in df.columns]]
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_dir = OUTPUT_DIR / "excel"
    excel_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = excel_dir / f"picnic_{timestamp}.xlsx"
    df.to_excel(output_file, index=False)
    logger.info(f"Saved {len(normalized)} stores to {output_file}")
    
    # Print summary
    print("\n" + "="*60)
    print("PICNIC SCRAPING RESULTS")
    print("="*60)
    print(f"Total stores scraped: {len(normalized)}")
    print(f"\nStores with addresses: {sum(1 for s in normalized if s.get('address'))}")
    print(f"Stores with postal codes: {sum(1 for s in normalized if s.get('postal_code'))}")
    print(f"Stores with cities: {sum(1 for s in normalized if s.get('city'))}")
    print(f"\nOutput file: {output_file}")
    print("="*60)
    
    # Show sample data
    print("\nFirst 10 stores:")
    print(df[['name', 'street_address', 'postal_code', 'city']].head(10).to_string())
    if len(df) > 10:
        print(f"\n... and {len(df) - 10} more stores")


async def main():
    """Main entry point."""
    stores = await scrape_stores_with_playwright()
    save_to_excel(stores)


if __name__ == "__main__":
    asyncio.run(main())
