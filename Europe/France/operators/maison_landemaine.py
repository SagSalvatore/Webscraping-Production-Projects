"""
Maison Landemaine Bakery Scraper - France
Extracts outlet data from Super Store Finder XML endpoint.

Data format (XML):
<store>
    <item>
        <location>Name</location>
        <address>Street PostalCode City</address>
        <latitude>...</latitude>
        <longitude>...</longitude>
        <telephone>...</telephone>
        <country>FR</country>
        <storeId>...</storeId>
        <description>Opening hours</description>
    </item>
</store>
"""
import asyncio
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from html import unescape
import pandas as pd

from curl_cffi.requests import AsyncSession
from loguru import logger


# ============================================================================
# Configuration
# ============================================================================

# Super Store Finder XML endpoint
XML_URL = "https://maisonlandemaine.com/wp-content/plugins/superstorefinder-wp/ssf-wp-xml.php"


# ============================================================================
# Data Extraction
# ============================================================================

def parse_xml_stores(xml_content: str) -> list[dict]:
    """
    Parse store data from Super Store Finder XML.
    
    Args:
        xml_content: Raw XML content
        
    Returns:
        List of store dictionaries
    """
    stores = []
    
    try:
        root = ET.fromstring(xml_content)
        
        # Find all <item> elements inside <store>
        store_elem = root.find('store')
        if store_elem is None:
            logger.error("No <store> element found!")
            return []
        
        for item in store_elem.findall('item'):
            store = {
                "store_code": item.findtext("storeId", "").strip(),
                "name": item.findtext("location", "").strip(),
                "address": unescape(item.findtext("address", "").strip().replace("&#44;", ",")),
                "latitude": item.findtext("latitude", "").strip(),
                "longitude": item.findtext("longitude", "").strip(),
                "phone": item.findtext("telephone", "").strip(),
                "country": item.findtext("country", "").strip(),
                "description": unescape(item.findtext("description", "").strip()),
                "image": item.findtext("storeimage", "").strip(),
            }
            stores.append(store)
        
        logger.success(f"Parsed {len(stores)} stores from XML")
    except ET.ParseError as e:
        logger.error(f"XML parse error: {e}")
    
    return stores


def parse_address(address: str) -> dict:
    """
    Parse address string to extract street, postal code, and city.
    
    French address format: "Street PostalCode City"
    Example: "28 boulevard Beaumarchais 75011 Paris"
    
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
    
    # Clean up address
    address = address.strip()
    
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
        # No postal code, use full address
        data["street_address"] = address
    
    return data


def normalize_store(store: dict) -> dict:
    """
    Normalize store data to standard format.
    
    Args:
        store: Raw store data from XML
        
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
        "store_code": store.get("store_code", ""),
        "name": f"Maison Landemaine {store.get('name', '')}".strip(),
        "city": addr_parts["city"],
        "postal_code": addr_parts["postal_code"],
        "street_address": addr_parts["street_address"],
        "address": store.get("address", ""),
        "country": "France" if store.get("country") == "FR" else store.get("country", ""),
        "latitude": lat,
        "longitude": lon,
        "url": "https://maisonlandemaine.com/boulangeries/",
        "phone": store.get("phone", ""),
        "email": "",
        "opening_hours": store.get("description", ""),
        "operator": "Maison Landemaine",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class MaisonLandemaineScraper:
    """Scraper for Maison Landemaine bakeries - France only."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.results = []
    
    async def run(self) -> list[dict]:
        """
        Run the scraper.
        
        Returns:
            List of bakery data dictionaries (France only)
        """
        logger.info("=" * 60)
        logger.info("Starting Maison Landemaine Scraper")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            logger.info(f"Fetching XML: {XML_URL[:60]}...")
            
            response = await session.get(XML_URL, timeout=60)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch: HTTP {response.status_code}")
                return []
            
            logger.info(f"XML size: {len(response.text)} bytes")
            
            # Parse XML
            stores = parse_xml_stores(response.text)
            
            if not stores:
                logger.error("No stores found!")
                return []
            
            # Count by country
            countries = {}
            for s in stores:
                c = s.get("country", "Unknown")
                countries[c] = countries.get(c, 0) + 1
            
            logger.info(f"Total stores: {len(stores)}")
            logger.info(f"By country: {countries}")
            
            # Filter for France only
            france_stores = [s for s in stores if s.get("country") == "FR" or not s.get("country")]
            logger.info(f"France stores: {len(france_stores)}")
            
            # Normalize stores
            self.results = [normalize_store(s) for s in france_stores]
        
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
        json_path = json_dir / f"maison_landemaine_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"maison_landemaine_{timestamp}.xlsx"
        
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
        output_dir / "maison_landemaine.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        encoding="utf-8",
    )
    
    # Run scraper
    scraper = MaisonLandemaineScraper(output_dir=output_dir)
    
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
