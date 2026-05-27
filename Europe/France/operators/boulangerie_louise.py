"""
Boulangerie Louise Scraper - France
Extracts outlet data from var locations JavaScript array on store locator page.

Data format:
var locations = [
    ['Name', lat, lng, 'Address', 'URL'],
    ...
]
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

STORE_LOCATOR_URL = "https://www.boulangerielouise.com/trouvez-une-boulangerie/"


# ============================================================================
# Data Extraction
# ============================================================================

def extract_locations(html: str) -> list[list]:
    """
    Extract locations array from JavaScript variable.
    
    Format: var locations = [['Name', lat, lng, 'Address', 'URL'], ...]
    
    Args:
        html: Raw HTML content
        
    Returns:
        List of location arrays
    """
    # Find var locations = [...]
    pattern = re.compile(
        r'var\s+locations\s*=\s*\[([\s\S]*?)\];',
        re.IGNORECASE
    )
    
    match = pattern.search(html)
    
    if not match:
        logger.error("Could not find var locations in HTML!")
        return []
    
    array_content = match.group(1)
    logger.info(f"Found locations array: {len(array_content)} characters")
    
    # Parse each location entry
    # Each entry: ['Name', lat, lng, 'Address', 'URL']
    locations = []
    
    # Pattern to match individual entries
    # Handle both single and double quotes, and HTML entities
    entry_pattern = re.compile(
        r"\['([^']*)',\s*([-\d.]+),\s*([-\d.]+),\s*'([^']*)',\s*'([^']*)'\]",
        re.IGNORECASE
    )
    
    for match in entry_pattern.finditer(array_content):
        name = match.group(1)
        lat = match.group(2)
        lng = match.group(3)
        address = match.group(4)
        url = match.group(5)
        
        locations.append([name, lat, lng, address, url])
    
    logger.success(f"Extracted {len(locations)} locations")
    return locations


def parse_address(address: str) -> dict:
    """
    Parse address string to extract street, postal code, and city.
    
    Address formats:
    - "Street, 12345 City, France"
    - "Street, City"
    - "12345 City"
    
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
    
    # Unescape HTML entities
    address = unescape(address)
    
    # Remove ", France" suffix
    address = re.sub(r',?\s*France$', '', address, flags=re.IGNORECASE)
    
    # Try to find postal code (5 digits)
    postal_match = re.search(r'(\d{5})', address)
    if postal_match:
        data["postal_code"] = postal_match.group(1)
        
        # City is after postal code
        parts = address.split(postal_match.group(1), 1)
        if len(parts) >= 1:
            data["street_address"] = parts[0].strip().rstrip(',').strip()
        if len(parts) >= 2:
            city = parts[1].strip().lstrip(',').strip()
            # Clean up city name
            city = re.sub(r'^\s*\d+\s*', '', city)  # Remove leading numbers
            data["city"] = city.strip().rstrip(',').strip()
    else:
        # No postal code, try to split by comma
        parts = [p.strip() for p in address.split(',')]
        if len(parts) >= 2:
            data["street_address"] = parts[0]
            data["city"] = parts[-1]
        else:
            data["street_address"] = address
    
    return data


def normalize_location(loc: list) -> dict:
    """
    Normalize location array to standard format.
    
    Args:
        loc: [name, lat, lng, address, url]
        
    Returns:
        Normalized bakery dictionary
    """
    name = unescape(loc[0]) if loc[0] else ""
    lat = float(loc[1]) if loc[1] else 0.0
    lng = float(loc[2]) if loc[2] else 0.0
    address = unescape(loc[3]) if loc[3] else ""
    url = loc[4] if len(loc) > 4 else ""
    
    # Parse address
    addr_parts = parse_address(address)
    
    return {
        "store_code": "",
        "name": name,
        "city": addr_parts["city"],
        "postal_code": addr_parts["postal_code"],
        "street_address": addr_parts["street_address"],
        "address": address,
        "country": "France",
        "latitude": lat,
        "longitude": lng,
        "url": url,
        "phone": "",
        "email": "",
        "opening_hours": {},
        "operator": "Boulangerie Louise",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class BoulangerieLouiseScraper:
    """Scraper for Boulangerie Louise bakeries."""
    
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
        logger.info("Starting Boulangerie Louise Scraper")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            logger.info(f"Fetching store locator: {STORE_LOCATOR_URL[:80]}...")
            
            response = await session.get(STORE_LOCATOR_URL, timeout=60)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch: HTTP {response.status_code}")
                return []
            
            logger.info(f"Page size: {len(response.text)} bytes")
            
            # Extract locations
            locations = extract_locations(response.text)
            
            if not locations:
                logger.error("No locations found!")
                return []
            
            # Normalize all locations
            self.results = [normalize_location(loc) for loc in locations]
            
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
        json_path = json_dir / f"boulangerie_louise_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"boulangerie_louise_{timestamp}.xlsx"
        
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
        output_dir / "boulangerie_louise.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        encoding="utf-8",
    )
    
    # Run scraper
    scraper = BoulangerieLouiseScraper(output_dir=output_dir)
    
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
