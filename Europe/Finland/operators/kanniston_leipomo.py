"""
Kanniston Leipomo Finland Store Scraper

Scrapes store locations from https://www.kannistonleipomo.fi/Myymalat

Two-step approach:
1. Fetch listing page to extract store URLs from aria-label links
2. Fetch each store page and parse the og:description meta tag which contains:
   - Address (street, postal code, city)
   - Opening hours
   - Phone
   - Email
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from curl_cffi.requests import AsyncSession
from loguru import logger

# Configuration
BASE_URL = "https://www.kannistonleipomo.fi"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Known store slugs (from user)
STORE_SLUGS = [
    "Hakaniemi",
    "Punavuori", 
    "Toolo",
    "Sello",
    "Yliopistonkatu",
    "Munkkiniemi",
]


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


def extract_store_urls(html: str) -> list[dict]:
    """Extract store URLs from the listing page."""
    stores = []
    
    # Look for links with aria-label (as mentioned by user)
    # Pattern: <a aria-label='StoreName' href='/StoreName'...>
    aria_pattern = r'<a[^>]*aria-label=["\']([^"\']+)["\'][^>]*href=["\']([^"\']+)["\']'
    matches = re.findall(aria_pattern, html, re.IGNORECASE)
    
    for name, url in matches:
        # Skip non-store links
        if url.startswith('/Myymalat') or url == '/':
            continue
        # Only include store pages (typically capitalized names)
        if url.startswith('/') and not url.startswith('/static') and not url.startswith('/api'):
            # Skip common non-store pages
            skip_pages = ['/Yhteystiedot', '/Tuotteet', '/Tilaus', '/Ajankohtaista', '/Koti', '/Myymalat']
            if url not in skip_pages and not any(url.startswith(p) for p in ['/static', '/api', '/css', '/js']):
                full_url = f"{BASE_URL}{url}" if not url.startswith('http') else url
                stores.append({
                    'name': name.strip(),
                    'url': full_url
                })
    
    # Also try href pattern with capitalized paths (store names)
    # Pattern: href="/StoreName" where StoreName starts with capital
    href_pattern = r'href=["\'](/[A-Z][a-zA-Z]+)["\']'
    href_matches = re.findall(href_pattern, html)
    
    seen_urls = {s['url'] for s in stores}
    skip_pages = ['Myymalat', 'Yhteystiedot', 'Tuotteet', 'Tilaus', 'Ajankohtaista', 'Koti', 'Ostoskori', 'Tilaukset']
    
    for path in href_matches:
        name = path.strip('/')
        if name not in skip_pages:
            full_url = f"{BASE_URL}{path}"
            if full_url not in seen_urls:
                stores.append({
                    'name': name,
                    'url': full_url
                })
                seen_urls.add(full_url)
    
    # Deduplicate by URL
    unique_stores = []
    seen = set()
    for store in stores:
        if store['url'] not in seen:
            unique_stores.append(store)
            seen.add(store['url'])
    
    logger.info(f"Found {len(unique_stores)} store URLs")
    return unique_stores


def parse_store_data(html: str, store_url: str, store_name: str) -> dict:
    """Parse store information from the page's meta tags."""
    store = {
        "name": f"Kanniston Leipomo {store_name}",
        "street_address": "",
        "postal_code": "",
        "city": "",
        "address": "",
        "phone": "",
        "email": "",
        "opening_hours": "",
        "url": store_url,
    }
    
    # Extract og:description or meta description
    # Format: "Street Address, PostalCode City<br>\nHours<br>\nPhone<br>\nEmail"
    desc_match = re.search(
        r'(?:og:description|name="description")["\s]+content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE
    )
    
    if desc_match:
        description = desc_match.group(1)
        # Decode HTML entities
        description = description.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        
        # Split by <br> or newlines
        parts = re.split(r'<br\s*/?>\s*|\n', description)
        parts = [p.strip() for p in parts if p.strip()]
        
        logger.debug(f"Description parts for {store_name}: {parts}")
        
        for part in parts:
            # Check for address (contains postal code pattern: 5 digits)
            postal_match = re.search(r'(\d{5})\s+(\w+)', part)
            if postal_match and not store['postal_code']:
                store['postal_code'] = postal_match.group(1)
                store['city'] = postal_match.group(2)
                # Street is the part before the postal code
                street_part = part[:postal_match.start()].strip().rstrip(',')
                store['street_address'] = street_part
                store['address'] = part.strip()
                continue
            
            # Check for phone (Finnish format: 010 xxx xxxx or +358...)
            phone_match = re.search(r'\b((?:010|\+358|0\d{1,2})[\s-]?\d{3}[\s-]?\d{3,4})\b', part)
            if phone_match and not store['phone']:
                store['phone'] = phone_match.group(1)
                continue
            
            # Check for email
            email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', part)
            if email_match and not store['email']:
                store['email'] = email_match.group(0)
                continue
            
            # Check for opening hours (contains day abbreviations like Ma-Pe, La, Su)
            if re.search(r'\b(Ma|Ti|Ke|To|Pe|La|Su|ma-pe|ma-su)\b', part, re.IGNORECASE):
                if store['opening_hours']:
                    store['opening_hours'] += "; " + part
                else:
                    store['opening_hours'] = part
    
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
        "latitude": 0.0,
        "longitude": 0.0,
        "url": store.get("url", ""),
        "phone": store.get("phone", "").strip(),
        "email": store.get("email", "").strip(),
        "opening_hours": store.get("opening_hours", "").strip(),
        "operator": "Kanniston Leipomo",
    }


async def scrape_stores():
    """Main scraping function."""
    logger.info("Starting Kanniston Leipomo store scraper")
    logger.info(f"Scraping {len(STORE_SLUGS)} known stores")
    
    async with AsyncSession() as session:
        stores = []
        
        for slug in STORE_SLUGS:
            url = f"{BASE_URL}/{slug}"
            logger.info(f"Fetching: {slug} - {url}")
            
            store_html = await fetch_page(session, url)
            
            if store_html:
                store = parse_store_data(store_html, url, slug)
                stores.append(normalize_store(store))
            else:
                logger.warning(f"Failed to fetch {slug}")
            
            # Small delay
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
        "address", "phone", "email", "opening_hours", "url", "operator"
    ]
    df = df[[c for c in column_order if c in df.columns]]
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_dir = OUTPUT_DIR / "excel"
    excel_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = excel_dir / f"kanniston_leipomo_{timestamp}.xlsx"
    df.to_excel(output_file, index=False)
    logger.info(f"Saved {len(stores)} stores to {output_file}")
    
    # Print summary
    print("\n" + "="*60)
    print("KANNISTON LEIPOMO SCRAPING RESULTS")
    print("="*60)
    print(f"Total stores scraped: {len(stores)}")
    print(f"\nStores with addresses: {sum(1 for s in stores if s.get('address'))}")
    print(f"Stores with phone: {sum(1 for s in stores if s.get('phone'))}")
    print(f"Stores with email: {sum(1 for s in stores if s.get('email'))}")
    print(f"Stores with hours: {sum(1 for s in stores if s.get('opening_hours'))}")
    print(f"\nOutput file: {output_file}")
    print("="*60)
    
    # Show data
    print("\nStore data:")
    print(df[['name', 'street_address', 'postal_code', 'city']].to_string())


async def main():
    """Main entry point."""
    stores = await scrape_stores()
    save_to_excel(stores)


if __name__ == "__main__":
    asyncio.run(main())
