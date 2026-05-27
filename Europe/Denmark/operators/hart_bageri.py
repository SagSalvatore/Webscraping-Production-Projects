"""
Hart Bageri Scraper - Denmark
Extracts outlet data from HTML page at hartbageri.com

Data structure in HTML:
- div.item contains each store
- h1 tags contain store name (e.g., "Carlsberg Byen")
- p tags contain: address and opening hours
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

STORE_URL = "https://hartbageri.com/"


# ============================================================================
# Data Extraction
# ============================================================================

def extract_stores(html: str) -> list[dict]:
    """
    Extract store data from HTML.
    
    Pattern:
    <div class="item">
        <h1>StoreName</h1>
        <p>Street Address<br />City, DK-PostalCode</p>
        <p>Opening Hours</p>
    </div>
    """
    stores = []
    
    # Find all item divs
    item_pattern = r'<div class="item">\s*([\s\S]*?)\s*<hr>'
    items = re.findall(item_pattern, html)
    
    logger.info(f"Found {len(items)} store items")
    
    for item in items:
        # Extract name from h1
        name_match = re.search(r'<h1>([^<]+)</h1>', item)
        if not name_match:
            continue
        
        name = name_match.group(1).strip()
        
        # Extract all p tags
        p_tags = re.findall(r'<p>([\s\S]*?)</p>', item)
        
        store = {
            "name": f"Hart Bageri {name}",
            "street_address": "",
            "city": "",
            "postal_code": "",
            "hours": "",
        }
        
        for p in p_tags:
            # Clean up HTML entities
            p_clean = p.replace('&#8211;', '-').replace('&amp;', '&').strip()
            
            # Check if this is address (contains DK- postal code)
            if 'DK-' in p_clean:
                lines = re.split(r'<br\s*/?>', p_clean)
                lines = [l.strip() for l in lines if l.strip()]
                
                if lines:
                    store["street_address"] = lines[0]
                
                if len(lines) > 1:
                    # Parse "City, DK-PostalCode"
                    city_postal = lines[1]
                    match = re.match(r'(.+?),\s*DK-(\d+)', city_postal)
                    if match:
                        store["city"] = match.group(1).strip()
                        store["postal_code"] = match.group(2).strip()
            
            # Check if this is opening hours
            elif re.search(r'(Open|Mon|Tue|Wed|Thu|Fri|Sat|Sun)', p_clean, re.IGNORECASE):
                hours = re.sub(r'<br\s*/?>', ', ', p_clean)
                store["hours"] = hours
        
        stores.append(store)
    
    return stores


def normalize_store(store: dict) -> dict:
    """Normalize store data to standard format."""
    street = store.get("street_address", "")
    postal = store.get("postal_code", "")
    city = store.get("city", "")
    
    if postal and city:
        full_address = f"{street}, DK-{postal} {city}"
    else:
        full_address = street
    
    return {
        "name": store.get("name", ""),
        "city": city,
        "postal_code": postal,
        "street_address": street,
        "address": full_address.strip(", "),
        "country": "Denmark",
        "latitude": 0.0,
        "longitude": 0.0,
        "url": STORE_URL,
        "phone": "",
        "email": "",
        "opening_hours": store.get("hours", ""),
        "operator": "Hart Bageri",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class HartBageriScraper:
    """Scraper for Hart Bageri - Denmark."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.results = []
    
    async def run(self) -> list[dict]:
        """Run the scraper."""
        logger.info("=" * 60)
        logger.info("Starting Hart Bageri Scraper (Denmark)")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            logger.info(f"Fetching: {STORE_URL}")
            
            response = await session.get(STORE_URL, timeout=60)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch: HTTP {response.status_code}")
                return []
            
            logger.info(f"Page size: {len(response.text)} bytes")
            
            # Extract stores
            stores = extract_stores(response.text)
            logger.info(f"Extracted {len(stores)} stores")
            
            # Normalize
            self.results = [normalize_store(s) for s in stores]
            
            # Stats
            with_city = sum(1 for r in self.results if r.get("city"))
            logger.info(f"With city: {with_city}")
        
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
        json_path = json_dir / f"hart_bageri_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"hart_bageri_{timestamp}.xlsx"
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
    scraper = HartBageriScraper(output_dir=output_dir)
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
