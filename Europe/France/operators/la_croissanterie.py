"""
La Croissanterie Bakery Scraper - France
Extracts outlet data from window.stores JavaScript array on store locator page.
Parses embedded HTML template for each store to extract details.

Data structure:
- window.stores = [{id, latitude, longitude, template}, ...]
- template contains: name, address, URL
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

STORE_LOCATOR_URL = "https://www.lacroissanterie.fr/sandwicherie-boulangerie/"


# ============================================================================
# Data Extraction
# ============================================================================

def extract_stores_json(html: str) -> list[dict]:
    """
    Extract stores JSON array from window.stores variable.
    Uses bracket counting for reliable extraction of large arrays.
    
    Args:
        html: Raw HTML content
        
    Returns:
        List of store dictionaries
    """
    # Find the start of window.stores array
    idx = html.find('window.stores = [')
    
    if idx == -1:
        logger.error("Could not find window.stores in HTML!")
        return []
    
    # Start from the opening bracket
    start = idx + len('window.stores = ')
    
    # Use bracket counting to find the end
    bracket_count = 0
    end = start
    
    for i, char in enumerate(html[start:start+1000000]):  # Limit to 1MB
        if char == '[':
            bracket_count += 1
        elif char == ']':
            bracket_count -= 1
            if bracket_count == 0:
                end = start + i + 1
                break
    
    json_str = html[start:end]
    logger.info(f"Found stores JSON: {len(json_str)} characters")
    
    try:
        stores = json.loads(json_str)
        logger.success(f"Extracted {len(stores)} stores from JSON")
        return stores
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return []


def parse_store_template(template: str) -> dict:
    """
    Parse store template HTML to extract details.
    
    Template structure:
    <div id="..." class="store" data-lat="..." data-lng="...">
        <span class="...font-semibold...">Name</span>
        <span class="address...">Full Address</span>
        <a href="URL">...</a>
    </div>
    
    Args:
        template: HTML template string
        
    Returns:
        Dict with name, address, url
    """
    data = {
        "name": "",
        "address": "",
        "url": "",
    }
    
    # Unescape HTML entities and fix escaped slashes
    html = unescape(template.replace(r'\/', '/'))
    
    # Extract name (first span with font-semibold)
    name_match = re.search(
        r'<span[^>]*class="[^"]*font-semibold[^"]*"[^>]*>\s*(.*?)\s*</span>',
        html, re.IGNORECASE | re.DOTALL
    )
    if name_match:
        data["name"] = name_match.group(1).strip()
        # Clean HTML tags
        data["name"] = re.sub(r'<[^>]+>', '', data["name"]).strip()
    
    # Extract address (span with class containing "address")
    addr_match = re.search(
        r'<span[^>]*class="[^"]*address[^"]*"[^>]*>\s*(.*?)\s*</span>',
        html, re.IGNORECASE | re.DOTALL
    )
    if addr_match:
        data["address"] = addr_match.group(1).strip()
        data["address"] = re.sub(r'<[^>]+>', '', data["address"]).strip()
    
    # Extract URL
    url_match = re.search(
        r'<a\s+href="([^"]+)"[^>]*>\s*Plus d\'infos',
        html, re.IGNORECASE
    )
    if url_match:
        data["url"] = url_match.group(1)
    
    return data


def parse_address(address: str) -> dict:
    """
    Parse address string to extract street, postal code, and city.
    
    Address formats:
    - "Aire de Creux Moreau - sens Province, 21360, Bligny Sur Ouche"
    - "N5, Le Lamentin 97232, Martinique"
    - "Route de la Gabarre - 97139 Les Abymes - Guadeloupe"
    
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
    
    # Try pattern: "..., POSTAL, City" or "... POSTAL City"
    # French postal codes are 5 digits
    postal_match = re.search(r'(\d{5})', address)
    if postal_match:
        data["postal_code"] = postal_match.group(1)
        
        # Split address around postal code
        parts = re.split(r'\d{5}', address, maxsplit=1)
        if len(parts) >= 1:
            # Street is before postal code
            street = parts[0].strip().rstrip(',').rstrip('-').strip()
            data["street_address"] = street
        
        if len(parts) >= 2:
            # City is after postal code
            city = parts[1].strip().lstrip(',').strip()
            # Remove trailing region names like "Martinique", "Guadeloupe", "France"
            city = re.sub(r'\s*[-,]\s*(Martinique|Guadeloupe|Guyane\s+Française|France)$', '', city, flags=re.IGNORECASE)
            data["city"] = city.strip()
    else:
        # No postal code found, just use full address
        data["street_address"] = address
    
    return data


def normalize_store(store: dict) -> dict:
    """
    Normalize store data to standard format.
    
    Args:
        store: Raw store data from JSON
        
    Returns:
        Normalized bakery dictionary
    """
    # Parse template HTML
    template_data = parse_store_template(store.get("template", ""))
    
    # Parse address
    addr_parts = parse_address(template_data.get("address", ""))
    
    # Get coordinates - note: lat/lon might be swapped in source data
    lat_str = store.get("latitude", "0")
    lon_str = store.get("longitude", "0")
    
    try:
        lat = float(lat_str)
        lon = float(lon_str)
    except (ValueError, TypeError):
        lat = 0.0
        lon = 0.0
    
    return {
        "store_code": str(store.get("id", "")),
        "name": f"La Croissanterie {template_data.get('name', '')}".strip(),
        "city": addr_parts["city"],
        "postal_code": addr_parts["postal_code"],
        "street_address": addr_parts["street_address"],
        "address": template_data.get("address", ""),
        "country": "France",
        "latitude": lat,
        "longitude": lon,
        "url": template_data.get("url", ""),
        "phone": "",
        "email": "",
        "opening_hours": {},
        "operator": "La Croissanterie",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class LaCroissanterieScraper:
    """Scraper for La Croissanterie bakeries."""
    
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
        logger.info("Starting La Croissanterie Scraper")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            logger.info(f"Fetching store locator: {STORE_LOCATOR_URL}")
            
            response = await session.get(STORE_LOCATOR_URL, timeout=60)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch: HTTP {response.status_code}")
                return []
            
            logger.info(f"Page size: {len(response.text)} bytes")
            
            # Extract stores from JSON
            stores = extract_stores_json(response.text)
            
            if not stores:
                logger.error("No stores found!")
                return []
            
            # Normalize all stores
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
        json_path = json_dir / f"la_croissanterie_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"la_croissanterie_{timestamp}.xlsx"
        
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
        output_dir / "la_croissanterie.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        encoding="utf-8",
    )
    
    # Run scraper
    scraper = LaCroissanterieScraper(output_dir=output_dir)
    
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
