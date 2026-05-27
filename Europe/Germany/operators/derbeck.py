"""
Der Beck Germany Store Scraper

Scrapes store locations from https://www.der-beck.de/in-ihrer-naehe/filialfinder

Uses Playwright since store data is loaded dynamically via JavaScript.

Store structure (based on XPath):
- div.store-finder-listing contains store cards
- Each store has address info in p elements
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
STORE_URL = "https://www.der-beck.de/in-ihrer-naehe/filialfinder"
OUTPUT_DIR = Path(__file__).parent.parent / "output"


async def scrape_stores_with_playwright():
    """Scrape stores using Playwright for JavaScript rendering."""
    logger.info("Starting Der Beck store scraper with Playwright")
    
    stores = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        logger.info(f"Navigating to: {STORE_URL}")
        await page.goto(STORE_URL, wait_until="networkidle")
        
        # Wait for store list to load
        logger.info("Waiting for store list to load...")
        try:
            await page.wait_for_selector(".store-finder-listing", timeout=15000)
            # Wait for actual content
            await asyncio.sleep(5)
        except Exception as e:
            logger.warning(f"Selector wait failed: {e}")
            await asyncio.sleep(5)
        
        # Take screenshot
        screenshot_path = OUTPUT_DIR / "derbeck_screenshot.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        logger.info(f"Screenshot saved to {screenshot_path}")
        
        # Save rendered HTML
        html = await page.content()
        html_path = OUTPUT_DIR / "derbeck_rendered.html"
        html_path.write_text(html, encoding='utf-8')
        logger.info(f"Rendered HTML saved ({len(html)} bytes)")
        
        # Get all store cards/items
        store_elements = await page.query_selector_all(".store-finder-listing > div")
        
        if not store_elements:
            # Try alternative selectors
            store_elements = await page.query_selector_all(".store-card")
        
        if not store_elements:
            # Try finding by address pattern
            store_elements = await page.query_selector_all("[class*='store']")
        
        logger.info(f"Found {len(store_elements)} store elements")
        
        for i, store_el in enumerate(store_elements):
            try:
                store = {
                    "name": "",
                    "address": "",
                    "street_address": "",
                    "postal_code": "",
                    "city": "",
                    "phone": "",
                }
                
                # Try to get all text content
                text = await store_el.inner_text()
                
                if text.strip():
                    lines = [l.strip() for l in text.split('\n') if l.strip()]
                    
                    # First line might be store name
                    if lines:
                        store["name"] = lines[0]
                    
                    # Look for address pattern
                    for line in lines:
                        # German postal code: 5 digits
                        postal_match = re.search(r'(\d{5})\s+(\w+)', line)
                        if postal_match:
                            store["postal_code"] = postal_match.group(1)
                            store["city"] = postal_match.group(2)
                            store["address"] = line
                        
                        # Look for street (usually before postal code)
                        if re.match(r'^[A-Za-zäöüÄÖÜß]+.*\d+', line) and not postal_match:
                            store["street_address"] = line
                        
                        # Phone pattern
                        phone_match = re.search(r'[\d\s/-]{10,}', line)
                        if phone_match and not postal_match:
                            store["phone"] = phone_match.group(0).strip()
                
                if store["name"] or store["postal_code"]:
                    stores.append(store)
                    logger.debug(f"Store {i+1}: {store.get('name', 'N/A')} - {store.get('city', 'N/A')}")
                    
            except Exception as e:
                logger.warning(f"Error extracting store {i+1}: {e}")
                continue
        
        await browser.close()
    
    logger.info(f"Successfully scraped {len(stores)} stores")
    return stores


def normalize_store(store: dict) -> dict:
    """Normalize store data to standard format."""
    return {
        "name": f"Der Beck {store.get('name', '')}".strip() if store.get('name') else "Der Beck",
        "city": store.get("city", "").strip(),
        "postal_code": store.get("postal_code", "").strip(),
        "street_address": store.get("street_address", "").strip(),
        "address": store.get("address", "").strip(),
        "country": "Germany",
        "latitude": 0.0,
        "longitude": 0.0,
        "url": STORE_URL,
        "phone": store.get("phone", "").strip(),
        "email": "",
        "operator": "Der Beck",
    }


def save_to_excel(stores: list[dict]):
    """Save stores to Excel file."""
    if not stores:
        logger.warning("No stores to save")
        return
    
    normalized = [normalize_store(s) for s in stores]
    df = pd.DataFrame(normalized)
    
    column_order = [
        "name", "street_address", "postal_code", "city", "country",
        "address", "phone", "url", "operator"
    ]
    df = df[[c for c in column_order if c in df.columns]]
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_dir = OUTPUT_DIR / "excel"
    excel_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = excel_dir / f"derbeck_{timestamp}.xlsx"
    df.to_excel(output_file, index=False)
    logger.info(f"Saved {len(normalized)} stores to {output_file}")
    
    print("\n" + "="*60)
    print("DER BECK SCRAPING RESULTS")
    print("="*60)
    print(f"Total stores scraped: {len(normalized)}")
    print(f"Stores with addresses: {sum(1 for s in normalized if s.get('address'))}")
    print(f"Stores with cities: {sum(1 for s in normalized if s.get('city'))}")
    print(f"\nOutput file: {output_file}")
    print("="*60)
    
    if len(df) > 0:
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
