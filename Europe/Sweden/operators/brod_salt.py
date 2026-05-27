"""
Bröd & Salt Bakery Scraper - Sweden
Extracts outlet data from JavaScript locations object on the page.

Data structure:
const locations = {
    Stockholm: [
        {
            name: "Store Name",
            position: { lat: 59.3389, lng: 18.0361 },
            address: "Full Address",
            phone: "phone",
            email: "email",
            cafe_hours: "hours",
            map_link: "google maps url"
        },
        ...
    ],
    Göteborg: [...],
    ...
}
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

STORE_URL = "https://brodsalt.se/"


# ============================================================================
# Data Extraction
# ============================================================================

def extract_locations_js(html: str) -> dict:
    """
    Extract the locations JavaScript object from the HTML.
    
    Pattern: const locations = { ... }
    Uses bracket counting for reliable extraction.
    
    Args:
        html: Raw HTML content
        
    Returns:
        Dictionary of locations by city
    """
    # Find the start of locations object
    pattern = r'const\s+locations\s*=\s*\{'
    match = re.search(pattern, html)
    if not match:
        logger.error("Could not find locations object in HTML")
        return {}
    
    # Extract using bracket counting
    js_start = html.find('{', match.start())
    bracket_count = 0
    js_end = js_start
    
    for i, char in enumerate(html[js_start:], js_start):
        if char == '{':
            bracket_count += 1
        elif char == '}':
            bracket_count -= 1
            if bracket_count == 0:
                js_end = i + 1
                break
    
    js_obj = html[js_start:js_end]
    logger.info(f"Extracted JS object: {len(js_obj)} chars")
    
    # Convert JavaScript object to valid JSON
    # Use state-based approach to only quote unquoted keys
    result = []
    i = 0
    in_string = False
    string_char = None
    
    while i < len(js_obj):
        char = js_obj[i]
        
        # Track string state
        if char in '"\'':
            if not in_string:
                in_string = True
                string_char = char
            elif char == string_char and (i == 0 or js_obj[i-1] != '\\'):
                in_string = False
                string_char = None
        
        # Only transform keys outside of strings
        if not in_string:
            # Look for unquoted key pattern: word followed by :
            # Match at start of object/array or after , or {
            if char.isalpha() or char == '_':
                # Check if this is a key (word followed by optional space and colon)
                key_match = re.match(r'^(\w+)\s*:', js_obj[i:])
                if key_match:
                    key = key_match.group(1)
                    result.append(f'"{key}"')
                    i += len(key)
                    continue
        
        result.append(char)
        i += 1
    
    json_str = ''.join(result)
    
    # Remove trailing commas before ] and }
    json_str = re.sub(r',(\s*[\]\}])', r'\1', json_str)
    
    try:
        locations = json.loads(json_str)
        logger.success(f"Parsed locations: {len(locations)} cities")
        return locations
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        # Save for debugging
        with open("sweden_debug.json", "w", encoding="utf-8") as f:
            f.write(json_str[:2000])
        return {}


def normalize_store(store: dict, city: str) -> dict:
    """
    Normalize store data to standard format.
    
    Args:
        store: Raw store data from JS
        city: City name
        
    Returns:
        Normalized bakery dictionary
    """
    position = store.get("position", {})
    
    try:
        lat = float(position.get("lat", 0))
        lng = float(position.get("lng", 0))
    except (ValueError, TypeError):
        lat = 0.0
        lng = 0.0
    
    # Extract postal code from address (Swedish format: XXXXX)
    address = store.get("address", "")
    postal_match = re.search(r'(\d{3}\s*\d{2})', address)
    postal_code = postal_match.group(1).replace(" ", "") if postal_match else ""
    
    return {
        "name": f"Bröd & Salt {store.get('name', '')}".strip(),
        "city": city,
        "postal_code": postal_code,
        "address": address,
        "country": "Sweden",
        "latitude": lat,
        "longitude": lng,
        "url": store.get("map_link", "https://brodsalt.se/"),
        "phone": store.get("phone", ""),
        "email": store.get("email", ""),
        "cafe_hours": store.get("cafe_hours", ""),
        "coworking_hours": store.get("coworking_hours", ""),
        "operator": "Bröd & Salt",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class BrodSaltScraper:
    """Scraper for Bröd & Salt bakeries - Sweden."""
    
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
        logger.info("Starting Bröd & Salt Scraper (Sweden)")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            logger.info(f"Fetching: {STORE_URL}")
            
            response = await session.get(STORE_URL, timeout=60)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch: HTTP {response.status_code}")
                return []
            
            logger.info(f"Page size: {len(response.text)} bytes")
            
            # Extract locations
            locations = extract_locations_js(response.text)
            
            if not locations:
                logger.error("No locations found!")
                return []
            
            # Flatten all stores from all cities
            for city, stores in locations.items():
                logger.info(f"  {city}: {len(stores)} stores")
                for store in stores:
                    normalized = normalize_store(store, city)
                    self.results.append(normalized)
            
            logger.success(f"Total stores: {len(self.results)}")
        
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
        json_path = json_dir / f"brod_salt_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"brod_salt_{timestamp}.xlsx"
        df = pd.DataFrame(results)
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
        output_dir / "brod_salt.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        encoding="utf-8",
    )
    
    # Run scraper
    scraper = BrodSaltScraper(output_dir=output_dir)
    
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
