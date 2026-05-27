"""
Marian Finland Store Scraper

Scrapes store locations from https://marian.fi/myymalat/

The page uses Elementor and store data is in static HTML:
- Store name: h3.elementor-heading-title a
- Phone: li with fa-phone-alt icon -> span.elementor-icon-list-text
- Address: li with fa-map-marker-alt icon -> a span.elementor-icon-list-text
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from loguru import logger

# Configuration
STORE_URL = "https://marian.fi/myymalat/"
OUTPUT_DIR = Path(__file__).parent.parent / "output"


def get_headers():
    """Get request headers."""
    return {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,fi;q=0.8',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }


async def fetch_page(session: AsyncSession, url: str) -> str:
    """Fetch page content."""
    try:
        response = await session.get(
            url,
            headers=get_headers(),
            impersonate="chrome"
        )
        return response.text if response.status_code == 200 else ""
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return ""


def parse_stores(html: str) -> list[dict]:
    """Parse stores from HTML using BeautifulSoup."""
    soup = BeautifulSoup(html, 'html.parser')
    stores = []
    
    # Find all store items (e-loop-item divs)
    store_items = soup.find_all('div', class_='e-loop-item')
    
    if not store_items:
        # Try alternative: find by myymala-item class
        store_items = soup.find_all('div', class_='myymala-item')
    
    if not store_items:
        # Try to find by elementor-heading-title (each store has a title)
        titles = soup.find_all('h3', class_='elementor-heading-title')
        for title in titles:
            # Get parent container
            parent = title.find_parent('div', class_='elementor-column')
            if parent:
                store_items.append(parent)
    
    logger.info(f"Found {len(store_items)} store items")
    
    for item in store_items:
        store = {
            "name": "",
            "url": "",
            "phone": "",
            "address": "",
            "street_address": "",
            "postal_code": "",
            "city": "",
        }
        
        # Get store name from h3 a
        title_el = item.find('h3', class_='elementor-heading-title')
        if title_el:
            link = title_el.find('a')
            if link:
                store["name"] = link.get_text(strip=True)
                store["url"] = link.get('href', '')
            else:
                store["name"] = title_el.get_text(strip=True)
        
        # Find icon list items
        list_items = item.find_all('li', class_='elementor-icon-list-item')
        
        for li in list_items:
            icon = li.find('i')
            text_span = li.find('span', class_='elementor-icon-list-text')
            
            if not text_span:
                continue
            
            text = text_span.get_text(strip=True)
            
            if icon:
                icon_class = ' '.join(icon.get('class', []))
                
                # Phone icon
                if 'fa-phone' in icon_class:
                    store["phone"] = text
                
                # Map/location icon - address
                elif 'fa-map-marker' in icon_class:
                    store["address"] = text
                    
                    # Parse address: "Kauppalantie 42, 00320 Helsinki"
                    addr_match = re.search(r'(.+?),?\s*(\d{5})\s+(\w+)', text)
                    if addr_match:
                        store["street_address"] = addr_match.group(1).strip()
                        store["postal_code"] = addr_match.group(2)
                        store["city"] = addr_match.group(3)
        
        if store["name"]:
            stores.append(store)
            logger.debug(f"Parsed: {store['name']} - {store['address']}")
    
    return stores


def normalize_store(store: dict) -> dict:
    """Normalize store data to standard format."""
    return {
        "name": store.get("name", "").strip(),
        "city": store.get("city", "").strip(),
        "postal_code": store.get("postal_code", "").strip(),
        "street_address": store.get("street_address", "").strip(),
        "address": store.get("address", "").strip(),
        "country": "Finland",
        "latitude": 0.0,
        "longitude": 0.0,
        "url": store.get("url", ""),
        "phone": store.get("phone", "").strip(),
        "email": "",
        "operator": "Marian",
    }


async def scrape_stores():
    """Main scraping function."""
    logger.info("Starting Marian store scraper")
    
    async with AsyncSession() as session:
        logger.info(f"Fetching: {STORE_URL}")
        html = await fetch_page(session, STORE_URL)
        
        if not html:
            logger.error("Failed to fetch store page")
            return []
        
        logger.info(f"Fetched {len(html)} bytes")
        
        # Save for debugging
        debug_file = OUTPUT_DIR / "marian.html"
        debug_file.parent.mkdir(parents=True, exist_ok=True)
        debug_file.write_text(html, encoding='utf-8')
        
        # Parse stores
        stores = parse_stores(html)
        
        if not stores:
            logger.warning("No stores found")
            return []
        
        # Normalize
        normalized = [normalize_store(s) for s in stores]
        
        logger.info(f"Successfully scraped {len(normalized)} stores")
        return normalized


def save_to_excel(stores: list[dict]):
    """Save stores to Excel file."""
    if not stores:
        logger.warning("No stores to save")
        return
    
    df = pd.DataFrame(stores)
    
    column_order = [
        "name", "street_address", "postal_code", "city", "country",
        "address", "phone", "url", "operator"
    ]
    df = df[[c for c in column_order if c in df.columns]]
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_dir = OUTPUT_DIR / "excel"
    excel_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = excel_dir / f"marian_{timestamp}.xlsx"
    df.to_excel(output_file, index=False)
    logger.info(f"Saved {len(stores)} stores to {output_file}")
    
    print("\n" + "="*60)
    print("MARIAN SCRAPING RESULTS")
    print("="*60)
    print(f"Total stores scraped: {len(stores)}")
    print(f"Stores with addresses: {sum(1 for s in stores if s.get('address'))}")
    print(f"Stores with phone: {sum(1 for s in stores if s.get('phone'))}")
    print(f"\nOutput file: {output_file}")
    print("="*60)
    print("\nStore data:")
    print(df[['name', 'street_address', 'postal_code', 'city']].to_string())


async def main():
    """Main entry point."""
    stores = await scrape_stores()
    save_to_excel(stores)


if __name__ == "__main__":
    asyncio.run(main())
