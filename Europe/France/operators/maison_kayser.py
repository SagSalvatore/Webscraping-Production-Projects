"""
Maison Kayser Bakery Scraper - France Only
Extracts outlet data from store locator page, filtering for France only.

Data attributes available:
- data-lat, data-lon: Coordinates
- data-store: Store ID
- data-title: Store name
- data-address: Street address
- data-zipcode: Postal code
- data-city: City
- data-permalink: Detail page URL
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

STORE_LOCATOR_URL = "https://maison-kayser.com/nos-boulangeries/"

# France bounding box (mainland + Corsica)
FRANCE_BOUNDS = {
    "lat_min": 41.0,  # Southern Corsica
    "lat_max": 51.5,  # Northern France
    "lon_min": -5.5,  # Western tip (Brittany)
    "lon_max": 10.0,  # Eastern border
}

# French overseas territories (Outre-Mer) - approximate bounds
OUTRE_MER_BOUNDS = [
    {"name": "Guadeloupe", "lat_min": 15.8, "lat_max": 16.5, "lon_min": -62.0, "lon_max": -61.0},
    {"name": "Martinique", "lat_min": 14.3, "lat_max": 14.9, "lon_min": -61.3, "lon_max": -60.8},
    {"name": "Reunion", "lat_min": -21.5, "lat_max": -20.8, "lon_min": 55.2, "lon_max": 55.9},
    {"name": "French Guiana", "lat_min": 2.0, "lat_max": 6.0, "lon_min": -55.0, "lon_max": -51.0},
    {"name": "Mayotte", "lat_min": -13.0, "lat_max": -12.6, "lon_min": 45.0, "lon_max": 45.4},
]


# ============================================================================
# Data Extraction
# ============================================================================

def is_in_france(lat: float, lon: float) -> bool:
    """Check if coordinates are in France (mainland or overseas)."""
    # Check mainland France
    if (FRANCE_BOUNDS["lat_min"] <= lat <= FRANCE_BOUNDS["lat_max"] and
        FRANCE_BOUNDS["lon_min"] <= lon <= FRANCE_BOUNDS["lon_max"]):
        return True
    
    # Check overseas territories
    for territory in OUTRE_MER_BOUNDS:
        if (territory["lat_min"] <= lat <= territory["lat_max"] and
            territory["lon_min"] <= lon <= territory["lon_max"]):
            return True
    
    return False


def extract_stores(html: str) -> list[dict]:
    """
    Extract store data from HTML.
    
    Store divs have format:
    <div class="store-item"
        data-lat="43.5535491"
        data-lon="7.019424"
        data-store="3190930"
        data-title="CANNES"
        data-address="2 Rue Jean Jaures"
        data-zipcode="06400"
        data-city="CANNES"
        data-permalink="https://..."
        ...>
    
    Args:
        html: Raw HTML content
        
    Returns:
        List of store dictionaries
    """
    stores = []
    
    # Pattern to match store-item divs with their attributes
    store_pattern = re.compile(
        r'<div\s+class="store-item"\s+[^>]*?>',
        re.IGNORECASE | re.DOTALL
    )
    
    store_divs = store_pattern.findall(html)
    logger.info(f"Found {len(store_divs)} store-item divs")
    
    for div in store_divs:
        # Extract individual attributes
        store = {}
        
        # Extract each data attribute
        for attr in ['lat', 'lon', 'store', 'title', 'address', 'zipcode', 'city', 'permalink', 'pcode']:
            match = re.search(rf'data-{attr}="([^"]*)"', div)
            if match:
                store[attr] = match.group(1).strip()
        
        # Only add if we have essential data
        if store.get('lat') and store.get('lon'):
            stores.append(store)
    
    logger.info(f"Extracted {len(stores)} stores with coordinates")
    return stores


def normalize_store(store: dict) -> dict:
    """
    Normalize store data to standard format.
    
    Args:
        store: Raw store data
        
    Returns:
        Normalized bakery dictionary
    """
    lat = float(store.get("lat", 0))
    lon = float(store.get("lon", 0))
    
    # Build full address
    street = store.get("address", "")
    postal = store.get("zipcode", "") or store.get("pcode", "")
    city = store.get("city", "") or store.get("title", "")
    
    address_parts = [p for p in [street, postal, city] if p]
    full_address = ", ".join(address_parts)
    
    return {
        "store_code": store.get("store", ""),
        "name": f"Maison Kayser {store.get('title', '')}".strip(),
        "city": city,
        "postal_code": postal,
        "street_address": street,
        "address": full_address,
        "country": "France",
        "latitude": lat,
        "longitude": lon,
        "url": store.get("permalink", ""),
        "phone": "",
        "email": "",
        "opening_hours": {},
        "operator": "Maison Kayser",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class MaisonKayserScraper:
    """Scraper for Maison Kayser bakeries - France only."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.results = []
    
    async def run(self) -> list[dict]:
        """
        Run the scraper.
        
        Returns:
            List of France bakery data dictionaries
        """
        logger.info("=" * 60)
        logger.info("Starting Maison Kayser Scraper - France Only")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            logger.info(f"Fetching store locator: {STORE_LOCATOR_URL}")
            
            response = await session.get(STORE_LOCATOR_URL, timeout=60)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch: HTTP {response.status_code}")
                return []
            
            logger.info(f"Page size: {len(response.text)} bytes")
            
            # Extract all stores
            all_stores = extract_stores(response.text)
            
            if not all_stores:
                logger.error("No stores found!")
                return []
            
            # Filter for France only
            france_stores = []
            for store in all_stores:
                try:
                    lat = float(store.get("lat", 0))
                    lon = float(store.get("lon", 0))
                    
                    if is_in_france(lat, lon):
                        france_stores.append(store)
                except (ValueError, TypeError):
                    continue
            
            logger.info(f"Total stores: {len(all_stores)}")
            logger.info(f"France stores: {len(france_stores)}")
            
            # Normalize France stores
            self.results = [normalize_store(s) for s in france_stores]
            
            # Count by city
            cities = {}
            for r in self.results:
                city = r.get("city", "Unknown")
                cities[city] = cities.get(city, 0) + 1
            
            logger.info(f"Unique cities: {len(cities)}")
        
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
        json_path = json_dir / f"maison_kayser_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"maison_kayser_{timestamp}.xlsx"
        
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
        output_dir / "maison_kayser.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        encoding="utf-8",
    )
    
    # Run scraper
    scraper = MaisonKayserScraper(output_dir=output_dir)
    
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} France bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
