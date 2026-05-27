"""
Banette Bakery Scraper - France
Extracts ~1500 outlet data from JavaScript jsonLocations variable on store locator page.
Single page extraction - no need for detail page scraping.

Data available:
- id, name, lat, lng
- popup_html contains: Ville (city), Code postal, Adresse
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

STORE_LOCATOR_URL = "https://www.banette.fr/nos-boulangeries"


# ============================================================================
# Data Extraction
# ============================================================================

def extract_json_locations(html: str) -> list[dict]:
    """
    Extract bakery data from jsonLocations JavaScript variable.
    Uses bracket counting to handle the large JSON array.
    
    Args:
        html: Raw HTML content
        
    Returns:
        List of location dictionaries
    """
    # Find the start of items array
    items_idx = html.find('"items":[')
    
    if items_idx == -1:
        logger.error("Could not find items array in HTML!")
        return []
    
    # Find the end of the items array using bracket counting
    start = items_idx + len('"items":')
    bracket_count = 0
    end = start
    
    for i, char in enumerate(html[start:start+3000000]):  # Limit to 3MB for safety
        if char == '[':
            bracket_count += 1
        elif char == ']':
            bracket_count -= 1
            if bracket_count == 0:
                end = start + i + 1
                break
    
    items_str = html[start:end]
    logger.info(f"Found items array: {len(items_str)} characters")
    
    try:
        items = json.loads(items_str)
        logger.success(f"Extracted {len(items)} bakery locations")
        return items
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return []


def parse_popup_html(popup_html: str) -> dict:
    """
    Parse popup_html to extract city, postal code, and address.
    
    Example popup_html:
    <div class="amlocator-info-popup"> 
        <h3 class="amlocator-name"><div class="amlocator-title">BOULANGERIE BANETTE</div></h3> 
        Ville : AINAY LE CHATEAU <br> 
        Code postal : 03360 <br> 
        Adresse : PLACE DU FAUBOURG <br> 
    </div>
    
    Args:
        popup_html: HTML string from popup
        
    Returns:
        Dict with city, postal_code, address
    """
    data = {
        "city": "",
        "postal_code": "",
        "street_address": "",
    }
    
    # Unescape HTML entities
    html = unescape(popup_html)
    
    # Extract Ville (city)
    city_match = re.search(r'Ville\s*:\s*([^<]+)', html, re.IGNORECASE)
    if city_match:
        data["city"] = city_match.group(1).strip()
    
    # Extract Code postal
    postal_match = re.search(r'Code\s*postal\s*:\s*(\d+)', html, re.IGNORECASE)
    if postal_match:
        data["postal_code"] = postal_match.group(1).strip()
    
    # Extract Adresse
    addr_match = re.search(r'Adresse\s*:\s*([^<]+)', html, re.IGNORECASE)
    if addr_match:
        data["street_address"] = addr_match.group(1).strip()
    
    return data


def normalize_location(loc: dict) -> dict:
    """
    Normalize a single location to standard format.
    
    Args:
        loc: Raw location from jsonLocations
        
    Returns:
        Normalized bakery dictionary
    """
    # Parse popup HTML for address details
    popup_data = parse_popup_html(loc.get("popup_html", ""))
    
    # Build full address
    address_parts = []
    if popup_data["street_address"]:
        address_parts.append(popup_data["street_address"])
    if popup_data["postal_code"]:
        address_parts.append(popup_data["postal_code"])
    if popup_data["city"]:
        address_parts.append(popup_data["city"])
    
    full_address = ", ".join(address_parts) if address_parts else ""
    
    return {
        "store_code": str(loc.get("id", "")),
        "name": loc.get("name", ""),
        "city": popup_data["city"],
        "postal_code": popup_data["postal_code"],
        "street_address": popup_data["street_address"],
        "address": full_address,
        "country": "France",
        "latitude": float(loc.get("lat", 0)) if loc.get("lat") else None,
        "longitude": float(loc.get("lng", 0)) if loc.get("lng") else None,
        "url": "",  # No individual URLs available
        "phone": "",
        "email": "",
        "opening_hours": {},
        "operator": "Banette",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class BanetteScraper:
    """Scraper for Banette bakeries - single page extraction."""
    
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
        logger.info("Starting Banette Scraper")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            logger.info(f"Fetching store locator: {STORE_LOCATOR_URL}")
            
            response = await session.get(STORE_LOCATOR_URL, timeout=60)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch: HTTP {response.status_code}")
                return []
            
            logger.info(f"Page size: {len(response.text)} bytes")
            
            # Extract locations from JSON
            locations = extract_json_locations(response.text)
            
            if not locations:
                logger.error("No locations found!")
                return []
            
            logger.info(f"Found {len(locations)} locations, normalizing...")
            
            # Normalize all locations
            self.results = [normalize_location(loc) for loc in locations]
            
            # Count successful extractions
            with_city = sum(1 for r in self.results if r.get("city"))
            with_address = sum(1 for r in self.results if r.get("street_address"))
            
            logger.info(f"Normalized: {len(self.results)} bakeries")
            logger.info(f"With city: {with_city}, With address: {with_address}")
        
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
        json_path = json_dir / f"banette_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"banette_{timestamp}.xlsx"
        
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
        output_dir / "banette.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        encoding="utf-8",
    )
    
    # Run scraper
    scraper = BanetteScraper(output_dir=output_dir)
    
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
