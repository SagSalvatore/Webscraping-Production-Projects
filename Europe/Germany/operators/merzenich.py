"""
Bäckerei Merzenich Scraper - Germany
Extracts outlet data from embedded JavaScript at https://baeckerei-merzenich.de/filialfinder/

The page contains a JavaScript variable: var merzenichFinderSettings = {..., data: [...]}
with detailed outlet information including addresses, opening hours, and features.
"""
import asyncio
import json
import re
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
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
# Module-level functions for multiprocessing
# ============================================================================

def parse_merzenich_location(location: dict) -> dict:
    """
    Parse a single location from the Merzenich data array.
    
    Args:
        location: Raw location dictionary from merzenichFinderSettings.data
        
    Returns:
        Normalized outlet dictionary
    """
    # Extract address components
    address_data = location.get("address", {})
    street = address_data.get("street", "")
    city = address_data.get("city", "")
    postal_code = address_data.get("postalCode", "")
    country_code = address_data.get("country", "DE")
    
    # Build full address
    full_address = f"{street}, {postal_code} {city}".strip(", ")
    
    # Parse location coordinates
    coord = location.get("location", {})
    
    # Parse opening hours
    # Array indexed: 0=Sunday, 1=Monday, 2=Tuesday, ..., 6=Saturday
    raw_hours = location.get("openingHours", [])
    day_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    opening_hours = {}
    
    for i, hours in enumerate(raw_hours):
        if hours and isinstance(hours, dict):
            start = hours.get("start", "")
            end = hours.get("end", "")
            if start and end:
                opening_hours[day_names[i]] = f"{start}-{end}"
    
    # Extract features
    features = location.get("features", {})
    
    return {
        "store_code": location.get("id", ""),
        "name": location.get("name", "Bäckerei Merzenich"),
        "status": "active" if location.get("status") == 1 else "inactive",
        "address": full_address,
        "street_address": street,
        "postal_code": postal_code,
        "city": city,
        "region": "DE",
        "country": "Germany" if country_code == "DE" else country_code,
        "phone": location.get("phone", ""),
        "latitude": coord.get("lat"),
        "longitude": coord.get("lng"),
        "opening_hours": opening_hours,
        "url": "https://baeckerei-merzenich.de/filialfinder/",
        # Features
        "is_bakery": features.get("isBakery", False),
        "is_coffee": features.get("isCoffee", False),
        "is_coffeeshop": features.get("isCoffeeshop", False),
        "has_wifi": features.get("wifi", False),
        "serves_breakfast": features.get("servesBreakfast", False),
        "has_delivery": features.get("hasDelivery", False),
        "pay_debit_card": features.get("payDebitCard", False),
        "wheelchair_accessible_entrance": features.get("hasWheelchairAccessibleEntrance", False),
        "wheelchair_accessible_seating": features.get("hasWheelchairAccessibleSeating", False),
        "operator": "Bäckerei Merzenich",
    }


# ============================================================================
# Main Scraper Class
# ============================================================================

class MerzenichScraper(BaseScraper):
    """Scraper for Bäckerei Merzenich outlets in Germany."""
    
    OPERATOR_NAME = "Bäckerei Merzenich"
    COUNTRY = "Germany"
    BASE_URL = "https://baeckerei-merzenich.de"
    FILIALFINDER_URL = "https://baeckerei-merzenich.de/filialfinder/"
    
    # Regex to extract merzenichFinderSettings from page
    SETTINGS_PATTERN = re.compile(
        r'var\s+merzenichFinderSettings\s*=\s*(\{[\s\S]*?\});\s*(?:</script>|$)',
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
        self.max_workers = max_workers or max(2, multiprocessing.cpu_count() - 1)
    
    async def scrape(self) -> list[dict]:
        """
        Scrape all Bäckerei Merzenich outlets from the filialfinder page.
        
        Returns:
            List of outlet dictionaries
        """
        logger.info(f"Fetching {self.FILIALFINDER_URL}")
        
        # Fetch the page
        html_content = await self.fetch_page(self.FILIALFINDER_URL)
        
        # Extract outlets from JavaScript
        outlets = await self._extract_outlets_parallel(html_content)
        
        logger.info(f"Extracted {len(outlets)} outlets from page source")
        return outlets
    
    async def _extract_outlets_parallel(self, html_content: str) -> list[dict]:
        """
        Extract outlet data from embedded JavaScript using parallel processing.
        
        Args:
            html_content: Raw HTML content
            
        Returns:
            List of normalized outlet dictionaries
        """
        # Find the merzenichFinderSettings variable
        match = self.SETTINGS_PATTERN.search(html_content)
        
        if not match:
            logger.error("Could not find 'var merzenichFinderSettings = {...}' in page source")
            # Try alternative pattern
            alt_pattern = re.search(r'"data":\s*\[([\s\S]*?)\],"', html_content)
            if alt_pattern:
                logger.info("Found data array using alternative pattern")
                try:
                    data_str = '[' + alt_pattern.group(1) + ']'
                    raw_locations = json.loads(data_str)
                except json.JSONDecodeError:
                    return []
            else:
                return []
        else:
            js_object_str = match.group(1)
            
            # Parse the JavaScript object as JSON
            try:
                settings = json.loads(js_object_str)
                raw_locations = settings.get("data", [])
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse merzenichFinderSettings JSON: {e}")
                # Try to fix common issues
                # The regex might not capture the complete object, try to find just the data array
                data_pattern = re.search(r'"data"\s*:\s*\[', html_content)
                if data_pattern:
                    # Find the matching closing bracket
                    start_idx = data_pattern.end() - 1
                    count = 1
                    end_idx = start_idx + 1
                    while count > 0 and end_idx < len(html_content):
                        if html_content[end_idx] == '[':
                            count += 1
                        elif html_content[end_idx] == ']':
                            count -= 1
                        end_idx += 1
                    
                    data_str = html_content[start_idx:end_idx]
                    try:
                        raw_locations = json.loads(data_str)
                        logger.info(f"Successfully extracted data array: {len(raw_locations)} locations")
                    except json.JSONDecodeError:
                        return []
                else:
                    return []
        
        if not raw_locations:
            logger.error("No locations found in merzenichFinderSettings.data")
            return []
        
        logger.info(f"Found {len(raw_locations)} locations, processing with ThreadPoolExecutor")
        
        # Process locations in parallel
        loop = asyncio.get_event_loop()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                loop.run_in_executor(executor, parse_merzenich_location, loc)
                for loc in raw_locations
            ]
            
            outlets = await asyncio.gather(*futures)
        
        # Filter out None results and inactive locations
        outlets = [o for o in outlets if o is not None]
        
        logger.info(f"Successfully parsed {len(outlets)} outlets")
        return outlets


async def main():
    """Main entry point for Bäckerei Merzenich scraper."""
    # Setup logging
    setup_logger(
        name="merzenich",
        log_dir=Path(__file__).parent.parent / "output",
    )
    
    logger.info("=" * 60)
    logger.info("Starting Bäckerei Merzenich Scraper")
    logger.info("=" * 60)
    
    # Run scraper
    scraper = MerzenichScraper()
    json_path, excel_path = await scraper.run()
    
    if json_path:
        logger.success(f"✅ JSON output: {json_path}")
        logger.success(f"✅ Excel output: {excel_path}")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
