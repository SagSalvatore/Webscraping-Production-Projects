"""
Maison Bécam Bakery Scraper - France
Extracts outlet data from CloseBy.co embed API.

API endpoint: https://www.closeby.co/embed/{mapKey}/locations
Returns JSON array of location objects.
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

# CloseBy.co embed map key
MAP_KEY = "565a516ff139f04494b93d2161a9620e"
API_URL = f"https://www.closeby.co/embed/{MAP_KEY}/locations"


# ============================================================================
# Data Extraction
# ============================================================================

def parse_address(address: str) -> dict:
    """
    Parse address string to extract street, postal code, and city.
    
    French address format: "Street, PostalCode City, Country" or similar
    Example: "Ecoparc du Buisson, 49073 Beaucouzé, France"
    
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
    
    # Remove country suffix
    address = re.sub(r',?\s*France\s*$', '', address, flags=re.IGNORECASE)
    
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
        # No postal code, use comma split
        parts = [p.strip() for p in address.split(',')]
        if len(parts) >= 2:
            data["street_address"] = parts[0]
            data["city"] = parts[-1]
        else:
            data["street_address"] = address
    
    return data


def normalize_location(loc: dict) -> dict:
    """
    Normalize location data from CloseBy.co API.
    
    API fields:
    - title: Store name
    - address_full: Full address
    - latitude, longitude: Coordinates
    - phone_number: Phone
    - email: Email
    - website: Website URL
    
    Args:
        loc: Raw location data from API
        
    Returns:
        Normalized bakery dictionary
    """
    # Get address and parse it
    address = loc.get("address_full", "") or ""
    addr_parts = parse_address(address)
    
    # Get coordinates
    try:
        lat = float(loc.get("latitude") or 0)
        lon = float(loc.get("longitude") or 0)
    except (ValueError, TypeError):
        lat = 0.0
        lon = 0.0
    
    # Get store name
    name = loc.get("title", "") or ""
    
    return {
        "store_code": str(loc.get("id", "")),
        "name": name,
        "city": addr_parts["city"],
        "postal_code": addr_parts["postal_code"],
        "street_address": addr_parts["street_address"],
        "address": address,
        "country": "France",
        "latitude": lat,
        "longitude": lon,
        "url": loc.get("website", "") or "https://maisonbecam.com/",
        "phone": loc.get("phone_number", "") or "",
        "email": loc.get("email", "") or "",
        "opening_hours": {},
        "operator": "Maison Bécam",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class MaisonBecamScraper:
    """Scraper for Maison Bécam bakeries via CloseBy.co API."""
    
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
        logger.info("Starting Maison Bécam Scraper")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            logger.info(f"Fetching API: {API_URL}")
            
            response = await session.get(API_URL, timeout=60)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch: HTTP {response.status_code}")
                return []
            
            logger.info(f"Response size: {len(response.text)} bytes")
            
            try:
                data = response.json()
                
                # Handle different response structures
                if isinstance(data, list):
                    locations = data
                elif isinstance(data, dict):
                    # CloseBy may return dict with locations key or items directly
                    if "locations" in data:
                        locations = data["locations"]
                    elif "items" in data:
                        locations = data["items"]
                    else:
                        # Values might be the locations if dict keys are IDs
                        locations = list(data.values())
                else:
                    locations = []
                
                logger.success(f"Fetched {len(locations)} locations from API")
            except json.JSONDecodeError as e:
                logger.error(f"JSON parse error: {e}")
                return []
            
            if not locations:
                logger.error("No locations found!")
                return []
            
            # Log sample structure
            if locations and isinstance(locations[0], dict):
                logger.debug(f"Sample keys: {list(locations[0].keys())}")
            
            # Normalize locations
            self.results = [normalize_location(loc) for loc in locations if isinstance(loc, dict)]
            
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
        json_path = json_dir / f"maison_becam_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"maison_becam_{timestamp}.xlsx"
        
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
        output_dir / "maison_becam.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        encoding="utf-8",
    )
    
    # Run scraper
    scraper = MaisonBecamScraper(output_dir=output_dir)
    
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
