"""
Wayne's Coffee Scraper - Sweden
Reads URLs from Excel file and extracts JSON-LD structured data from each page.

Data is in schema.org format: application/ld+json with type CafeOrCoffeeShop
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

# Excel file with URLs
URLS_FILE = Path(__file__).parent.parent / "output" / "excel" / "Waynes.xlsx"


# ============================================================================
# Data Extraction
# ============================================================================

def extract_json_ld(html: str) -> dict:
    """
    Extract JSON-LD data from HTML.
    
    Looks for: <script type="application/ld+json">...</script>
    
    Args:
        html: Raw HTML content
        
    Returns:
        Parsed JSON-LD data or empty dict
    """
    pattern = r'<script\s+type="application/ld\+json"[^>]*>([\s\S]*?)</script>'
    matches = re.findall(pattern, html, re.IGNORECASE)
    
    for match in matches:
        try:
            data = json.loads(match.strip())
            # Look for CafeOrCoffeeShop type
            if isinstance(data, dict) and data.get("@type") in ["CafeOrCoffeeShop", "Restaurant", "LocalBusiness"]:
                return data
        except json.JSONDecodeError:
            continue
    
    return {}


def normalize_store(data: dict, url: str) -> dict:
    """
    Normalize JSON-LD store data to standard format.
    
    Args:
        data: JSON-LD data
        url: Source URL
        
    Returns:
        Normalized bakery dictionary
    """
    address = data.get("address", {})
    geo = data.get("geo", {})
    
    # Parse coordinates
    try:
        lat = float(geo.get("latitude", 0))
        lng = float(geo.get("longitude", 0))
    except (ValueError, TypeError):
        lat = 0.0
        lng = 0.0
    
    # Parse opening hours
    hours_spec = data.get("openingHoursSpecification", [])
    opening_hours = {}
    for spec in hours_spec:
        day = spec.get("dayOfWeek", "")
        opens = spec.get("opens", "")
        closes = spec.get("closes", "")
        if day and opens and closes:
            opening_hours[day] = f"{opens}-{closes}"
    
    # Get postal code (may have format "NNN NN")
    postal_code = address.get("postalCode", "")
    if isinstance(postal_code, str):
        postal_code = postal_code.replace(" ", "")
    
    return {
        "name": data.get("name", ""),
        "city": address.get("addressLocality", ""),
        "postal_code": postal_code,
        "street_address": address.get("streetAddress", ""),
        "address": f"{address.get('streetAddress', '')}, {address.get('postalCode', '')} {address.get('addressLocality', '')}".strip(", "),
        "country": "Sweden",
        "latitude": lat,
        "longitude": lng,
        "url": data.get("url", url),
        "phone": data.get("telephone", ""),
        "email": "",
        "opening_hours": opening_hours,
        "operator": "Wayne's Coffee",
    }


# ============================================================================
# Scraper Class
# ============================================================================

class WaynesCoffeeScraper:
    """Scraper for Wayne's Coffee - Sweden."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.results = []
    
    async def run(self) -> list[dict]:
        """
        Run the scraper.
        
        Returns:
            List of cafe data dictionaries
        """
        logger.info("=" * 60)
        logger.info("Starting Wayne's Coffee Scraper (Sweden)")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        # Read URLs from Excel
        if not URLS_FILE.exists():
            logger.error(f"URLs file not found: {URLS_FILE}")
            return []
        
        df = pd.read_excel(URLS_FILE)
        logger.info(f"Loaded {len(df)} URLs from {URLS_FILE.name}")
        
        # Find URL column
        url_col = None
        for col in df.columns:
            if 'url' in col.lower() or 'link' in col.lower():
                url_col = col
                break
        if url_col is None:
            url_col = df.columns[0]  # Use first column
        
        urls = df[url_col].dropna().tolist()
        logger.info(f"Found {len(urls)} URLs to scrape")
        
        # Scrape each URL
        async with AsyncSession(impersonate="chrome120") as session:
            for i, url in enumerate(urls, 1):
                url = str(url).strip()
                if not url.startswith("http"):
                    continue
                
                logger.info(f"[{i}/{len(urls)}] Fetching: {url}")
                
                try:
                    response = await session.get(url, timeout=30)
                    
                    if response.status_code != 200:
                        logger.warning(f"  HTTP {response.status_code}")
                        continue
                    
                    # Extract JSON-LD
                    data = extract_json_ld(response.text)
                    
                    if data:
                        store = normalize_store(data, url)
                        self.results.append(store)
                        logger.success(f"  Found: {store['name']}")
                    else:
                        logger.warning(f"  No JSON-LD data found")
                    
                    # Small delay to be polite
                    await asyncio.sleep(0.3)
                    
                except Exception as e:
                    logger.error(f"  Error: {e}")
                    continue
        
        logger.success(f"\nTotal stores scraped: {len(self.results)}")
        
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
        json_path = json_dir / f"waynes_coffee_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Flatten opening hours for Excel
        flat_results = []
        for r in results:
            flat = r.copy()
            hours = flat.pop("opening_hours", {})
            flat["opening_hours_json"] = json.dumps(hours, ensure_ascii=False)
            flat_results.append(flat)
        
        # Save Excel
        excel_path = excel_dir / f"waynes_coffee_{timestamp}.xlsx"
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
        output_dir / "waynes_coffee.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        encoding="utf-8",
    )
    
    # Run scraper
    scraper = WaynesCoffeeScraper(output_dir=output_dir)
    
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} cafes successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
