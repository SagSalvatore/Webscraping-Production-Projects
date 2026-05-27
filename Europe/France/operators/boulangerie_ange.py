"""
Boulangerie Ange Scraper - France
Extracts outlet data from JavaScript markers variable on the store locator page.
Optionally enriches with detail page data (address, phone).

Features:
- Single page extraction for basic data (name, coordinates, URLs)
- Optional detail page scraping for full data (address, phone, hours)
- Async HTTP requests with curl_cffi
- Rate limiting with jitter, backoff
- Auto-save and auto-resume
"""
import asyncio
import json
import re
import random
from pathlib import Path
from datetime import datetime
from typing import Optional
import pandas as pd

from curl_cffi.requests import AsyncSession
from loguru import logger


# ============================================================================
# Configuration
# ============================================================================

MAX_CONCURRENT = 10  # Max concurrent requests for detail pages
BASE_DELAY = 0.3  # Base delay between requests (seconds)
JITTER_RANGE = (0.1, 0.3)  # Random jitter range (seconds)
MAX_RETRIES = 3  # Max retries per URL
BACKOFF_BASE = 2  # Exponential backoff base
CHECKPOINT_INTERVAL = 50  # Save checkpoint every N URLs

# Store locator URL
STORE_LOCATOR_URL = "https://www.boulangerie-ange.fr/en/your-nearest-ange/"

# Pattern to extract markers array
MARKERS_PATTERN = re.compile(
    r'var\s+markers\s*=\s*(\[\s*\{.*?\}\s*\])\s*;?',
    re.DOTALL
)


# ============================================================================
# Data Extraction
# ============================================================================

def extract_markers_from_html(html: str) -> list[dict]:
    """
    Extract bakery markers from JavaScript variable.
    
    Args:
        html: Raw HTML content
        
    Returns:
        List of marker dictionaries
    """
    match = MARKERS_PATTERN.search(html)
    
    if not match:
        logger.error("Could not find markers variable in HTML!")
        return []
    
    json_str = match.group(1)
    
    try:
        markers = json.loads(json_str)
        logger.success(f"Extracted {len(markers)} bakery markers")
        return markers
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return []


def normalize_marker(marker: dict) -> dict:
    """
    Normalize marker data to standard format.
    
    Args:
        marker: Raw marker from JavaScript
        
    Returns:
        Normalized bakery dictionary
    """
    # Extract city from name (e.g., "Boulangerie Ange Riom" -> "Riom")
    name = marker.get("text", "")
    city = name.replace("Boulangerie Ange", "").strip()
    
    return {
        "store_code": marker.get("id", ""),
        "name": name,
        "city": city.title(),  # Capitalize properly
        "country": "France",
        "latitude": float(marker.get("lat", 0)) if marker.get("lat") else None,
        "longitude": float(marker.get("lng", 0)) if marker.get("lng") else None,
        "url": marker.get("permalink", ""),
        "operator": "Boulangerie Ange",
        # These will be filled from detail page if available
        "address": "",
        "street_address": "",
        "postal_code": "",
        "phone": "",
        "email": "",
        "opening_hours": {},
    }


def extract_detail_data(html: str) -> dict:
    """
    Extract additional data from bakery detail page.
    
    HTML structure:
    - Phone: tel: link
    - Address: <div class="stores-card__txt"> with street on first line, <b>postal city</b>
    - Hours: <table class='horaires'> with rows for each day
    
    Args:
        html: Raw HTML content of detail page
        
    Returns:
        Dictionary with address, phone, hours, city
    """
    data = {
        "phone": "",
        "address": "",
        "street_address": "",
        "postal_code": "",
        "city": "",
        "opening_hours": {},
    }
    
    # Extract phone from tel: link
    phone_match = re.search(r'tel:([^"\'<\s]+)', html)
    if phone_match:
        data["phone"] = phone_match.group(1).strip()
    
    # Extract address from stores-card__txt div
    # Pattern: <div class="stores-card__txt"> street <br> <b>postal&nbsp;CITY</b> </div>
    # Note: &nbsp; may appear between postal and city
    addr_pattern = re.compile(
        r'<div\s+class="stores-card__txt">\s*(.*?)\s*<br>\s*<b>(\d{5})(?:&nbsp;|\s)*([^<]+)</b>',
        re.IGNORECASE | re.DOTALL
    )
    match = addr_pattern.search(html)
    
    if match:
        street = match.group(1).strip()
        postal = match.group(2).strip()
        city = match.group(3).strip()
        
        # Clean up HTML entities
        street = re.sub(r'&[a-z]+;', ' ', street)
        street = re.sub(r'\s+', ' ', street).strip()
        city = re.sub(r'&[a-z]+;', ' ', city)
        city = re.sub(r'\s+', ' ', city).strip()
        
        data["street_address"] = street
        data["postal_code"] = postal
        data["city"] = city
        data["address"] = f"{street}, {postal} {city}"
    else:
        # Try alternative pattern - some pages may have slightly different structure
        alt_pattern = re.compile(
            r'stores-card__txt[^>]*>\s*([^<]+)<br>\s*<b>([^<]+)</b>',
            re.IGNORECASE
        )
        alt_match = alt_pattern.search(html)
        if alt_match:
            street = alt_match.group(1).strip()
            postal_city = alt_match.group(2).strip().replace('&nbsp;', ' ')
            
            # Try to split postal code and city
            pc_match = re.search(r'(\d{5})\s*(.+)', postal_city)
            if pc_match:
                data["postal_code"] = pc_match.group(1)
                data["city"] = pc_match.group(2).strip()
            
            data["street_address"] = street
            data["address"] = f"{street}, {postal_city}"
    
    # Extract opening hours from horaires table
    # Pattern: <th class='d'>Lundi</th><td class='s'>06h30</td><td class='e'>20h00</td>
    hours_pattern = re.compile(
        r"<th\s+class='d'>([^<]+)</th><td\s+class='s'>([^<]+)</td><td\s+class='e'>([^<]+)</td>",
        re.IGNORECASE
    )
    
    hours_matches = hours_pattern.findall(html)
    if hours_matches:
        for day, opens, closes in hours_matches:
            # Map French day names to English
            day_map = {
                'Lundi': 'Monday',
                'Mardi': 'Tuesday',
                'Mercredi': 'Wednesday',
                'Jeudi': 'Thursday',
                'Vendredi': 'Friday',
                'Samedi': 'Saturday',
                'Dimanche': 'Sunday',
            }
            day_en = day_map.get(day.strip(), day.strip())
            data["opening_hours"][day_en] = f"{opens.strip()}-{closes.strip()}"
    
    return data


# ============================================================================
# Scraper Class
# ============================================================================

class BoulangerieAngeScraper:
    """Scraper for Boulangerie Ange bakeries."""
    
    def __init__(
        self,
        output_dir: Path,
        scrape_details: bool = True,
        max_concurrent: int = MAX_CONCURRENT,
    ):
        self.output_dir = output_dir
        self.scrape_details = scrape_details
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
        # Checkpointing and auto-save files
        self.checkpoint_file = output_dir / "ange_checkpoint.json"
        self.autosave_json = output_dir / "json" / "ange_autosave.json"
        self.autosave_excel = output_dir / "excel" / "ange_autosave.xlsx"
        
        # Progress tracking
        self.completed = 0
        self.success = 0
        self.failed = 0
        self.results = []
        self.failed_urls = []
        self.scraped_urls = set()
        
        # Load checkpoint if exists
        self._load_checkpoint()
    
    def _load_checkpoint(self):
        """Load checkpoint file if it exists for auto-resume."""
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                    checkpoint = json.load(f)
                
                self.results = checkpoint.get("results", [])
                self.failed_urls = checkpoint.get("failed_urls", [])
                self.success = len(self.results)
                self.failed = len(self.failed_urls)
                
                # Build set of already scraped URLs
                for r in self.results:
                    if r.get("url"):
                        self.scraped_urls.add(r["url"])
                
                self.scraped_urls.update(self.failed_urls)
                
                logger.info(f"✅ Loaded checkpoint: {len(self.results)} results, {len(self.failed_urls)} failed")
                
            except Exception as e:
                logger.warning(f"⚠️ Failed to load checkpoint: {e}")
                self.results = []
                self.failed_urls = []
                self.scraped_urls = set()
    
    async def fetch_store_locator(self, session: AsyncSession) -> list[dict]:
        """
        Fetch and parse the store locator page.
        
        Args:
            session: HTTP session
            
        Returns:
            List of normalized bakery data
        """
        logger.info(f"Fetching store locator: {STORE_LOCATOR_URL}")
        
        response = await session.get(STORE_LOCATOR_URL, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch store locator: HTTP {response.status_code}")
            return []
        
        markers = extract_markers_from_html(response.text)
        
        if not markers:
            return []
        
        # Normalize all markers
        bakeries = [normalize_marker(m) for m in markers]
        
        return bakeries
    
    async def fetch_detail_with_retry(
        self,
        session: AsyncSession,
        url: str,
    ) -> Optional[dict]:
        """
        Fetch bakery detail page with retry logic.
        
        Args:
            session: HTTP session
            url: Detail page URL
            
        Returns:
            Detail data dict or None
        """
        for attempt in range(MAX_RETRIES):
            async with self.semaphore:
                try:
                    jitter = random.uniform(*JITTER_RANGE)
                    await asyncio.sleep(BASE_DELAY + jitter)
                    
                    response = await session.get(url, timeout=30)
                    
                    if response.status_code == 200:
                        return extract_detail_data(response.text)
                    
                    elif response.status_code == 429:
                        wait_time = BACKOFF_BASE ** (attempt + 1) + random.uniform(1, 3)
                        logger.warning(f"Rate limited, waiting {wait_time:.1f}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    else:
                        logger.warning(f"HTTP {response.status_code} for {url}")
                        
                except Exception as e:
                    logger.error(f"Error fetching {url}: {e}")
                    
                    if attempt < MAX_RETRIES - 1:
                        wait_time = BACKOFF_BASE ** (attempt + 1)
                        await asyncio.sleep(wait_time)
        
        return None
    
    async def enrich_bakery(
        self,
        session: AsyncSession,
        bakery: dict,
        index: int,
        total: int,
    ) -> dict:
        """
        Enrich bakery data with detail page info.
        
        Args:
            session: HTTP session
            bakery: Basic bakery data
            index: Current index
            total: Total bakeries
            
        Returns:
            Enriched bakery dict
        """
        url = bakery.get("url", "")
        
        if not url or url in self.scraped_urls:
            return bakery
        
        detail = await self.fetch_detail_with_retry(session, url)
        
        self.completed += 1
        
        if detail:
            # Update with detail data, but only if we got non-empty values
            if detail.get("address"):
                bakery["address"] = detail["address"]
            if detail.get("street_address"):
                bakery["street_address"] = detail["street_address"]
            if detail.get("postal_code"):
                bakery["postal_code"] = detail["postal_code"]
            if detail.get("city"):
                bakery["city"] = detail["city"]  # Override city from detail (more accurate)
            if detail.get("phone"):
                bakery["phone"] = detail["phone"]
            if detail.get("opening_hours"):
                bakery["opening_hours"] = detail["opening_hours"]
            
            self.success += 1
            self.results.append(bakery)
            logger.debug(f"✅ Enriched: {bakery.get('name', 'Unknown')} - {bakery.get('address', '')}")
        else:
            self.failed += 1
            self.failed_urls.append(url)
            self.results.append(bakery)  # Still save basic data
            logger.error(f"❌ Failed to enrich: {url}")
            logger.bind(FAILED_URL=True).error(f"FAILED: {url}")
        
        # Log progress
        if self.completed % 25 == 0 or self.completed == total:
            logger.info(
                f"Progress: {self.completed}/{total} "
                f"({100*self.completed/total:.1f}%) - "
                f"Success: {self.success}, Failed: {self.failed}"
            )
        
        # Save checkpoint
        if self.completed % CHECKPOINT_INTERVAL == 0:
            await self.save_checkpoint()
        
        return bakery
    
    async def save_checkpoint(self):
        """Save current progress to checkpoint file and auto-save results."""
        checkpoint = {
            "timestamp": datetime.now().isoformat(),
            "completed": self.completed,
            "success": self.success,
            "failed": self.failed,
            "results": self.results,
            "failed_urls": self.failed_urls,
        }
        
        with open(self.checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(checkpoint, f, ensure_ascii=False, indent=2)
        
        # Auto-save results
        if self.results:
            self.autosave_json.parent.mkdir(parents=True, exist_ok=True)
            with open(self.autosave_json, "w", encoding="utf-8") as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
            
            try:
                self.autosave_excel.parent.mkdir(parents=True, exist_ok=True)
                flat_results = []
                for r in self.results:
                    flat = r.copy()
                    hours = flat.pop("opening_hours", {})
                    flat["opening_hours_json"] = json.dumps(hours, ensure_ascii=False)
                    flat_results.append(flat)
                
                df = pd.DataFrame(flat_results)
                df.to_excel(self.autosave_excel, index=False)
            except Exception as e:
                logger.warning(f"Auto-save Excel failed: {e}")
        
        logger.info(f"💾 Auto-saved: {len(self.results)} results")
    
    async def run(self) -> list[dict]:
        """
        Run the scraper.
        
        Returns:
            List of bakery data dictionaries
        """
        logger.info("=" * 60)
        logger.info("Starting Boulangerie Ange Scraper")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            # Step 1: Fetch store locator and get all bakeries
            bakeries = await self.fetch_store_locator(session)
            
            if not bakeries:
                logger.error("No bakeries found!")
                return []
            
            logger.info(f"Found {len(bakeries)} bakeries")
            
            # Step 2: Optionally enrich with detail page data
            if self.scrape_details:
                # Filter out already scraped
                to_scrape = [b for b in bakeries if b.get("url") not in self.scraped_urls]
                
                if len(self.scraped_urls) > 0:
                    logger.info(f"📂 Resuming: {len(self.scraped_urls)} already scraped")
                    logger.info(f"📋 Remaining: {len(to_scrape)} to scrape")
                
                logger.info(f"Enriching {len(to_scrape)} bakeries with detail data...")
                
                tasks = [
                    self.enrich_bakery(session, b, i, len(to_scrape))
                    for i, b in enumerate(to_scrape)
                ]
                
                await asyncio.gather(*tasks)
            else:
                # Just use basic data
                self.results = bakeries
                self.success = len(bakeries)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        
        logger.info(f"Scraping completed in {elapsed:.1f}s")
        logger.info(f"Total: {len(self.results)}, Success: {self.success}, Failed: {self.failed}")
        
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
        json_path = json_dir / f"boulangerie_ange_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"boulangerie_ange_{timestamp}.xlsx"
        
        flat_results = []
        for r in results:
            flat = r.copy()
            hours = flat.pop("opening_hours", {})
            flat["opening_hours_json"] = json.dumps(hours, ensure_ascii=False)
            flat_results.append(flat)
        
        df = pd.DataFrame(flat_results)
        df.to_excel(excel_path, index=False)
        logger.success(f"Excel saved: {excel_path}")
        
        # Save failed URLs if any
        if self.failed_urls:
            failed_path = self.output_dir / f"ange_failed_{timestamp}.txt"
            with open(failed_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.failed_urls))
            logger.warning(f"Failed URLs saved: {failed_path}")
        
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
        output_dir / "ange.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        encoding="utf-8",
    )
    
    logger.add(
        output_dir / "ange_failed_urls.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {message}",
        level="ERROR",
        filter=lambda record: "FAILED_URL" in record["extra"],
        rotation="5 MB",
        encoding="utf-8",
    )
    
    # Run scraper - set scrape_details=True to get address/phone from detail pages
    scraper = BoulangerieAngeScraper(
        output_dir=output_dir,
        scrape_details=True,  # Set to False for quick scrape without detail pages
        max_concurrent=MAX_CONCURRENT,
    )
    
    results = await scraper.run()
    
    if results:
        scraper.save_results(results)
        logger.success(f"✅ Scraped {len(results)} bakeries successfully!")
    else:
        logger.error("❌ No data scraped")


if __name__ == "__main__":
    asyncio.run(main())
