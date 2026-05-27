"""
Konditoria PH7 Finland Store Scraper

Scrapes store locations from known store URLs.
Uses curl_cffi to bypass bot protection on the React SPA.
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from curl_cffi.requests import AsyncSession
from loguru import logger

# Configuration
OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Known store URLs (from user)
STORE_URLS = [
    {"name": "Tuusula Tehtaanmyymälä", "url": "https://www.konditoriaph7.fi/i/tuusula-tehtaanmyymala/19/"},
    {"name": "Espoo K-Supermarket Lasihytti", "url": "https://www.konditoriaph7.fi/i/espoo-k-supermarket-lasihytti/41/"},
    {"name": "Helsinki Citymarket Ruoholahti", "url": "https://www.konditoriaph7.fi/i/helsinki-citymarket-ruoholahti/32/"},
    {"name": "Helsinki Kulosaari A-lehtien kiinteistö", "url": "https://www.konditoriaph7.fi/i/helsinki-kulosaari-a-lehtien-kiinteisto/24/"},
    {"name": "Tampere Kahvila Leivonpesä", "url": "https://www.konditoriaph7.fi/i/tampere-kahvila-leivonpesa/34/"},
]


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


def extract_address_from_url_name(name: str) -> dict:
    """Extract city and location from URL name."""
    # Names are like "Tuusula Tehtaanmyymälä", "Espoo K-Supermarket Lasihytti"
    parts = name.split(' ', 1)
    city = parts[0] if parts else ""
    location = parts[1] if len(parts) > 1 else ""
    return {"city": city, "location": location}


async def scrape_stores():
    """Main scraping function."""
    logger.info("Starting Konditoria PH7 store scraper")
    logger.info(f"Scraping {len(STORE_URLS)} known stores")
    
    stores = []
    
    async with AsyncSession() as session:
        for store_info in STORE_URLS:
            name = store_info["name"]
            url = store_info["url"]
            
            logger.info(f"Fetching: {name}")
            
            # Extract city from name
            name_parts = extract_address_from_url_name(name)
            
            store = {
                "name": name,
                "url": url,
                "city": name_parts["city"],
                "location": name_parts["location"],
                "address": "",
                "street_address": "",
                "postal_code": "",
                "phone": "",
                "email": "",
            }
            
            # Try to fetch page for more details
            html = await fetch_page(session, url)
            
            if html and len(html) > 500:  # Page rendered
                # Look for address pattern: "Osoite: Street, PostalCode City"
                addr_match = re.search(r'Osoite[:\s]*([^<\n]+)', html, re.IGNORECASE)
                if addr_match:
                    addr = addr_match.group(1).strip()
                    store["address"] = addr
                    
                    # Parse postal code
                    postal_match = re.search(r'(\d{5})\s+(\w+)', addr)
                    if postal_match:
                        store["postal_code"] = postal_match.group(1)
                        store["city"] = postal_match.group(2)
                        street_part = addr[:postal_match.start()].strip().rstrip(',')
                        store["street_address"] = street_part
                
                # Look for phone
                phone_match = re.search(r'(?:Puhelin|Puh|Tel)[:\s]*([+\d\s-]+)', html, re.IGNORECASE)
                if phone_match:
                    store["phone"] = phone_match.group(1).strip()
                
                # Look for email
                email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', html)
                if email_match:
                    store["email"] = email_match.group(0)
            else:
                logger.warning(f"Could not fetch full page for {name}, using URL info only")
            
            stores.append(store)
            await asyncio.sleep(0.5)
    
    logger.info(f"Scraped {len(stores)} stores")
    return stores


def normalize_store(store: dict) -> dict:
    """Normalize store data to standard format."""
    return {
        "name": f"Konditoria PH7 {store.get('name', '')}".strip(),
        "city": store.get("city", "").strip(),
        "postal_code": store.get("postal_code", "").strip(),
        "street_address": store.get("street_address", "").strip(),
        "address": store.get("address", "").strip(),
        "country": "Finland",
        "latitude": 0.0,
        "longitude": 0.0,
        "url": store.get("url", ""),
        "phone": store.get("phone", "").strip(),
        "email": store.get("email", "").strip(),
        "operator": "Konditoria PH7",
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
        "address", "phone", "email", "url", "operator"
    ]
    df = df[[c for c in column_order if c in df.columns]]
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_dir = OUTPUT_DIR / "excel"
    excel_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = excel_dir / f"konditoria_ph7_{timestamp}.xlsx"
    df.to_excel(output_file, index=False)
    logger.info(f"Saved {len(normalized)} stores to {output_file}")
    
    print("\n" + "="*60)
    print("KONDITORIA PH7 SCRAPING RESULTS")
    print("="*60)
    print(f"Total stores scraped: {len(normalized)}")
    print(f"\nOutput file: {output_file}")
    print("="*60)
    print("\nStore data:")
    print(df[['name', 'city', 'address']].to_string())


async def main():
    """Main entry point."""
    stores = await scrape_stores()
    save_to_excel(stores)


if __name__ == "__main__":
    asyncio.run(main())
