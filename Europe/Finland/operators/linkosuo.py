"""
Linkosuo Finland Store Scraper

Scrapes store locations from provided URLs.
Extracts address info and deduplicates by address.
"""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from curl_cffi.requests import AsyncSession
from loguru import logger
from lxml import html

# ============================================================================
# Configuration
# ============================================================================

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Pre-fetched outlet URLs
OUTLET_URLS = [
    "https://linkosuo.fi/en/place/snygg-restaurant-cafe/",
    "https://linkosuo.fi/en/place/restaurant-rouhea/",
    "https://linkosuo.fi/en/place/ravintola-alvar/",
    "https://linkosuo.fi/en/place/orvokki/",
    "https://linkosuo.fi/en/place/min-asemankeskus-meeting-event-venues/",
    "https://linkosuo.fi/en/place/min-asemakeskus/",
    "https://linkosuo.fi/en/place/meeting-venues-cafe-linkosuo-esplanadi/",
    "https://linkosuo.fi/en/place/meeting-event-venues-in-hervanta/",
    "https://linkosuo.fi/en/place/mannakorven-kahvila/",
    "https://linkosuo.fi/en/place/kalevanpaasi/",
    "https://linkosuo.fi/en/place/nasin-sauna-english/",
    "https://linkosuo.fi/en/place/hertta/",
    "https://linkosuo.fi/en/place/fastelle-by-linkosuo/",
    "https://linkosuo.fi/en/place/cafe-linkosuo-ratina/",
    "https://linkosuo.fi/en/place/linkosuo-cafe-esplanadi/",
]

# ============================================================================
# Scraper Class
# ============================================================================

class LinkosuoScraper:
    """Scraper for Linkosuo - Finland."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.results = []
        self.seen_addresses = set()  # For deduplication
    
    async def run(self) -> list[dict]:
        """Run the scraper."""
        logger.info("=" * 60)
        logger.info("Starting Linkosuo Scraper (Finland)")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            logger.info(f"Processing {len(OUTLET_URLS)} outlet URLs")
            
            for url in OUTLET_URLS:
                logger.info(f"Processing: {url}")
                await self.process_outlet(session, url)
                await asyncio.sleep(0.5)  # Be nice to the server
                
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"Scraping completed in {elapsed:.1f}s")
        logger.info(f"Total unique stores: {len(self.results)}")
        
        return self.results

    async def process_outlet(self, session: AsyncSession, url: str):
        """Fetch and parse a single outlet page."""
        try:
            response = await session.get(url, timeout=30)
            if response.status_code != 200:
                logger.error(f"Failed to fetch {url}: {response.status_code}")
                return

            tree = html.fromstring(response.content)
            
            # Extract Store Name from H1
            h1 = tree.xpath("//h1//text()")
            name = "Linkosuo"
            if h1:
                name = " ".join([t.strip() for t in h1 if t.strip()]).strip()
            else:
                # Fallback from URL
                slug = url.rstrip('/').split('/')[-1]
                name = slug.replace('-', ' ').title()
            
            # Extract Address
            # User XPath example: //p[normalize-space()='Työpajankatu 300580 Helsinki']
            # The address is in a <p> tag, format: "Street PostalCode City"
            
            address = ""
            street = ""
            postal_code = ""
            city = ""
            
            # Look for paragraphs containing Finnish postal codes (5 digits)
            paragraphs = tree.xpath("//p")
            
            for p in paragraphs:
                text = p.text_content().strip()
                # Check if text contains a 5-digit postal code
                match = re.search(r'(.+?)\s*(\d{5})\s+(.+)', text)
                if match:
                    street = match.group(1).strip()
                    postal_code = match.group(2)
                    city_raw = match.group(3).strip()
                    
                    # Clean up city - remove common suffixes
                    city = city_raw
                    noise_patterns = [
                        r'View on map.*$',
                        r'See the location.*$',
                        r'Katso sijainti.*$',
                        r'Directions.*$',
                        r'Saapumisohjeet.*$',
                        r'päivitetään.*$',
                        r'Phone:.*$',
                    ]
                    for pattern in noise_patterns:
                        city = re.sub(pattern, '', city, flags=re.IGNORECASE).strip()
                    
                    # Clean up street - remove "Address:" prefix
                    street = re.sub(r'^Address:\s*', '', street, flags=re.IGNORECASE).strip()
                    # Remove trailing restaurant names from street if present
                    street = re.sub(r'^Restaurant\s+\w+', '', street, flags=re.IGNORECASE).strip()
                    
                    address = f"{street}, {postal_code} {city}"
                    break
            
            # Skip if no address found
            if not address:
                logger.warning(f"No address found for {name}")
                return
            
            # Deduplication by address
            address_key = address.lower().strip()
            if address_key in self.seen_addresses:
                logger.warning(f"Duplicate address skipped: {name} ({address})")
                return
            
            self.seen_addresses.add(address_key)
            
            # Create store entry
            store = {
                "name": name,
                "street_address": street,
                "postal_code": postal_code,
                "city": city,
                "address": address,
                "country": "Finland",
                "latitude": 0.0,
                "longitude": 0.0,
                "url": url,
                "phone": "",
                "email": "",
                "operator": "Linkosuo"
            }
            
            self.results.append(store)
            logger.success(f"Added: {name} | {address}")

        except Exception as e:
            logger.error(f"Error processing {url}: {e}")

    def save_results(self):
        """Save results to JSON and Excel files."""
        if not self.results:
            logger.warning("No results to save.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create output directories
        json_dir = self.output_dir / "json"
        excel_dir = self.output_dir / "excel"
        json_dir.mkdir(parents=True, exist_ok=True)
        excel_dir.mkdir(parents=True, exist_ok=True)
        
        # Save JSON
        json_path = json_dir / f"linkosuo_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"linkosuo_{timestamp}.xlsx"
        df = pd.DataFrame(self.results)
        df.to_excel(excel_path, index=False)
        logger.success(f"Excel saved: {excel_path}")
        
        # Summary
        print("\n" + "=" * 60)
        print("LINKOSUO SCRAPING RESULTS")
        print("=" * 60)
        print(f"Total unique stores: {len(self.results)}")
        print(f"Duplicates skipped: {len(OUTLET_URLS) - len(self.results)}")
        print(f"Output: {excel_path}")
        print("=" * 60)

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
    
    scraper = LinkosuoScraper(output_dir=output_dir)
    try:
        await scraper.run()
    except Exception as e:
        logger.error(f"Scraper run failed: {e}")
    finally:
        scraper.save_results()

if __name__ == "__main__":
    asyncio.run(main())
