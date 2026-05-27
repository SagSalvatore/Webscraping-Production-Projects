"""
Marie Blachère Bakery Scraper - France
Extracts outlet data from LD+JSON embedded in 800+ bakery URLs.

Features:
- Async HTTP requests with curl_cffi
- Rate limiting with jitter
- Exponential backoff on failures
- Progress tracking and checkpointing
- Concurrent processing with semaphore
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

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from utils.logger import setup_logger


# ============================================================================
# Configuration
# ============================================================================

MAX_CONCURRENT = 10  # Max concurrent requests
BASE_DELAY = 0.5  # Base delay between requests (seconds)
JITTER_RANGE = (0.1, 0.5)  # Random jitter range (seconds)
MAX_RETRIES = 3  # Max retries per URL
BACKOFF_BASE = 2  # Exponential backoff base
CHECKPOINT_INTERVAL = 50  # Save checkpoint every N URLs


# ============================================================================
# LD+JSON Extraction
# ============================================================================

# Pattern to extract LD+JSON from script tags
LD_JSON_PATTERN = re.compile(
    r'<script\s+type=["\']application/ld\+json["\']>\s*(\[[\s\S]*?\]|\{[\s\S]*?\})\s*</script>',
    re.IGNORECASE
)


def parse_ld_json_bakery(html: str, source_url: str) -> Optional[dict]:
    """
    Extract bakery data from LD+JSON in HTML content.
    
    Args:
        html: Raw HTML content
        source_url: Source URL for logging
        
    Returns:
        Normalized bakery dictionary or None if not found
    """
    matches = LD_JSON_PATTERN.findall(html)
    
    if not matches:
        logger.warning(f"No LD+JSON found in {source_url}")
        return None
    
    for match in matches:
        try:
            data = json.loads(match)
            
            # Handle both array and object formats
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") == "Bakery":
                        return normalize_bakery_data(item, source_url)
            elif isinstance(data, dict):
                if data.get("@type") == "Bakery":
                    return normalize_bakery_data(data, source_url)
                    
        except json.JSONDecodeError as e:
            logger.debug(f"JSON parse error: {e}")
            continue
    
    logger.warning(f"No Bakery type found in LD+JSON for {source_url}")
    return None


def normalize_bakery_data(data: dict, source_url: str) -> dict:
    """
    Normalize bakery data from LD+JSON format.
    
    Args:
        data: Raw LD+JSON bakery object
        source_url: Source URL
        
    Returns:
        Normalized bakery dictionary
    """
    address = data.get("address", {})
    geo = data.get("geo", {})
    hours = data.get("openingHoursSpecification", [])
    
    # Parse opening hours
    opening_hours = {}
    for h in hours:
        day = h.get("dayOfWeek", "").replace("http://schema.org/", "").replace("https://schema.org/", "")
        opens = h.get("opens", "")
        closes = h.get("closes", "")
        if day and opens and closes:
            opening_hours[day] = f"{opens}-{closes}"
    
    # Extract store code from @id
    store_id = data.get("@id", "")
    store_code = store_id.split("#")[-1] if "#" in store_id else ""
    
    return {
        "store_code": store_code,
        "name": data.get("name", ""),
        "address": f"{address.get('streetAddress', '')}, {address.get('postalCode', '')} {address.get('addressLocality', '')}".strip(", "),
        "street_address": address.get("streetAddress", ""),
        "postal_code": address.get("postalCode", ""),
        "city": address.get("addressLocality", ""),
        "country": "France",
        "region": address.get("addressCountry", "FR"),
        "phone": data.get("telephone", ""),
        "email": data.get("email", ""),
        "latitude": geo.get("latitude"),
        "longitude": geo.get("longitude"),
        "url": data.get("url", source_url),
        "opening_hours": opening_hours,
        "logo": data.get("logo", ""),
        "image": data.get("image", ""),
        "operator": "Marie Blachère",
    }


# ============================================================================
# Async Scraper
# ============================================================================

class MarieBlachereScraper:
    """Async scraper for Marie Blachère bakeries with rate limiting."""
    
    def __init__(
        self,
        urls: list[str],
        output_dir: Path,
        max_concurrent: int = MAX_CONCURRENT,
    ):
        self.all_urls = urls
        self.output_dir = output_dir
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
        # Checkpointing and auto-save files
        self.checkpoint_file = output_dir / "marie_blachere_checkpoint.json"
        self.autosave_json = output_dir / "json" / "marie_blachere_autosave.json"
        self.autosave_excel = output_dir / "excel" / "marie_blachere_autosave.xlsx"
        
        # Progress tracking
        self.completed = 0
        self.success = 0
        self.failed = 0
        self.results = []
        self.failed_urls = []
        self.scraped_urls = set()  # Track already scraped URLs
        
        # Load checkpoint if exists (auto-resume)
        self._load_checkpoint()
        
        # Filter out already scraped URLs
        self.urls = [url for url in self.all_urls if url not in self.scraped_urls]
        
        if len(self.scraped_urls) > 0:
            logger.info(f"📂 Resuming from checkpoint: {len(self.scraped_urls)} URLs already scraped")
            logger.info(f"📋 Remaining URLs to scrape: {len(self.urls)}")
    
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
                
                # Build set of already scraped URLs from results
                for r in self.results:
                    if r.get("url"):
                        self.scraped_urls.add(r["url"])
                
                # Also consider failed URLs as "processed"
                self.scraped_urls.update(self.failed_urls)
                
                logger.info(f"✅ Loaded checkpoint: {len(self.results)} results, {len(self.failed_urls)} failed")
                
            except Exception as e:
                logger.warning(f"⚠️ Failed to load checkpoint: {e}")
                # Reset state
                self.results = []
                self.failed_urls = []
                self.scraped_urls = set()
    
    async def fetch_with_retry(
        self,
        session: AsyncSession,
        url: str,
    ) -> Optional[dict]:
        """
        Fetch a single URL with exponential backoff and retry.
        
        Args:
            session: Async HTTP session
            url: URL to fetch
            
        Returns:
            Bakery data dict or None on failure
        """
        for attempt in range(MAX_RETRIES):
            async with self.semaphore:
                try:
                    # Add jitter to avoid thundering herd
                    jitter = random.uniform(*JITTER_RANGE)
                    await asyncio.sleep(BASE_DELAY + jitter)
                    
                    response = await session.get(
                        url,
                        timeout=30,
                        headers={
                            "Accept": "text/html,application/xhtml+xml",
                            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
                        }
                    )
                    
                    if response.status_code == 200:
                        result = parse_ld_json_bakery(response.text, url)
                        if result:
                            return result
                        else:
                            logger.warning(f"No data extracted from {url}")
                            return None
                    
                    elif response.status_code == 429:
                        # Rate limited - wait with exponential backoff
                        wait_time = BACKOFF_BASE ** (attempt + 1) + random.uniform(1, 3)
                        logger.warning(f"Rate limited, waiting {wait_time:.1f}s before retry...")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    else:
                        logger.warning(f"HTTP {response.status_code} for {url}")
                        
                except Exception as e:
                    logger.error(f"Error fetching {url}: {e}")
                    
                    # Exponential backoff on error
                    if attempt < MAX_RETRIES - 1:
                        wait_time = BACKOFF_BASE ** (attempt + 1)
                        await asyncio.sleep(wait_time)
        
        return None
    
    async def process_url(
        self,
        session: AsyncSession,
        url: str,
        index: int,
    ):
        """
        Process a single URL and track progress.
        
        Args:
            session: Async HTTP session
            url: URL to process
            index: URL index for progress tracking
        """
        result = await self.fetch_with_retry(session, url)
        
        self.completed += 1
        
        if result:
            self.success += 1
            self.results.append(result)
            logger.debug(f"✅ Scraped: {result.get('name', 'Unknown')} - {url}")
        else:
            self.failed += 1
            self.failed_urls.append(url)
            # Log to both main log and failed URLs log
            logger.error(f"❌ FAILED to scrape: {url}")
            logger.bind(FAILED_URL=True).error(f"FAILED: {url}")
        
        # Log progress
        if self.completed % 25 == 0 or self.completed == len(self.urls):
            logger.info(
                f"Progress: {self.completed}/{len(self.urls)} "
                f"({100*self.completed/len(self.urls):.1f}%) - "
                f"Success: {self.success}, Failed: {self.failed}"
            )
        
        # Save checkpoint
        if self.completed % CHECKPOINT_INTERVAL == 0:
            await self.save_checkpoint()
    
    async def save_checkpoint(self):
        """Save current progress to checkpoint file and auto-save results."""
        # Save checkpoint JSON
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
        
        # Auto-save results to JSON
        if self.results:
            self.autosave_json.parent.mkdir(parents=True, exist_ok=True)
            with open(self.autosave_json, "w", encoding="utf-8") as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
            
            # Auto-save results to Excel
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
        
        logger.info(f"💾 Auto-saved: {len(self.results)} results, {self.completed} URLs processed")
    
    async def run(self) -> list[dict]:
        """
        Run the scraper on all URLs.
        
        Returns:
            List of bakery data dictionaries
        """
        logger.info(f"Starting scraper for {len(self.urls)} URLs")
        logger.info(f"Max concurrent: {self.max_concurrent}, Base delay: {BASE_DELAY}s")
        
        start_time = datetime.now()
        
        async with AsyncSession(impersonate="chrome120") as session:
            tasks = [
                self.process_url(session, url, i)
                for i, url in enumerate(self.urls)
            ]
            
            await asyncio.gather(*tasks)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        
        logger.info(f"Scraping completed in {elapsed:.1f}s")
        logger.info(f"Total: {self.completed}, Success: {self.success}, Failed: {self.failed}")
        logger.info(f"Rate: {self.completed/elapsed:.2f} URLs/sec")
        
        return self.results
    
    def save_results(self, results: list[dict]):
        """
        Save results to JSON and Excel files.
        
        Args:
            results: List of bakery data dictionaries
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create output directories
        json_dir = self.output_dir / "json"
        excel_dir = self.output_dir / "excel"
        json_dir.mkdir(parents=True, exist_ok=True)
        excel_dir.mkdir(parents=True, exist_ok=True)
        
        # Save JSON
        json_path = json_dir / f"marie_blachere_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.success(f"JSON saved: {json_path}")
        
        # Save Excel
        excel_path = excel_dir / f"marie_blachere_{timestamp}.xlsx"
        
        # Flatten opening_hours for Excel
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
            failed_path = self.output_dir / f"marie_blachere_failed_{timestamp}.txt"
            with open(failed_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.failed_urls))
            logger.warning(f"Failed URLs saved: {failed_path}")
        
        return json_path, excel_path


async def main():
    """Main entry point."""
    # Setup logging
    output_dir = Path(__file__).parent.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Configure loguru for detailed logging
    logger.remove()  # Remove default handler
    
    # Console logging - INFO level
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{function}</cyan> - <level>{message}</level>",
        level="INFO",
        colorize=True,
    )
    
    # File logging - DEBUG level for all operations
    logger.add(
        output_dir / "marie_blachere.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )
    
    # Separate file for FAILED URLs only - easy to retry later
    logger.add(
        output_dir / "marie_blachere_failed_urls.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {message}",
        level="ERROR",
        filter=lambda record: "FAILED_URL" in record["extra"],
        rotation="5 MB",
        encoding="utf-8",
    )
    
    logger.info("=" * 60)
    logger.info("Marie Blachère Scraper - France")
    logger.info("=" * 60)
    
    # Load URLs from CSV
    csv_path = Path(__file__).parent.parent.parent / "FRANCE-URL.csv"
    
    if not csv_path.exists():
        logger.error(f"CSV file not found: {csv_path}")
        return
    
    df = pd.read_csv(csv_path)
    urls = df.iloc[:, 0].dropna().tolist()  # First column contains URLs
    
    logger.info(f"Loaded {len(urls)} URLs from {csv_path}")
    
    # Run scraper
    scraper = MarieBlachereScraper(
        urls=urls,
        output_dir=output_dir,
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
