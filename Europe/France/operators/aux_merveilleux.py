"""
Aux Merveilleux de Fred Bakery Scraper - France
Extracts outlet data from HTML marker divs on the addresses page.

Data structure:
<div class="marker" data-lat="50.6272867" data-lng="3.0490622">
    <h3>Store Name</h3>
    <p><em>Address</em></p>
</div>
"""
import asyncio
import json
import re
from pathlib import Path
from datetime import datetime
from html import unescape
import pandas as pd

from curl_cffi.requests import AsyncSession
from loguru import logger


# ============================================================================
# Configuration
# ============================================================================

ADDRESSES_URL = "https://www.auxmerveilleux.fr/adresses/"


# ============================================================================
# Data Extraction
# ============================================================================

def extract_stores(html: str) -> list[dict]:
    """
    Extract store data from HTML marker divs.
    
    Pattern:
    <div class="marker" data-js="marker" data-lat="..." data-lng="...">
        <h3>Name</h3>
        <p><em>Address</em></p>
    </div>
    
    Args:
        html: Raw HTML content
        
    Returns:
        List of store dictionaries
    """
    stores = []
    
    # Pattern to match marker divs with their content
    marker_pattern = re.compile(
        r'<div[^>]*class="[^"]*marker[^"]*"[^>]*'
        r'data-lat="([^"]+)"[^>]*'
        r'data-lng="([^"]+)"[^>]*>'
        r'([\s\S]*?)</div>',
        re.IGNORECASE
    )
    
    for match in marker_pattern.finditer(html):
        lat = match.group(1).strip()
        lng = match.group(2).strip()
        content = match.group(3)
        
        store = {
            "latitude": lat,
            "longitude": lng,
            "name": "",
            "address": "",
        }
        
        # Extract name from h3
        name_match = re.search(r'<h3[^>]*>(.*?)</h3>', content, re.IGNORECASE | re.DOTALL)
        if name_match:
            name = name_match.group(1).strip()
            # Clean HTML entities
            name = unescape(name)
            # Remove HTML tags
            name = re.sub(r'<[^>]+>', '', name)
            store["name"] = name.strip()
        
        # Extract address from p > em
        addr_match = re.search(r'<em[^>]*>(.*?)</em>', content, re.IGNORECASE | re.DOTALL)
        if addr_match:
            addr = addr_match.group(1).strip()
            addr = unescape(addr)
            addr = re.sub(r'<[^>]+>', '', addr)
            store["address"] = addr.strip()
        
        stores.append(store)
    
    logger.info(f"Extracted {len(stores)} stores from HTML")
    return stores


def parse_address(address: str) -> dict:
    """
    Parse address string to extract street, postal code, and city.
    
    French address format: "Street PostalCode City"
    Example: "336 rue Léon Gambetta 59800 Lille"
    
    Args:
        address: Full address string
        
    Returns:
        Dict with street, postal_code, city
    """
    data = {
        "street_address": "",
        "postal_code": "",
        "city": "",
    }
    
    if not address:
        return data
    
    # Find French postal code (5 digits)
    postal_match = re.search(r'(\d{5})', address)
    if postal_match:
        data["postal_code"] = postal_match.group(1)
        
        # Split around postal code
        parts = address.split(postal_match.group(1), 1)
        if len(parts) >= 1:
            data["street_address"] = parts[0].strip().rstrip(',').strip()
        if len(parts) >= 2:
            data["city"] = parts[1].strip().lstrip(',').strip()
    else:
        data["street_address"] = address
    
    return data


def normalize_store(store: dict) -> dict:
    """
    Normalize store data to standard format.
    
    Args:
        store: Raw store data from HTML
        
    Returns:
        Normalized bakery dictionary
    """
    # Parse address
    addr_parts = parse_address(store.get("address", ""))
    
    # Parse coordinates
    try:
        lat = float(store.get("latitude", 0))
        lon = float(store.get("longitude", 0))
    except (ValueError, TypeError):
        lat = 0.0
        lon = 0.0
    
    return {
        "store_code": "",
        "name": f"Aux Merveilleux {store.get('name', '')}".strip(),
        "city": addr_parts["city"],
        "postal_code": addr_parts["postal_code"],
        "street_address": addr_parts["street_address"],
        "address": store.get("address", ""),
        "country": "France",
        "latitude": lat,
        "longitude": lon,
        "url": ADDRESSES_URL,
        "phone": "",
        "email": "",
        "opening_hours": {},
        "operator": "Aux Merveilleux de Fred",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class AuxMerveilleuxScraper:
    """Scraper for Aux Merveilleux bakeries."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.results = []
    
    async def run(self) -> list[dict]:
        """
        Run the scraper.
        
        Returns:
            List of bakery data dictionaries
        """
        logger.info("=" * 60)
        logger.info("Starting Aux Merveilleux Scraper")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            logger.info(f"Fetching: {ADDRESSES_URL}")
            
            response = await session.get(ADDRESSES_URL, timeout=60)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch: HTTP {response.status_code}")
                return []
            
            logger.info(f"Page size: {len(response.text)} bytes")
            
            # Extract stores
            stores = extract_stores(response.text)
            
            if not stores:
                logger.error("No stores found!")
                return []
            
            # Normalize stores
            self.results = [normalize_store(s) for s in stores]
            
            # Count statistics
            with_city = sum(1 for r in self.results if r.get("city"))
            with_postal = sum(1 for r in self.results if r.get("postal_code"))
            
            logger.info(f"Normalized: {len(self.results)} bakeries")
            logger.info(f"With city: {with_city}, With postal: {with_postal}")
        
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
        json_path = json_dir / f"aux_merveilleux_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"aux_merveilleux_{timestamp}.xlsx"
        
        flat_results = []
        for r in results:
            flat = r.copy()
            hours = flat.pop("opening_hours", {})
            flat["opening_hours_json"] = json.dumps(hours, ensure_ascii=False)
            flat_results.append(flat)
        
        df = pd.DataFrame(flat_results)
        df.to_excel(excel_path, index=False)
        logger.success(f"Excel saved: {excel_path}")
        
        return json_path, excel_path


async def main():
    """Main entry point."""
    # Setup logging
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
        output_dir / "aux_merveilleux.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        encoding="utf-8",
    )
    
    # Run scraper
    scraper = AuxMerveilleuxScraper(output_dir=output_dir)
    
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
