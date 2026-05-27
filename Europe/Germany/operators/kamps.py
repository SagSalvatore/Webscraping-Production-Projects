"""
Kamps Bäckerei Scraper - Germany
Extracts outlet data from embedded JSON in page source at https://kamps.de/standorte

Uses multiprocessing for parallel JSON parsing and asyncio for concurrent HTTP requests.
"""
import asyncio
import html
import json
import re
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Optional
import multiprocessing

from loguru import logger

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from utils.base_scraper import BaseScraper, run_scraper
from utils.http_client import AsyncHttpClient
from utils.exporters import export_outlets
from utils.logger import setup_logger


# ============================================================================
# Module-level functions for multiprocessing (must be picklable)
# ============================================================================

def parse_single_outlet_json(json_string: str, base_url: str) -> Optional[dict]:
    """
    Parse a single outlet JSON string (for multiprocessing).
    
    Args:
        json_string: Raw HTML-encoded JSON string
        base_url: Base URL for building full URLs
        
    Returns:
        Normalized outlet dict or None if invalid
    """
    try:
        # Decode HTML entities: &quot; -> "
        decoded = html.unescape(json_string)
        
        # Parse JSON
        data = json.loads(decoded)
        
        # Get the properties (main data is nested)
        props = data.get("properties", data)
        
        # Skip if hidden
        if props.get("is_hidden", False):
            return None
        
        # Skip duplicates (status = "Duplikat")
        if props.get("status", "").lower() == "duplikat":
            return None
        
        # Normalize the outlet data
        return normalize_kamps_outlet(props, base_url)
        
    except json.JSONDecodeError:
        return None
    except Exception:
        return None


def normalize_kamps_outlet(props: dict, base_url: str) -> dict:
    """
    Normalize Kamps outlet data to standard format.
    
    Args:
        props: Raw properties from embedded JSON
        base_url: Base URL for building full URLs
        
    Returns:
        Normalized outlet dictionary
    """
    # Build full address
    address_parts = []
    
    # Street address
    street = props.get("adresszeile1", "")
    if street:
        address_parts.append(street)
    
    # Additional address info (like "Rewe")
    addr2 = props.get("adresszeile2", "")
    
    # Postal code and city
    plz = props.get("postleitzahl", "")
    city = props.get("ort", "")
    if plz and city:
        address_parts.append(f"{plz} {city}")
    elif city:
        address_parts.append(city)
    
    full_address = ", ".join(address_parts)
    
    # Build opening hours
    opening_hours = {}
    day_mapping = {
        "mo": "Monday",
        "di": "Tuesday", 
        "mi": "Wednesday",
        "do": "Thursday",
        "fr": "Friday",
        "sa": "Saturday",
        "so": "Sunday",
    }
    
    for short, full in day_mapping.items():
        hours = props.get(f"oeffnungszeiten_{short}", "")
        if hours:
            opening_hours[full] = hours
    
    # Build website URL
    website_path = props.get("website", "")
    if website_path and not website_path.startswith("http"):
        website_url = f"{base_url}{website_path}"
    else:
        website_url = website_path
    
    # Safe float conversion
    def safe_float(value):
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    
    return {
        "store_code": props.get("geschaeftscode", ""),
        "name": props.get("unternehmen", "Kamps Bäckerei"),
        "address": full_address,
        "street_address": street,
        "address_line_2": addr2,
        "postal_code": plz,
        "city": city,
        "district": props.get("ortsteil", ""),
        "region": props.get("region", "DE"),
        "country": "Germany",
        "phone": props.get("telefonnummer", ""),
        "latitude": safe_float(props.get("latitude")),
        "longitude": safe_float(props.get("longitude")),
        "opening_hours": opening_hours,
        "url": website_url,
        "has_wifi": props.get("has_wlan", False),
        "has_delivery": props.get("has_lieferung", False),
        "has_cashless": props.get("has_bargeldlos", False),
        "profile_image": props.get("profilbild", ""),
        "title_image": props.get("titelbild", ""),
        "status": props.get("status", ""),
        "operator": "Kamps",
    }


# ============================================================================
# Main Scraper Class
# ============================================================================

class KampsScraper(BaseScraper):
    """Scraper for Kamps Bäckerei outlets in Germany."""
    
    OPERATOR_NAME = "Kamps"
    COUNTRY = "Germany"
    BASE_URL = "https://kamps.de"
    STANDORTE_URL = "https://kamps.de/standorte"
    
    # Regex to extract JSON from: var item = JSON.parse('{...}'.replace(...)
    # The HTML has: var item = JSON.parse('{...}'.replace(/\r\n|&quot;/g, ...));
    JSON_PATTERN = re.compile(
        r"var\s+item\s*=\s*JSON\.parse\s*\(\s*'(\{.+?\})'\s*\.replace",
        re.DOTALL
    )
    
    def __init__(
        self,
        output_dir: Optional[Path] = None,
        proxy: Optional[str] = None,
        max_workers: int = None,
    ):
        """
        Initialize the scraper.
        
        Args:
            output_dir: Output directory for results
            proxy: Proxy URL (optional)
            max_workers: Number of parallel workers for processing
        """
        super().__init__(output_dir, proxy)
        # Use CPU count - 1 for workers, minimum 2
        self.max_workers = max_workers or max(2, multiprocessing.cpu_count() - 1)
    
    async def scrape(self) -> list[dict]:
        """
        Scrape all Kamps outlets from the standorte page.
        
        Returns:
            List of outlet dictionaries
        """
        logger.info(f"Fetching {self.STANDORTE_URL}")
        
        # Fetch the main page
        html_content = await self.fetch_page(self.STANDORTE_URL)
        
        # Extract all embedded JSON using multiprocessing
        outlets = await self._extract_outlets_parallel(html_content)
        
        logger.info(f"Extracted {len(outlets)} outlets from page source")
        return outlets
    
    async def _extract_outlets_parallel(self, html_content: str) -> list[dict]:
        """
        Extract outlet data from embedded JSON using parallel processing.
        
        Args:
            html_content: Raw HTML content
            
        Returns:
            List of normalized outlet dictionaries
        """
        # Find all JSON.parse patterns
        matches = self.JSON_PATTERN.findall(html_content)
        logger.info(f"Found {len(matches)} JSON.parse patterns, processing with {self.max_workers} workers")
        
        if not matches:
            return []
        
        # Use ThreadPoolExecutor for I/O-bound-ish JSON parsing
        # ProcessPoolExecutor has higher overhead for small tasks
        loop = asyncio.get_event_loop()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Create partial function with base_url
            parse_func = partial(parse_single_outlet_json, base_url=self.BASE_URL)
            
            # Submit all parsing tasks
            futures = [
                loop.run_in_executor(executor, parse_func, match)
                for match in matches
            ]
            
            # Gather all results
            results = await asyncio.gather(*futures)
        
        # Filter out None results
        outlets = [r for r in results if r is not None]
        
        logger.info(f"Successfully parsed {len(outlets)} outlets (filtered {len(matches) - len(outlets)} duplicates/hidden)")
        return outlets


async def main():
    """Main entry point for Kamps scraper."""
    # Setup logging
    setup_logger(
        name="kamps",
        log_dir=Path(__file__).parent.parent / "output",
    )
    
    logger.info("=" * 60)
    logger.info("Starting Kamps Bäckerei Scraper")
    logger.info("=" * 60)
    
    # Run scraper
    scraper = KampsScraper()
    json_path, excel_path = await scraper.run()
    
    if json_path:
        logger.success(f"✅ JSON output: {json_path}")
        logger.success(f"✅ Excel output: {excel_path}")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
