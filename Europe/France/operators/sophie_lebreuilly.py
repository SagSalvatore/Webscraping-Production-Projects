"""
Sophie Lebreuilly Bakery Scraper - France
Extracts outlet data from iframe on adelyashop.com store locator.

Data structure:
- Store wrapper div with data-codegroup (store ID)
- h5.title contains store name
- p contains address with postal code (format: "Street POSTAL City FR")
- a[href^="tel:"] contains phone number
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

# Iframe URL containing the actual store data
IFRAME_URL = "https://www.adelyashop.com/Adelyaview/sophielebreuilly/storelocator/Boulangerie-Sophie.html?lang=fr"


# ============================================================================
# Data Extraction
# ============================================================================

def extract_stores(html: str) -> list[dict]:
    """
    Extract store data from HTML.
    
    Each store is in a <li> with structure:
    <div class="store-wrapper" data-codegroup="G89293533">
        <h5 class="title">Name</h5>
        <p>Address with postal code</p>
        <a href="tel:+33...">Phone</a>
    </div>
    
    Args:
        html: Raw HTML content
        
    Returns:
        List of store dictionaries
    """
    stores = []
    
    # Pattern to extract each store block
    store_pattern = re.compile(
        r'<div class="store-wrapper" data-codegroup="([^"]+)"[^>]*>([\s\S]*?)</div>\s*</div>\s*</li>',
        re.IGNORECASE
    )
    
    for match in store_pattern.finditer(html):
        store_code = match.group(1)
        content = match.group(2)
        
        store = {
            "store_code": store_code,
            "name": "",
            "address": "",
            "phone": "",
        }
        
        # Extract name from h5.title
        name_match = re.search(r'<h5[^>]*class="[^"]*title[^"]*"[^>]*>\s*([\s\S]*?)\s*</h5>', content)
        if name_match:
            name_text = name_match.group(1)
            # Remove badge span
            name_text = re.sub(r'<span[^>]*>.*?</span>', '', name_text)
            store["name"] = unescape(name_text.strip())
        
        # Extract address (first <p> after <!-- Address -->)
        addr_match = re.search(r'<!-- Address -->\s*<p>([^<]+)</p>', content)
        if addr_match:
            store["address"] = unescape(addr_match.group(1).strip())
        
        # Extract phone
        phone_match = re.search(r'<a href="tel:([^"]+)"', content)
        if phone_match:
            store["phone"] = phone_match.group(1).strip()
        
        stores.append(store)
    
    logger.info(f"Extracted {len(stores)} stores")
    return stores


def parse_address(address: str) -> dict:
    """
    Parse address string to extract street, postal code, and city.
    
    Format: "Street POSTAL City FR"
    Example: "1 boulevard Billiet 62630 Etaples sur mer FR"
    
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
    
    # Remove trailing "FR" or "France"
    address = re.sub(r'\s+FR\s*$', '', address, flags=re.IGNORECASE)
    address = re.sub(r'\s+France\s*$', '', address, flags=re.IGNORECASE)
    
    # Find postal code (5 digits)
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
        data["street_address"] = address
    
    return data


def normalize_store(store: dict) -> dict:
    """
    Normalize store data to standard format.
    
    Args:
        store: Raw store data
        
    Returns:
        Normalized bakery dictionary
    """
    # Parse address
    addr_parts = parse_address(store.get("address", ""))
    
    return {
        "store_code": store.get("store_code", ""),
        "name": store.get("name", ""),
        "city": addr_parts["city"],
        "postal_code": addr_parts["postal_code"],
        "street_address": addr_parts["street_address"],
        "address": store.get("address", ""),
        "country": "France",
        "latitude": None,
        "longitude": None,
        "url": f"https://sophie-lebreuilly.com/",
        "phone": store.get("phone", ""),
        "email": "",
        "opening_hours": {},
        "operator": "Sophie Lebreuilly",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class SophieLebreuillyScraper:
    """Scraper for Sophie Lebreuilly bakeries - from iframe."""
    
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
        logger.info("Starting Sophie Lebreuilly Scraper")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            logger.info(f"Fetching iframe: {IFRAME_URL[:60]}...")
            
            response = await session.get(IFRAME_URL, timeout=60)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch: HTTP {response.status_code}")
                return []
            
            logger.info(f"Page size: {len(response.text)} bytes")
            
            # Extract stores
            stores = extract_stores(response.text)
            
            if not stores:
                logger.error("No stores found!")
                return []
            
            # Normalize all stores
            self.results = [normalize_store(s) for s in stores]
            
            # Count statistics
            with_city = sum(1 for r in self.results if r.get("city"))
            with_phone = sum(1 for r in self.results if r.get("phone"))
            
            logger.info(f"Normalized: {len(self.results)} bakeries")
            logger.info(f"With city: {with_city}, With phone: {with_phone}")
        
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
        json_path = json_dir / f"sophie_lebreuilly_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"sophie_lebreuilly_{timestamp}.xlsx"
        
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
        output_dir / "sophie_lebreuilly.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        encoding="utf-8",
    )
    
    # Run scraper
    scraper = SophieLebreuillyScraper(output_dir=output_dir)
    
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
