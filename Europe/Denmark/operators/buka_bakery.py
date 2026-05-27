"""
Buka Bakery Scraper - Denmark
Fetch and parse store data from buka-bakery.com/locations
"""
import asyncio
import json
import re
from pathlib import Path
from datetime import datetime
import pandas as pd

from curl_cffi.requests import AsyncSession
from loguru import logger


# ============================================================================
# Configuration
# ============================================================================

STORE_URL = "https://www.buka-bakery.com/locations"


# ============================================================================
# Data Extraction - Updated based on HTML inspection
# ============================================================================

def extract_stores(html: str) -> list[dict]:
    """
    Extract stores: Look for sections with h2 store names, then
    extract address from Google Maps links (underlined links) and findsmiley links.
    Phone numbers are ignored per user request.
    """
    stores = []
    
    # Find sections - typically split by h2 tags containing store names
    # Pattern: h2 with uppercase store name (STRØGET, STORE KONGENSGADE, etc.)
    h2_pattern = r'<h2[^>]*>.*?<span[^>]*>([^<]+)</span>.*?</h2>'
    store_names = re.findall(h2_pattern, html, re.DOTALL | re.IGNORECASE)
    
    logger.info(f"Found {len(store_names)} h2 store names")
    
    # Find Google Maps links (these contain the addresses)
    # Pattern: <a href="https://maps.google.com/..." with underlined text
    # The link text contains the full address
    maps_pattern = r'<a[^>]*href="https://maps\.google\.com[^"]*"[^>]*>([^<]+(?:<br[^>]*>[^<]+)*)</a>'
    maps_links = re.findall(maps_pattern, html, re.DOTALL)
    
    logger.info(f"Found {len(maps_links)} Google Maps address links")
    
    # Find findsmiley links
    smiley_pattern = r'href="(https://www\.findsmiley\.dk/\d+)"'
    smiley_links = re.findall(smiley_pattern, html)
    
    logger.info(f"Found {len(smiley_links)} findsmiley links")
    
    # Try to match them by position in HTML
    # We'll search for each store name and extract data around it
    for name in store_names:
        # Find position of this store name in HTML
        name_clean = re.sub(r'<[^>]+>', '', name).strip()
        if not name_clean:
            continue
            
        # Search for this name's position
        name_pos = html.find(name)
        if name_pos == -1:
            continue
        
        # Get next 3000 chars after store name
        section = html[name_pos:name_pos+3000]
        
        store = {
            "name": name_clean,
            "address": "",
            "smiley_url": ""
        }
        
        # Find address in this section (from Google Maps link)
        addr_match = re.search(r'<a[^>]*href="https://maps\.google\.com[^"]*"[^>]*>([^<]+(?:<br[^>]*>[^<]+)*)</a>', section, re.DOTALL)
        if addr_match:
            addr_text = addr_match.group(1)
            # Replace <br> tags with comma and space
            addr_text = re.sub(r'<br[^>]*>', ', ', addr_text)
            # Remove any remaining HTML tags
            store["address"] = re.sub(r'<[^>]+>', '', addr_text).strip()
        
        # Find smiley link in this section
        smiley_match = re.search(r'href="(https://www\.findsmiley\.dk/\d+)"', section)
        if smiley_match:
            store["smiley_url"] = smiley_match.group(1)
        
        stores.append(store)
    
    return stores


def normalize_store(store: dict) -> dict:
    """Normalize store data to standard format."""
    address = store.get("address", "")
    
    # Parse address for postal code and city
    postal_code = ""
    city = ""
    street = address
    
    # Look for 4-digit postal code
    postal_match = re.search(r',?\s*(\d{4})\s+([^,]+)$', address)
    if postal_match:
        postal_code = postal_match.group(1)
        city = postal_match.group(2).strip()
        street = address[:postal_match.start()].strip().rstrip(',')
    
    return {
        "name": f"Buka Bakery {store.get('name', '')}".strip(),
        "city": city,
        "postal_code": postal_code,
        "street_address": street,
        "address": address,
        "country": "Denmark",
        "latitude": 0.0,
        "longitude": 0.0,
        "url": STORE_URL,
        "phone": "",
        "email": "",
        "control_report_url": store.get("smiley_url", ""),
        "operator": "Buka Bakery",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class BukaBakeryScraper:
    """Scraper for Buka Bakery - Denmark."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.results = []
    
    async def run(self) -> list[dict]:
        """Run the scraper."""
        logger.info("=" * 60)
        logger.info("Starting Buka Bakery Scraper (Denmark)")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            logger.info(f"Fetching: {STORE_URL}")
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5"
            }
            
            response = await session.get(STORE_URL, headers=headers, timeout=60)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch: HTTP {response.status_code}")
                return []
            
            logger.info(f"Page size: {len(response.text)} bytes")
            
            # Save HTML for debugging
            with open(self.output_dir / "buka_page2.html", "w", encoding="utf-8") as f:
                f.write(response.text)
            logger.info("Saved HTML to buka_page2.html")
            
            # Extract stores
            stores = extract_stores(response.text)
            logger.info(f"Extracted {len(stores)} stores")
            
            if stores:
                # Show first store details
                logger.info(f"First store: {stores[0]}")
            
            # Normalize
            self.results = [normalize_store(s) for s in stores]
            
            # Stats
            with_city = sum(1 for r in self.results if r.get("city"))
            with_smiley = sum(1 for r in self.results if r.get("control_report_url"))
            logger.info(f"With city: {with_city}, With smiley URL: {with_smiley}")
        
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"Scraping completed in {elapsed:.1f}s")
        
        return self.results
    
    def save_results(self, results: list[dict]):
        """Save results to JSON and Excel files."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create output directories
        json_dir = self.output_dir / "json"
        excel_dir = self.output_dir / "excel"
        json_dir.mkdir(parents=True, exist_ok=True)
        excel_dir.mkdir(parents=True, exist_ok=True)
        
        # Save JSON
        json_path = json_dir / f"buka_bakery_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"buka_bakery_{timestamp}.xlsx"
        df = pd.DataFrame(results)
        df.to_excel(excel_path, index=False)
        logger.success(f"Excel saved: {excel_path}")
        
        return json_path, excel_path


async def main():
    """Main entry point."""
    output_dir = Path(__file__).parent.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Configure loguru
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{function}</cyan> - <level>{message}</level>",
        level="INFO",
        colorize=True,
    )
    
    # Run scraper
    scraper = BukaBakeryScraper(output_dir=output_dir)
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
