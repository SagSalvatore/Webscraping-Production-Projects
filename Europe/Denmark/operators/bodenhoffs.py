"""
Bodenhoffs Scraper - Denmark
Fetch and parse store data from https://www.bodenhoffs.dk/butikkerne/
"""
import asyncio
import json
import re
from pathlib import Path
from datetime import datetime
import pandas as pd
from urllib.parse import urljoin

from curl_cffi.requests import AsyncSession
from loguru import logger
from lxml import html

# ============================================================================
# Configuration
# ============================================================================

BASE_URL = "https://www.bodenhoffs.dk"
START_URL = "https://www.bodenhoffs.dk/butikkerne/"

# ============================================================================
# Scraper Class
# ============================================================================

class BodenhoffsScraper:
    """Scraper for Bodenhoffs - Denmark."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.results = []
    
    async def run(self) -> list[dict]:
        """Run the scraper."""
        logger.info("=" * 60)
        logger.info("Starting Bodenhoffs Scraper (Denmark)")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            # 1. Fetch Main Page
            logger.info(f"Fetching Main Page: {START_URL}")
            response = await session.get(START_URL, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch main page: {response.status_code}")
                return []
            
            # 2. Extract Outlet URLs
            tree = html.fromstring(response.content)
            # Look for links that look like store pages. 
            # Usually they are under a specific container or just distinct links.
            # Based on user input, example: https://www.bodenhoffs.dk/butikkerne/bellahojvej/
            
            # General approach: Find all links under /butikkerne/ that are strictly sub-paths
            links = tree.xpath("//a/@href")
            outlet_urls = set()
            
            for link in links:
                full_url = urljoin(BASE_URL, link)
                # Filter for valid store URLs:
                # - Must start with base url + /butikkerne/
                # - Must not be exactly the start url
                # - Should not be a mailto or other type
                if (full_url.startswith(START_URL) and 
                    full_url != START_URL and 
                    len(full_url) > len(START_URL)):
                    outlet_urls.add(full_url)
            
            logger.info(f"Found {len(outlet_urls)} potential outlet URLs")
            
            # 3. Process Each Outlet
            for url in outlet_urls:
                logger.info(f"Processing: {url}")
                await self.process_outlet(session, url)
                await asyncio.sleep(1) # Be graceful
                
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"Scraping completed in {elapsed:.1f}s")
        
        return self.results

    async def process_outlet(self, session: AsyncSession, url: str):
        """Fetch and parse a single outlet page."""
        try:
            response = await session.get(url, timeout=30)
            if response.status_code != 200:
                logger.error(f"Failed to fetch {url}: {response.status_code}")
                return

            tree = html.fromstring(response.content)
            
            # Extract Store Name (usually from the URL or H1)
            # URL structure: .../butikkerne/store-name/
            slug = url.rstrip('/').split('/')[-1]
            name = slug.replace('-', ' ').title() # Fallback name
            
            # Try to get H1
            h1 = tree.xpath("//h1/text()")
            if h1:
                name = h1[0].strip()
                
            # Extract Address
            # User hint: //font[contains(text(),'Bellahøjvej 106, 2720 Vanløse')]
            # We will look for this pattern or similar text nodes
            address = "N/A"
            
            # Attempt 1: Look for the specific font tag pattern mentioned by user
            # It seems they might use <font> tags for address?
            font_texts = tree.xpath("//font/text()")
            address_candidates = [t for t in font_texts if any(char.isdigit() for char in t) and "," in t]
            
            if address_candidates:
                # Heuristic: address usually has a comma or zip code
                address = address_candidates[0].strip()
            else:
                 # Attempt 2: General search for address-like patterns in common containers
                 # often in p tags or footer or specific divs
                 # Let's try to match a Danish zip code pattern (4 digits)
                 texts = tree.xpath("//body//text()")
                 for t in texts:
                     t_clean = t.strip()
                     # Look for "Street, 0000 City" roughly
                     if re.search(r'\d{4}\s+[A-Za-zæøåÆØÅ]+', t_clean) and len(t_clean) < 100:
                         address = t_clean
                         break

            # Normalize data
            store_data = {
                "name": f"Bodenhoffs {name}",
                "address": address,
                "url": url,
                "operator": "Bodenhoffs",
                "country": "Denmark"
            }
            
            # Geographic and Detail Extraction (Simple parsing)
            self.normalize_and_add(store_data)

        except Exception as e:
            logger.error(f"Error processing {url}: {e}")

    def normalize_and_add(self, raw_data: dict):
        """Normalize address and add to results."""
        address = raw_data["address"]
        postal_code = ""
        city = ""
        street = address
        
        # simple Danish address parsing: "Street 123, 1234 City" or "Street 123 1234 City"
        # Find the 4 digit zip
        zip_match = re.search(r'(\d{4})\s+([A-Za-zæøåÆØÅ\s]+)', address)
        if zip_match:
            postal_code = zip_match.group(1)
            city = zip_match.group(2).strip()
            # Everything before the zip is likely the street
            street_part = address.split(postal_code)[0].strip().rstrip(',').strip()
            if street_part:
                street = street_part

        store = {
            "name": raw_data["name"],
            "street_address": street,
            "city": city,
            "postal_code": postal_code,
            "address": address,
            "country": "Denmark",
            "latitude": 0.0,
            "longitude": 0.0,
            "phone": "",
            "email": "",
            "url": raw_data["url"],
            "control_report_url": "", # Can add if found
            "operator": "Bodenhoffs"
        }
        self.results.append(store)
        logger.success(f"Added: {store['name']} | {city}")

    def save_results(self):
        """Save results to JSON and Excel files."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create output directories
        json_dir = self.output_dir / "json"
        excel_dir = self.output_dir / "excel"
        json_dir.mkdir(parents=True, exist_ok=True)
        excel_dir.mkdir(parents=True, exist_ok=True)
        
        # Save JSON
        json_path = json_dir / f"bodenhoffs_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"bodenhoffs_{timestamp}.xlsx"
        df = pd.DataFrame(self.results)
        df.to_excel(excel_path, index=False)
        logger.success(f"Excel saved: {excel_path}")

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
    
    scraper = BodenhoffsScraper(output_dir=output_dir)
    await scraper.run()
    scraper.save_results()

if __name__ == "__main__":
    asyncio.run(main())
