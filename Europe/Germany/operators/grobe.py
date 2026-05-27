"""
Bäckerei Grobe Scraper - Germany
Extracts outlet data from embedded JavaScript array at https://www.baeckerei-grobe.de/filialen

The page contains a JavaScript array: var locations = [{lat: ..., lng: ..., city: ..., adresse: ...}, ...]
"""
import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Optional

from loguru import logger

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from utils.base_scraper import BaseScraper
from utils.http_client import AsyncHttpClient
from utils.exporters import export_outlets
from utils.logger import setup_logger


# ============================================================================
# Module-level functions for multiprocessing (must be picklable)
# ============================================================================

def parse_single_location(location_dict: dict) -> dict:
    """
    Normalize a single location from the JavaScript array.
    
    Args:
        location_dict: Raw location dictionary from JS array
        
    Returns:
        Normalized outlet dict
    """
    # Parse address - format: "Street, Postal City, Country"
    adresse = location_dict.get("adresse", "")
    
    # Extract components from address
    street_address = ""
    postal_code = ""
    city_from_addr = ""
    country = "Germany"
    
    if adresse:
        # Split by comma and clean up
        parts = [p.strip() for p in adresse.split(",")]
        
        if len(parts) >= 1:
            street_address = parts[0]
        
        if len(parts) >= 2:
            # Second part usually has "POSTAL CITY"
            postal_city = parts[1].strip()
            # Try to extract postal code (German postal codes are 5 digits)
            match = re.match(r'^(\d{5})\s+(.+)$', postal_city)
            if match:
                postal_code = match.group(1)
                city_from_addr = match.group(2)
            else:
                city_from_addr = postal_city
        
        if len(parts) >= 3:
            country = parts[2].strip()
            if country.lower() == "deutschland":
                country = "Germany"
    
    # The 'city' field in the data appears to be the outlet/branch name, not the actual city
    outlet_name = location_dict.get("city", "")
    
    return {
        "store_code": str(location_dict.get("markerid", "")),
        "name": f"Bäckerei Grobe - {outlet_name}" if outlet_name else "Bäckerei Grobe",
        "branch_name": outlet_name,
        "address": adresse.replace(", Deutschland", "").strip(", "),
        "street_address": street_address,
        "postal_code": postal_code,
        "city": city_from_addr,
        "region": "DE",
        "country": country,
        "phone": "",  # Not available in this data
        "latitude": location_dict.get("lat"),
        "longitude": location_dict.get("lng"),
        "opening_hours": {},  # Not available in the basic location data
        "url": f"https://www.baeckerei-grobe.de/filialen",
        "operator": "Bäckerei Grobe",
    }


def parse_js_array_to_dicts(js_array_str: str) -> list[dict]:
    """
    Parse a JavaScript array string with unquoted keys into Python dicts.
    
    Args:
        js_array_str: Raw JavaScript array string like [{lat: 51.5, lng: 7.5, ...}, ...]
        
    Returns:
        List of dictionaries
    """
    # JavaScript object keys don't need quotes, but Python JSON does
    # Convert: {lat: 51.5, lng: 7.5, city: "Name"}
    # To:      {"lat": 51.5, "lng": 7.5, "city": "Name"}
    
    # Add quotes around unquoted keys
    # Pattern: word characters followed by colon (not inside quotes)
    fixed = re.sub(r'(\s*)(\w+)\s*:', r'\1"\2":', js_array_str)
    
    # Remove trailing commas before ] or }
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
    
    try:
        return json.loads(fixed)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JS array: {e}")
        # Try alternative parsing
        return []


# ============================================================================
# Main Scraper Class
# ============================================================================

class GrobeScraper(BaseScraper):
    """Scraper for Bäckerei Grobe outlets in Germany."""
    
    OPERATOR_NAME = "Bäckerei Grobe"
    COUNTRY = "Germany"
    BASE_URL = "https://www.baeckerei-grobe.de"
    FILIALEN_URL = "https://www.baeckerei-grobe.de/filialen"
    
    # Regex to extract the locations array from JavaScript
    # Pattern: var locations = [...];
    LOCATIONS_PATTERN = re.compile(
        r'var\s+locations\s*=\s*\[([\s\S]*?)\];',
        re.MULTILINE
    )
    
    def __init__(
        self,
        output_dir: Optional[Path] = None,
        proxy: Optional[str] = None,
        max_workers: int = None,
    ):
        """Initialize the scraper."""
        super().__init__(output_dir, proxy)
        import multiprocessing
        self.max_workers = max_workers or max(2, multiprocessing.cpu_count() - 1)
    
    async def scrape(self) -> list[dict]:
        """
        Scrape all Bäckerei Grobe outlets from the filialen page.
        
        Returns:
            List of outlet dictionaries
        """
        logger.info(f"Fetching {self.FILIALEN_URL}")
        
        # Fetch the main page
        html_content = await self.fetch_page(self.FILIALEN_URL)
        
        # Extract locations from JavaScript
        outlets = await self._extract_outlets_parallel(html_content)
        
        logger.info(f"Extracted {len(outlets)} outlets from page source")
        return outlets
    
    async def _extract_outlets_parallel(self, html_content: str) -> list[dict]:
        """
        Extract outlet data from embedded JavaScript array using parallel processing.
        
        Args:
            html_content: Raw HTML content
            
        Returns:
            List of normalized outlet dictionaries
        """
        # Find the locations array
        match = self.LOCATIONS_PATTERN.search(html_content)
        
        if not match:
            logger.error("Could not find 'var locations = [...]' in page source")
            return []
        
        js_array_content = match.group(1)
        logger.debug(f"Found locations array ({len(js_array_content)} chars)")
        
        # Parse the JavaScript array
        raw_locations = parse_js_array_to_dicts(f"[{js_array_content}]")
        
        if not raw_locations:
            logger.error("Failed to parse locations array")
            return []
        
        logger.info(f"Found {len(raw_locations)} locations, processing with ThreadPoolExecutor")
        
        # Use ThreadPoolExecutor for parallel processing
        loop = asyncio.get_event_loop()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                loop.run_in_executor(executor, parse_single_location, loc)
                for loc in raw_locations
            ]
            
            results = await asyncio.gather(*futures)
        
        # Filter out None results
        outlets = [r for r in results if r is not None]
        
        logger.info(f"Successfully parsed {len(outlets)} outlets")
        return outlets


async def main():
    """Main entry point for Bäckerei Grobe scraper."""
    # Setup logging
    setup_logger(
        name="grobe",
        log_dir=Path(__file__).parent.parent / "output",
    )
    
    logger.info("=" * 60)
    logger.info("Starting Bäckerei Grobe Scraper")
    logger.info("=" * 60)
    
    # Run scraper
    scraper = GrobeScraper()
    json_path, excel_path = await scraper.run()
    
    if json_path:
        logger.success(f"✅ JSON output: {json_path}")
        logger.success(f"✅ Excel output: {excel_path}")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
