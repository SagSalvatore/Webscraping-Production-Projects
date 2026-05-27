"""
Bäckerei Brinker Scraper - Germany
Extracts outlet data from Elfsight Store Locator API at https://brinker.de/standorte/

The page uses an Elfsight Store Locator widget with widget ID: c9fe3066-6081-49a0-b005-8c1c40725bb3
We directly call the Elfsight API to get all location data.
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

def parse_elfsight_location(location: dict) -> dict:
    """
    Parse a single location from the Elfsight API response.
    
    Args:
        location: Raw location dictionary from Elfsight API
        
    Returns:
        Normalized outlet dictionary
    """
    place = location.get("place", {})
    coordinates = place.get("coordinates", {})
    
    # Parse address
    address = location.get("address", "") or place.get("address", "")
    
    # Extract components from address
    # Format: "Street, Postal City, Country"
    street_address = ""
    postal_code = ""
    city = ""
    country = "Germany"
    
    if address:
        parts = [p.strip() for p in address.split(",")]
        
        if len(parts) >= 1:
            street_address = parts[0]
        
        if len(parts) >= 2:
            postal_city = parts[1].strip()
            match = re.match(r'^(\d{5})\s+(.+)$', postal_city)
            if match:
                postal_code = match.group(1)
                city = match.group(2)
            else:
                city = postal_city
        
        if len(parts) >= 3:
            country = parts[2].strip()
            if country.lower() == "germany":
                country = "Germany"
    
    # Parse opening hours from the day* fields
    opening_hours = {}
    day_mapping = {
        "Monday": "dayMondayHours",
        "Tuesday": "dayTuesdayHours",
        "Wednesday": "dayWednesdayHours",
        "Thursday": "dayThursdayHours",
        "Friday": "dayFridayHours",
        "Saturday": "daySaturdayHours",
        "Sunday": "daySundayHours",
    }
    
    for day_name, hours_key in day_mapping.items():
        is_open_key = hours_key.replace("Hours", "Open")
        if location.get(is_open_key, False):
            hours = location.get(hours_key, [])
            if hours and len(hours) > 0:
                time_range = hours[0].get("timeRange", [])
                if len(time_range) == 2:
                    opening_hours[day_name] = f"{time_range[0]}-{time_range[1]}"
    
    name = location.get("name", "Bäckerei Brinker")
    
    return {
        "store_code": location.get("id", ""),
        "name": f"Bäckerei Brinker - {city}" if city else name,
        "branch_name": name,
        "address": address.replace(", Germany", "").strip(", "),
        "street_address": street_address,
        "postal_code": postal_code,
        "city": city,
        "region": "DE",
        "country": country,
        "phone": location.get("phone", ""),
        "latitude": coordinates.get("lat"),
        "longitude": coordinates.get("lng"),
        "opening_hours": opening_hours,
        "url": f"https://brinker.de/standorte/",
        "email": location.get("email", ""),
        "website": location.get("website", ""),
        "operator": "Bäckerei Brinker",
    }


# ============================================================================
# Main Scraper Class
# ============================================================================

class BrinkerScraper(BaseScraper):
    """Scraper for Bäckerei Brinker outlets in Germany using Elfsight API."""
    
    OPERATOR_NAME = "Bäckerei Brinker"
    COUNTRY = "Germany"
    BASE_URL = "https://brinker.de"
    STANDORTE_URL = "https://brinker.de/standorte/"
    
    # Elfsight widget configuration
    WIDGET_ID = "c9fe3066-6081-49a0-b005-8c1c40725bb3"
    ELFSIGHT_API_URL = f"https://core.service.elfsight.com/p/boot/?page=https%3A%2F%2Fbrinker.de%2Fstandorte%2F&w={WIDGET_ID}"
    
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
        Scrape all Bäckerei Brinker outlets from Elfsight API.
        
        Returns:
            List of outlet dictionaries
        """
        logger.info(f"Fetching Elfsight API: {self.ELFSIGHT_API_URL[:80]}...")
        
        async with AsyncHttpClient(proxy=self.proxy) as client:
            response = await client.get(
                self.ELFSIGHT_API_URL,
                headers={
                    "Accept": "application/json",
                    "Referer": self.STANDORTE_URL,
                    "Origin": self.BASE_URL,
                }
            )
            
            if response.status_code != 200:
                logger.error(f"API returned status {response.status_code}")
                return []
            
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}")
                return []
        
        # Extract locations from the response
        # Structure: data.widgets[WIDGET_ID].data.settings.locations
        outlets = await self._extract_outlets_parallel(data)
        
        logger.info(f"Extracted {len(outlets)} outlets")
        return outlets
    
    async def _extract_outlets_parallel(self, data: dict) -> list[dict]:
        """
        Extract outlet data from Elfsight API response using parallel processing.
        
        Args:
            data: Raw API response dictionary
            
        Returns:
            List of normalized outlet dictionaries
        """
        # Navigate to the locations array
        try:
            widget_data = data.get("data", {}).get("widgets", {}).get(self.WIDGET_ID, {})
            settings = widget_data.get("data", {}).get("settings", {})
            locations = settings.get("locations", [])
        except Exception as e:
            logger.error(f"Failed to extract locations from response: {e}")
            return []
        
        if not locations:
            logger.error("No locations found in API response")
            return []
        
        logger.info(f"Found {len(locations)} locations in API response")
        
        # Process locations in parallel
        loop = asyncio.get_event_loop()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                loop.run_in_executor(executor, parse_elfsight_location, loc)
                for loc in locations
            ]
            
            outlets = await asyncio.gather(*futures)
        
        # Filter out None results
        outlets = [o for o in outlets if o is not None]
        
        return outlets


async def main():
    """Main entry point for Bäckerei Brinker scraper."""
    # Setup logging
    setup_logger(
        name="brinker",
        log_dir=Path(__file__).parent.parent / "output",
    )
    
    logger.info("=" * 60)
    logger.info("Starting Bäckerei Brinker Scraper (Elfsight API)")
    logger.info("=" * 60)
    
    # Run scraper
    scraper = BrinkerScraper()
    json_path, excel_path = await scraper.run()
    
    if json_path:
        logger.success(f"✅ JSON output: {json_path}")
        logger.success(f"✅ Excel output: {excel_path}")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
