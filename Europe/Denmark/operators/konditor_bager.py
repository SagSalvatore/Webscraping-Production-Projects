"""
KonditorBager Scraper - Denmark
Fetch and parse store data from https://www.konditor-bager.dk/19-find-bager
Using Playwright due to dynamic content loading.
"""
import asyncio
import json
import re
from pathlib import Path
from datetime import datetime
import pandas as pd
from urllib.parse import urljoin

from playwright.async_api import async_playwright
from loguru import logger
from lxml import html

# ============================================================================
# Configuration
# ============================================================================

BASE_URL = "https://www.konditor-bager.dk"
START_URL = "https://www.konditor-bager.dk/19-find-bager"

# ============================================================================
# Scraper Class
# ============================================================================

class KonditorBagerScraper:
    """Scraper for KonditorBager - Denmark."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.results = []
    
    async def run(self) -> list[dict]:
        """Run the scraper."""
        logger.info("=" * 60)
        logger.info("Starting KonditorBager Scraper (Denmark) with Playwright")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with async_playwright() as p:
            # Launch browser (headless)
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            try:
                # 1. Fetch Main Page
                logger.info(f"Navigating to: {START_URL}")
                await page.goto(START_URL, timeout=60000)
                
                # Wait for the store locator list
                # User locator: //div[@id='storeLocator__storeListRow']
                # We wait for it to be visible
                logger.info("Waiting for store list...")
                try:
                    await page.wait_for_selector("#storeLocator__storeListRow", timeout=15000)
                except Exception:
                    logger.warning("Timeout waiting for #storeLocator__storeListRow. Getting content anyway.")

                # Get the HTML content after JS execution
                content = await page.content()
                tree = html.fromstring(content)
                
                # 2. Extract Outlet URLs
                store_list = tree.xpath("//div[@id='storeLocator__storeListRow']")
                outlet_urls = set()
                
                if store_list:
                    # Find all links inside the store list
                    links = store_list[0].xpath(".//a/@href")
                    for link in links:
                        full_url = urljoin(BASE_URL, link)
                        if (full_url.startswith(BASE_URL) and 
                            "/19-find-bager" not in full_url and 
                            "#" not in full_url and 
                            "maps.google" not in full_url):
                            
                            # Filter out product pages to avoid duplicates
                            lower_url = full_url.lower()
                            if not any(x in lower_url for x in ["rundstykker", "boller", "kagemand"]):
                                outlet_urls.add(full_url)
                
                # Filter out suspicious short links
                final_urls = sorted([u for u in outlet_urls if len(u) > len(BASE_URL) + 4])
                
                logger.info(f"Found {len(final_urls)} store URLs")
                
                # Close main page to save resources
                await page.close()
                
                # 3. Process Each Outlet
                # We can now use a simpler method (curl_cffi) or stick with playwright if needed.
                # Since we suspect the DETAIL pages might also be dynamic or share the same template,
                # let's try curl_cffi for speed first (hybrid approach), but fall back to playwright if needed. 
                # Actually, given the main page was dynamic, subpages might be too. 
                # Let's stick with Playwright context but maybe reuse a page?
                # For safety and robustness let's just use the same browser instance.
                
                page = await browser.new_page()
                
                for url in final_urls:
                    logger.info(f"Processing: {url}")
                    await self.process_outlet_playwright(page, url)
                    # Be nice to the server
                    await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Global error: {e}")
            finally:
                await browser.close()
                
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"Scraping completed in {elapsed:.1f}s")
        
        return self.results

    async def process_outlet_playwright(self, page, url: str):
        """Fetch and parse a single outlet page using Playwright."""
        try:
            try:
                # Add retry logic for navigation
                for attempt in range(3):
                    try:
                        await page.goto(url, timeout=30000)
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                        break
                    except Exception as nav_err:
                        if attempt == 2: raise nav_err
                        logger.warning(f"Retry {attempt+1}/3 for {url}: {nav_err}")
                        await asyncio.sleep(2)
                
                content = await page.content()
                tree = html.fromstring(content)
                
                # Name
                h1 = tree.xpath("//h1/text()")
                name = "KonditorBager"
                if h1:
                    name = f"KonditorBager {h1[0].strip()}"
                else:
                    slug = url.rstrip('/').split('/')[-1]
                    name = f"KonditorBager {slug.replace('-', ' ').title()}"
                    
                # Address
                address = "N/A"
                font_nodes = tree.xpath("//font/text()")
                
                for t in font_nodes:
                    # Filter out known noise
                    if "EuroSkills" in t or "Bager fra Ørum" in t:
                        continue

                    if re.search(r'\d{4}', t):
                        address = t.strip()
                        break
                
                if address == "N/A":
                    texts = tree.xpath("//body//text()")
                    for t in texts:
                         t_cl = t.strip()
                         if re.search(r'\b\d{4}\s+[A-Za-zæøåÆØÅ]+\b', t_cl) and len(t_cl) < 100:
                             if "@" not in t_cl and "CVR" not in t_cl:
                                 address = t_cl
                                 break
                
                self.normalize_and_add({
                    "name": name,
                    "address": address,
                    "url": url
                })

            except Exception as e:
                logger.error(f"Error processing {url}: {e}")
                # Don't re-raise, just log and skip this store so others can proceed

        except Exception as e:
             logger.error(f"Critical error in process_outlet_playwright: {e}")

    def normalize_and_add(self, raw_data: dict):
        """Normalize address and add to results."""
        address = raw_data["address"]
        postal_code = ""
        city = ""
        street = address
        
        # Parse: "Street 123, 1234 City"
        zip_match = re.search(r'(\d{4})\s+([A-Za-zæøåÆØÅ\s]+)', address)
        if zip_match:
            postal_code = zip_match.group(1)
            city = zip_match.group(2).strip()
            parts = address.split(postal_code)
            if parts:
                street = parts[0].strip().rstrip(',').strip()

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
            "control_report_url": "",
            "operator": "KonditorBager"
        }
        
        # Deduplication
        # Check if we already have this store by address or name
        for existing in self.results:
            # If address is N/A, fallback to name check.
            # If address matches and is not N/A, it's a duplicate.
            if store["address"] != "N/A" and store["address"] == existing["address"]:
                logger.warning(f"Duplicate/Sub-page detected (Address match): {store['name']} vs {existing['name']}")
                return
            if store["name"] == existing["name"]:
                logger.warning(f"Duplicate detected (Name match): {store['name']}")
                return

        self.results.append(store)
        logger.success(f"Added: {store['name']} | {city}")

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
        json_path = json_dir / f"konditor_bager_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"konditor_bager_{timestamp}.xlsx"
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
    
    scraper = KonditorBagerScraper(output_dir=output_dir)
    try:
        await scraper.run()
    except Exception as e:
        logger.error(f"Scraper run failed: {e}")
    finally:
        logger.info("Saving what we have...")
        scraper.save_results()

if __name__ == "__main__":
    asyncio.run(main())
