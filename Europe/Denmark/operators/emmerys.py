"""
Emmerys Bakery Scraper - Denmark
Extracts outlet data from HTML page at emmerys.dk/aabningstider/

Data structure in HTML:
- h5 tags contain store name (e.g., "emmerys Egevangen 6")
- Following p tags contain: postal code + city, phone, and opening hours
- Stores are grouped by city (København, Århus)
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

STORE_URL = "https://emmerys.dk/aabningstider/"


# ============================================================================
# Data Extraction
# ============================================================================

def extract_stores(html: str) -> list[dict]:
    """
    Extract store data from HTML.
    
    Pattern:
    <h5>emmerys StoreName</h5>
    <p>PostalCode City<br />
    Telefon: Phone<br />
    OpeningHours</p>
    
    Args:
        html: Raw HTML content
        
    Returns:
        List of store dictionaries
    """
    stores = []
    
    # Find all but-col sections
    but_cols = re.findall(r'<div class="but-col">([\s\S]*?)</div>\s*</div>', html)
    
    if not but_cols:
        # Try alternative pattern - look for h5 followed by p
        but_cols = re.findall(r'class="but-col">([\s\S]*?)(?=<div class="but-col">|</div>\s*</div>\s*<h3|$)', html)
    
    logger.info(f"Found {len(but_cols)} but-col sections")
    
    # Also try to find current city context
    current_city = "København"
    
    # Find all h5 tags with store names
    h5_pattern = r'<h5>([^<]+)</h5>\s*<p>([^<]+(?:<br\s*/?>[^<]+)*)</p>'
    
    for match in re.finditer(h5_pattern, html):
        name = match.group(1).strip()
        details = match.group(2).strip()
        
        # Parse details
        lines = re.split(r'<br\s*/?>', details)
        lines = [line.strip() for line in lines if line.strip()]
        
        store = {
            "name": name,
            "city": "",
            "postal_code": "",
            "address": name.replace("emmerys ", "").strip(),
            "phone": "",
            "hours": "",
        }
        
        for line in lines:
            # Check for postal code (4 digits followed by city)
            postal_match = re.match(r'^(\d{4})\s+(.+)$', line)
            if postal_match:
                store["postal_code"] = postal_match.group(1)
                store["city"] = postal_match.group(2)
                continue
            
            # Check for phone
            phone_match = re.match(r'Telefon:\s*(.+)', line)
            if phone_match:
                store["phone"] = phone_match.group(1).strip()
                continue
            
            # Opening hours
            if re.match(r'^(Mandag|Tirsdag|Onsdag|Torsdag|Fredag|Lørdag|Søndag)', line):
                if store["hours"]:
                    store["hours"] += ", "
                store["hours"] += line
        
        if store["name"]:
            stores.append(store)
    
    return stores


def normalize_store(store: dict) -> dict:
    """
    Normalize store data to standard format.
    """
    # Build full address
    address = store.get("address", "")
    postal = store.get("postal_code", "")
    city = store.get("city", "")
    
    if postal and city:
        full_address = f"{address}, {postal} {city}"
    else:
        full_address = address
    
    return {
        "name": store.get("name", ""),
        "city": city,
        "postal_code": postal,
        "street_address": address,
        "address": full_address.strip(", "),
        "country": "Denmark",
        "latitude": 0.0,
        "longitude": 0.0,
        "url": STORE_URL,
        "phone": store.get("phone", ""),
        "email": "",
        "opening_hours": store.get("hours", ""),
        "operator": "Emmerys",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class EmmerysScraper:
    """Scraper for Emmerys bakeries - Denmark."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.results = []
    
    async def run(self) -> list[dict]:
        """Run the scraper."""
        logger.info("=" * 60)
        logger.info("Starting Emmerys Scraper (Denmark)")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            logger.info(f"Fetching: {STORE_URL}")
            
            response = await session.get(STORE_URL, timeout=60)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch: HTTP {response.status_code}")
                return []
            
            logger.info(f"Page size: {len(response.text)} bytes")
            
            # Save HTML for debugging
            with open(self.output_dir / "emmerys_page.html", "w", encoding="utf-8") as f:
                f.write(response.text)
            
            # Extract stores
            stores = extract_stores(response.text)
            logger.info(f"Extracted {len(stores)} stores")
            
            # Normalize
            self.results = [normalize_store(s) for s in stores]
            
            # Stats
            with_city = sum(1 for r in self.results if r.get("city"))
            with_phone = sum(1 for r in self.results if r.get("phone"))
            logger.info(f"With city: {with_city}, With phone: {with_phone}")
        
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
        json_path = json_dir / f"emmerys_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"emmerys_{timestamp}.xlsx"
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
    
    logger.add(
        output_dir / "emmerys.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        encoding="utf-8",
    )
    
    # Run scraper
    scraper = EmmerysScraper(output_dir=output_dir)
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
