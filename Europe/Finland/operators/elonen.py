"""
Elonen Finland Store Scraper

Scrapes store locations from https://elonen.fi/toimipisteet
Two-step process:
1. Fetch main page and extract all outlet URLs
2. Visit each outlet page and extract details (name, address, city, etc.)
"""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
from curl_cffi.requests import AsyncSession
from loguru import logger
from lxml import html

# ============================================================================
# Configuration
# ============================================================================

BASE_URL = "https://elonen.fi"
START_URL = "https://elonen.fi/toimipisteet"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

# ============================================================================
# Scraper Class
# ============================================================================

class ElonenScraper:
    """Scraper for Elonen - Finland."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.results = []
    
    async def run(self) -> list[dict]:
        """Run the scraper."""
        logger.info("=" * 60)
        logger.info("Starting Elonen Scraper (Finland)")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            # 1. Fetch Main Page
            logger.info(f"Fetching Main Page: {START_URL}")
            response = await session.get(START_URL, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch main page: {response.status_code}")
                return []
            
            # Save debug HTML
            debug_path = self.output_dir / "elonen_main.html"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(response.text)
            logger.info(f"Saved debug HTML to {debug_path}")
            
            # 2. Extract Outlet URLs
            tree = html.fromstring(response.content)
            
            # Look for links under /toimipisteet/
            outlet_urls = set()
            links = tree.xpath("//a/@href")
            
            for link in links:
                full_url = urljoin(BASE_URL, link)
                # Filter for valid outlet URLs (under /toimipisteet/ but not the main page)
                if (full_url.startswith(f"{BASE_URL}/toimipisteet/") and 
                    full_url != START_URL and
                    full_url != f"{START_URL}/" and
                    len(full_url) > len(f"{BASE_URL}/toimipisteet/")):
                    outlet_urls.add(full_url)
            
            logger.info(f"Found {len(outlet_urls)} outlet URLs")
            
            # 3. Process Each Outlet
            for url in sorted(outlet_urls):
                logger.info(f"Processing: {url}")
                await self.process_outlet(session, url)
                await asyncio.sleep(0.5)  # Be nice to the server
                
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
            
            # Extract Store Name
            # Try H1 first
            h1 = tree.xpath("//h1//text()")
            name = "Elonen"
            if h1:
                name = " ".join(h1).strip()
            else:
                # Fallback from URL
                slug = url.rstrip('/').split('/')[-1]
                name = slug.replace('-', ' ').title()
            
            # Extract Address Details
            # Target: section.block-single-office-location //div[@class='column'][2]//p
            # Structure: <p><strong>Location Name</strong>Street<br />PostalCode City<br />Phone</p>
            
            location_name = ""
            street = ""
            postal_city = ""
            phone = ""
            
            # Find the Info column (second column in the section)
            info_columns = tree.xpath("//section[contains(@class, 'block-single-office-location')]//div[@class='column']")
            
            if len(info_columns) >= 2:
                info_col = info_columns[1]
                # Get paragraphs in this column
                paragraphs = info_col.xpath(".//p")
                
                for p in paragraphs:
                    # Check if this paragraph contains <strong> (location info)
                    strong = p.xpath(".//strong/text()")
                    if strong:
                        location_name = strong[0].strip()
                        # Get all text content
                        full_text = p.text_content()
                        # Remove the location name from the text
                        if location_name in full_text:
                            full_text = full_text.replace(location_name, "").strip()
                        
                        # Split by newlines or logical breaks
                        lines = [l.strip() for l in full_text.split('\n') if l.strip()]
                        
                        for line in lines:
                            # Check for Finnish postal code pattern (5 digits + city)
                            if re.match(r'^\d{5}\s+\w+', line):
                                postal_city = line
                            # Check for street (contains number, not postal code, not phone)
                            elif re.search(r'\d', line) and not re.match(r'^\d{5}', line) and not re.match(r'^0\d', line):
                                street = line
                            # Check for phone (starts with 0 or +)
                            elif re.match(r'^[0+]', line):
                                phone = line
                        
                        break  # Found the info paragraph
            
            # Parse postal code and city from postal_city
            postal_code = ""
            city = ""
            if postal_city:
                match = re.match(r'(\d{5})\s+(.+)', postal_city)
                if match:
                    postal_code = match.group(1)
                    city = match.group(2).strip()
            
            # Construct full address
            address_parts = [p for p in [street, postal_city] if p]
            full_address = ", ".join(address_parts) if address_parts else "N/A"
            
            # Create store entry
            store = {
                "name": name,
                "location_name": location_name,
                "street_address": street,
                "postal_code": postal_code,
                "city": city,
                "address": full_address,
                "country": "Finland",
                "latitude": 0.0,
                "longitude": 0.0,
                "url": url,
                "phone": "",
                "email": "",
                "operator": "Elonen"
            }
            
            self.results.append(store)
            logger.success(f"Added: {name} | {city}")

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
        json_path = json_dir / f"elonen_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"elonen_{timestamp}.xlsx"
        df = pd.DataFrame(self.results)
        df.to_excel(excel_path, index=False)
        logger.success(f"Excel saved: {excel_path}")
        
        # Summary
        print("\n" + "=" * 60)
        print("ELONEN SCRAPING RESULTS")
        print("=" * 60)
        print(f"Total stores scraped: {len(self.results)}")
        print(f"Stores with addresses: {sum(1 for s in self.results if s.get('address') != 'N/A')}")
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
    
    scraper = ElonenScraper(output_dir=output_dir)
    try:
        await scraper.run()
    except Exception as e:
        logger.error(f"Scraper run failed: {e}")
    finally:
        scraper.save_results()

if __name__ == "__main__":
    asyncio.run(main())
