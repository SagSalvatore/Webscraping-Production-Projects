"""
Gateau Bakery Scraper - Sweden
Extracts outlet data from Gateau.se hidden API.

API endpoint: https://www.gateau.se/api/cda/content
Called with contentUrl for each store page.
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

API_URL = "https://www.gateau.se/api/cda/content"
BASE_URL = "https://www.gateau.se"


# ============================================================================
# Data Extraction
# ============================================================================

def normalize_store(data: dict) -> dict:
    """
    Normalize store data from API response.
    
    API returns: name, streetAddress, postalCode, city, latitude, longitude, email, openingHours
    """
    try:
        lat = float(data.get("latitude", 0))
        lng = float(data.get("longitude", 0))
    except (ValueError, TypeError):
        lat = 0.0
        lng = 0.0
    
    # Extract phone from additionalContactInfomation (HTML)
    contact_info = data.get("additionalContactInfomation", "")
    phone = ""
    phone_match = re.search(r'Tel[.\s:]*([0-9\s-]+)', contact_info)
    if phone_match:
        phone = phone_match.group(1).strip()
    
    # Format address
    street = data.get("streetAddress", "")
    postal = data.get("postalCode", "")
    city = data.get("city", "")
    full_address = f"{street}, {postal} {city}".strip(", ")
    
    return {
        "name": f"Gateau {data.get('name', '')}".strip(),
        "city": city,
        "postal_code": postal,
        "street_address": street,
        "address": full_address,
        "country": "Sweden",
        "latitude": lat,
        "longitude": lng,
        "url": data.get("url", ""),
        "phone": phone,
        "email": data.get("email", ""),
        "opening_hours": data.get("openingHours", {}),
        "operator": "Gateau",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class GateauScraper:
    """Scraper for Gateau bakeries - Sweden."""
    
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
        logger.info("Starting Gateau Scraper (Sweden)")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            # Step 1: Get all store slugs from main butiker page
            logger.info("Fetching main stores page to get all URLs...")
            
            params = {
                "contentUrl": f"{BASE_URL}/butiker/",
                "currentPageUrl": "/butiker/"
            }
            r = await session.get(API_URL, params=params, timeout=60)
            
            if r.status_code != 200:
                logger.error(f"Failed to fetch: HTTP {r.status_code}")
                return []
            
            data_str = r.text
            store_slugs = re.findall(r'/butiker/([^/"\s<>]+)/', data_str)
            unique_slugs = sorted(set(store_slugs))
            
            logger.success(f"Found {len(unique_slugs)} stores")
            
            # Step 2: Fetch each store's data
            for i, slug in enumerate(unique_slugs, 1):
                store_url = f"{BASE_URL}/butiker/{slug}/"
                logger.info(f"[{i}/{len(unique_slugs)}] Fetching: {slug}")
                
                try:
                    params = {
                        "contentUrl": store_url,
                        "currentPageUrl": "/butiker/"
                    }
                    store_r = await session.get(API_URL, params=params, timeout=30)
                    
                    if store_r.status_code == 200:
                        store_data = store_r.json()
                        
                        # API returns list, first item is the store
                        if isinstance(store_data, list) and store_data:
                            store = store_data[0]
                            normalized = normalize_store(store)
                            self.results.append(normalized)
                            logger.success(f"  Found: {normalized['name']}")
                        elif isinstance(store_data, dict):
                            normalized = normalize_store(store_data)
                            self.results.append(normalized)
                            logger.success(f"  Found: {normalized['name']}")
                    
                    await asyncio.sleep(0.2)
                    
                except Exception as e:
                    logger.error(f"  Error: {e}")
                    continue
        
        # Statistics
        with_coords = sum(1 for r in self.results if r["latitude"] != 0)
        logger.info(f"\nTotal stores: {len(self.results)}")
        logger.info(f"With coordinates: {with_coords}")
        
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
        json_path = json_dir / f"gateau_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Flatten hours for Excel
        flat_results = []
        for r in results:
            flat = r.copy()
            hours = flat.pop("opening_hours", {})
            flat["opening_hours_json"] = json.dumps(hours, ensure_ascii=False)
            flat_results.append(flat)
        
        # Save Excel
        excel_path = excel_dir / f"gateau_{timestamp}.xlsx"
        df = pd.DataFrame(flat_results)
        df.to_excel(excel_path, index=False)
        logger.success(f"Excel saved: {excel_path}")
        
        return json_path, excel_path


async def main():
    """Main entry point."""
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
        output_dir / "gateau.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        encoding="utf-8",
    )
    
    # Run scraper
    scraper = GateauScraper(output_dir=output_dir)
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
