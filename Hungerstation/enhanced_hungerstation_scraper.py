"""
Enhanced HungerStation Al-Muruj Restaurant Scraper
Focuses on JSON-LD data extraction with advanced rate limiting and 429 error handling
Features: Async/Concurrent processing, Exponential backoff, Jitter, Rate limiting, Rich UI
"""

import os
import re
import json
import asyncio
import random
import time
import logging
from datetime import datetime
from urllib.parse import urljoin, urlparse
from typing import List, Dict, Optional, Tuple
import aiohttp
import pandas as pd
from dataclasses import dataclass
import sys
from bs4 import BeautifulSoup
import backoff
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from collections import deque
import statistics
from curl_cffi import requests as curl_requests
from curl_cffi.requests import AsyncSession
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed
import queue
import threading
from functools import partial

# Configure UTF-8 encoding
sys.stdout.reconfigure(encoding='utf-8')

# === Oxylabs Proxy Configuration ===
# Set these in your local environment before running.
PROXY_CONFIG = {
    "username": os.getenv("OXYLABS_USERNAME", ""),
    "password": os.getenv("OXYLABS_PASSWORD", ""),
    "country": os.getenv("OXYLABS_COUNTRY", "sa"),
    "server": os.getenv("OXYLABS_SERVER", "http://pr.oxylabs.io:7777")
}

# === Cuisine Filtering Configuration ===
# Only restaurants with these cuisines will be included
# If a restaurant has ANY of these cuisines, it will be scraped

# === Enhanced Configuration ===
BASE_URL = "https://hungerstation.com/sa-en/restaurants/riyadh/jabrah"
OUTPUT_DIR = "scraped_data"

JSONLD_INPUT_FILE = "scraped_data/Jabrah_jsonld_data.json"
RESTAURANT_URLS_FILE = "Jabrah_restaurant_urls.json"  # Fallback
URLS_FILE = "scraped_data/Jabrah_restaurant_urls.json"  # Main URLs file for loading
RESTAURANT_DATA_FILE = "Jabrah_enhanced_restaurant_data.json"
EXCEL_OUTPUT_FILE = "Jabrah_enhanced_restaurant_data.xlsx"
LOG_FILE = "Jabrah_enhanced_scraping_log.txt"
PROGRESS_FILE = "scraped_data/Jabrah_enhanced_progress.json"  # Resume functionality checkpoint file

# Enhanced performance configuration - Optimized for speed with intelligent rate limiting
MAX_CONCURRENT_REQUESTS = 15  # Increased from 12 for better parallelism
MIN_CONCURRENT_REQUESTS = 3  # Reduced minimum concurrent requests
BASE_DELAY = 0.6  # Reduced from 0.8s for faster response times
MAX_DELAY = 120.0  # Increased maximum delay for better handling of long rate limits
JITTER_RANGE = 0.3  # Reduced jitter for more predictable timing
MAX_RETRIES = 7  # Increased retries for better resilience
TIMEOUT_SECONDS = 20  # Reduced timeout for faster failure detection

# === Multiprocessing Configuration ===
NUM_PROCESSES = min(mp.cpu_count(), 4)  # Reduced to 2 processes to be less aggressive
URLS_PER_PROCESS = 100  # Reduced URLs per process for better rate limiting
PROCESS_BATCH_SIZE = 25  # Further reduced batch size for gentler requests

# Advanced Rate Limiting Configuration
RATE_LIMIT_WINDOW = 60  # 1 minute window for rate tracking
SUCCESS_RATE_THRESHOLD = 0.75  # Minimum success rate to maintain current concurrency
CIRCUIT_BREAKER_THRESHOLD = 8  # Number of consecutive failures to trigger circuit breaker
CIRCUIT_BREAKER_TIMEOUT = 20  # Seconds to wait before retrying after circuit break

# Auto-save Configuration
AUTOSAVE_INTERVAL = 15  # Save every 15 restaurants for better performance and safety in multiprocessing

# Allowed cuisine types
ALLOWED_CUISINES = {
    "Fast Food", "Sandwich", "Arabic", "Desserts", "American", "Beverages", 
    "Coffee", "International", "Shawarma", "Italian", "Bakery", "Grill", 
    "Seafood", "Juices", "Asian", "Healthy", "Indian", "Egyptian", "Mexican", 
    "Pizza", "Burgers", "Ice Cream", "Saudi", "Sushi", "Thai", "Pasta", 
    "Japanese", "Vegetarian", "Hot Dogs", "Pakistani", "Lebanese", 
    "Salads", "Falafel", "Indonesian", "Breakfast", "Gluten Free"
}

# Create output directory
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Multiprocessing worker function
def scrape_urls_worker(url_batch: List[str], process_id: int) -> List[Dict]:
    """Worker function for multiprocessing - scrapes a batch of URLs"""
    import asyncio
    
    # Create a new event loop for this process
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Create scraper instance for this process
        scraper = EnhancedHungerStationScraper()
        scraper.logger.info(f"Process {process_id} starting with {len(url_batch)} URLs")
        
        # Run async scraping in this process
        results = loop.run_until_complete(scraper.scrape_batch_async(url_batch, process_id))
        
        scraper.logger.info(f"Process {process_id} completed: {len(results)} restaurants scraped")
        return results
        
    except Exception as e:
        print(f"Error in process {process_id}: {e}")
        return []
    finally:
        loop.close()

def chunk_urls(urls: List[str], num_processes: int) -> List[List[str]]:
    """Split URLs into chunks for multiprocessing"""
    chunk_size = max(1, len(urls) // num_processes)
    chunks = []
    
    for i in range(0, len(urls), chunk_size):
        chunk = urls[i:i + chunk_size]
        if chunk:
            chunks.append(chunk)
    
    # Ensure we don't have more chunks than processes
    while len(chunks) > num_processes:
        # Merge the smallest chunks
        chunks.sort(key=len)
        chunks[1].extend(chunks[0])
        chunks.pop(0)
    
    return chunks

@dataclass
class RestaurantData:
    """Data class for restaurant information"""
    url: str
    vendor_id: str = ""
    name: str = ""
    description: str = ""
    cuisine_type: str = ""
    filtered_cuisines: List[str] = None
    address: str = ""
    telephone: str = ""
    price_range: str = ""
    rating: str = ""
    review_count: str = ""
    opening_hours: str = ""
    image_url: str = ""
    latitude: str = ""
    longitude: str = ""
    menu_items: List[Dict] = None
    json_ld_raw: Dict = None
    scrape_timestamp: str = ""
    
    def __post_init__(self):
        if self.menu_items is None:
            self.menu_items = []
        if self.filtered_cuisines is None:
            self.filtered_cuisines = []
        if self.scrape_timestamp == "":
            self.scrape_timestamp = datetime.now().isoformat()

class AdvancedRateLimiter:
    """Advanced rate limiter with adaptive concurrency and circuit breaker"""
    
    def __init__(self, max_requests_per_second: float = 1.0):  # Updated default to match optimized rate
        self.max_requests_per_second = max_requests_per_second
        self.min_interval = 1.0 / max_requests_per_second
        self.last_request_time = 0
        self.consecutive_429s = 0
        self.lock = asyncio.Lock()
        
        # Advanced features
        self.request_times = deque(maxlen=100)  # Track recent request times
        self.success_times = deque(maxlen=100)  # Track successful requests
        self.failure_times = deque(maxlen=50)   # Track failed requests
        self.current_concurrency = MAX_CONCURRENT_REQUESTS
        self.circuit_breaker_active = False
        self.circuit_breaker_start_time = 0
        self.consecutive_failures = 0
    
    async def acquire(self):
        """Acquire permission to make a request with adaptive rate limiting"""
        async with self.lock:
            # Check circuit breaker
            if self.circuit_breaker_active:
                if time.time() - self.circuit_breaker_start_time > CIRCUIT_BREAKER_TIMEOUT:
                    self.circuit_breaker_active = False
                    self.consecutive_failures = 0
                    logging.info("Circuit breaker reset - resuming requests")
                else:
                    await asyncio.sleep(5)  # Wait before retrying
                    return
            
            current_time = time.time()
            time_since_last = current_time - self.last_request_time
            
            # Calculate dynamic delay based on recent performance
            base_delay = self.calculate_adaptive_delay()
            
            # Add jitter to prevent thundering herd
            jitter = random.uniform(-JITTER_RANGE, JITTER_RANGE)
            required_delay = max(0, base_delay + jitter)
            
            if time_since_last < required_delay:
                sleep_time = required_delay - time_since_last
                await asyncio.sleep(sleep_time)
            
            self.last_request_time = time.time()
            self.request_times.append(self.last_request_time)
    
    def calculate_adaptive_delay(self) -> float:
        """Calculate adaptive delay based on recent success/failure rates"""
        if len(self.request_times) < 10:
            return self.min_interval
        
        # Calculate success rate in recent window
        current_time = time.time()
        recent_successes = sum(1 for t in self.success_times if current_time - t < RATE_LIMIT_WINDOW)
        recent_failures = sum(1 for t in self.failure_times if current_time - t < RATE_LIMIT_WINDOW)
        
        if recent_successes + recent_failures == 0:
            return self.min_interval
        
        success_rate = recent_successes / (recent_successes + recent_failures)
        
        # Adjust delay based on success rate
        if success_rate >= SUCCESS_RATE_THRESHOLD:
            # Good performance - reduce delay slightly
            return max(self.min_interval * 0.8, 0.5)
        elif success_rate >= 0.7:
            # Moderate performance - maintain current delay
            return self.min_interval
        else:
            # Poor performance - increase delay
            return min(self.min_interval * 2, MAX_DELAY)
    
    def record_success(self):
        """Record a successful request"""
        self.success_times.append(time.time())
        self.consecutive_failures = 0
        if self.consecutive_429s > 0:
            self.consecutive_429s = max(0, self.consecutive_429s - 1)
    
    def record_failure(self, is_rate_limit: bool = False):
        """Record a failed request"""
        self.failure_times.append(time.time())
        self.consecutive_failures += 1
        
        if is_rate_limit:
            self.consecutive_429s += 1
        
        # Trigger circuit breaker if too many consecutive failures
        if self.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            self.circuit_breaker_active = True
            self.circuit_breaker_start_time = time.time()
            logging.warning(f"Circuit breaker activated after {self.consecutive_failures} consecutive failures")
    
    def get_current_concurrency(self) -> int:
        """Get adaptive concurrency level based on performance"""
        if self.circuit_breaker_active:
            return 1
        
        current_time = time.time()
        recent_successes = sum(1 for t in self.success_times if current_time - t < RATE_LIMIT_WINDOW)
        recent_failures = sum(1 for t in self.failure_times if current_time - t < RATE_LIMIT_WINDOW)
        
        if recent_successes + recent_failures < 10:
            return self.current_concurrency
        
        success_rate = recent_successes / (recent_successes + recent_failures)
        
        if success_rate >= SUCCESS_RATE_THRESHOLD:
            # Good performance - can increase concurrency
            self.current_concurrency = min(MAX_CONCURRENT_REQUESTS, self.current_concurrency + 1)
        elif success_rate < 0.7:
            # Poor performance - reduce concurrency
            self.current_concurrency = max(MIN_CONCURRENT_REQUESTS, self.current_concurrency - 1)
        
        return self.current_concurrency

class EnhancedHungerStationScraper:
    """Enhanced scraper with advanced error handling and rate limiting"""
    
    def __init__(self):
        self.session_id = random.randint(10000, 99999)
        self.restaurant_urls = []
        self.restaurant_data = []
        self.scraped_restaurants = []  # Add this for compatibility
        self.scraped_count = 0
        self.failed_count = 0
        self.rate_limiter = AdvancedRateLimiter(max_requests_per_second=1.0)  # Increased from 0.7 to 1.0 for better performance
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self.unique_restaurants = set()  # Track unique vendor_id + coordinates
        self.start_time = None
        self.proxy_info = {}
        
        # Progress tracking variables
        self.total_urls = 0
        self.processed_urls = 0
        self.remaining_urls = 0
        self.scraping_start_time = None
        self.last_progress_update = None
        self.urls_per_minute = 0.0
        self.estimated_completion_time = None
        
        # Resume functionality variables
        self.processed_urls_set = set()  # Track processed URLs for resume functionality
        self.checkpoint_save_interval = 10  # Save progress every 10 successful scrapes
        self.last_checkpoint_save = 0
        
        # Setup logging with UTF-8 encoding
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(os.path.join(OUTPUT_DIR, LOG_FILE), encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Set console handler encoding to handle Unicode characters
        for handler in self.logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.stream.reconfigure(encoding='utf-8', errors='replace')
    
    def test_proxy_connection(self) -> Dict[str, str]:
        """Test proxy connection and get IP information"""
        import requests
        
        try:
            self.logger.info("Testing proxy connection...")
            
            # Create proxy URL using the simplified format
            proxy_user = f"customer-{PROXY_CONFIG['username']}-cc-{PROXY_CONFIG['country'].lower()}"
            proxy_url = f"http://{proxy_user}:{PROXY_CONFIG['password']}@pr.oxylabs.io:7777"
            proxies = {
                'http': proxy_url,
                'https': proxy_url
            }
            
            # Test with IP detection service
            ip_data = {}
            try:
                response = requests.get('http://httpbin.org/ip', proxies=proxies, timeout=15)
                if response.status_code == 200:
                    ip_data = response.json()
                    self.logger.info(f"IP detection successful: {ip_data.get('origin', 'Unknown')}")
                else:
                    self.logger.warning(f"IP detection failed with status {response.status_code}")
            except Exception as e:
                self.logger.error(f"IP detection request failed: {e}")
                # Don't raise here, continue with geolocation test
            
            # Get more detailed info
            location_data = {}
            try:
                response2 = requests.get('http://ip-api.com/json/', proxies=proxies, timeout=15)
                if response2.status_code == 200:
                    location_data = response2.json()
                    self.logger.info(f"Geolocation successful: {location_data.get('country', 'Unknown')}")
                else:
                    self.logger.warning(f"Geolocation failed with status {response2.status_code}")
            except Exception as e:
                self.logger.error(f"Geolocation request failed: {e}")
            
            # Build proxy info with fallbacks
            proxy_info = {
                'ip': ip_data.get('origin', 'Unknown'),
                'country': location_data.get('country', 'Unknown'),
                'region': location_data.get('regionName', 'Unknown'),
                'city': location_data.get('city', 'Unknown'),
                'isp': location_data.get('isp', 'Unknown'),
                'status': 'Connected' if (ip_data or location_data) else 'Failed'
            }
            
            # Log proxy info
            self.logger.info(f"Connection Status: {proxy_info['status']}")
            self.logger.info(f"IP Address: {proxy_info['ip']}")
            self.logger.info(f"Country: {proxy_info['country']}")
            self.logger.info(f"Region: {proxy_info['region']}")
            self.logger.info(f"City: {proxy_info['city']}")
            self.logger.info(f"ISP: {proxy_info['isp']}")
            
            return proxy_info
            
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            proxy_info = {
                'ip': 'Unknown',
                'country': 'Unknown', 
                'region': 'Unknown',
                'city': 'Unknown',
                'isp': 'Unknown',
                'status': 'Failed'
            }
            return proxy_info
    
    def load_progress_data(self) -> dict:
        """Load progress data from checkpoint file without filtering URLs."""
        try:
            if os.path.exists(PROGRESS_FILE):
                with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                    progress_data = json.load(f)
                    self.logger.info(f"📄 Loaded progress data: {progress_data}")
                    return progress_data
        except Exception as e:
            self.logger.error(f"❌ Error loading progress data: {e}")
        return {}

    def load_all_restaurant_urls(self) -> List[str]:
        """Load all restaurant URLs without filtering (for chunk-based processing)."""
        try:
            with open(URLS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Extract URLs from JSON data
                urls = []
                for item in data:
                    if isinstance(item, dict) and 'url' in item:
                        urls.append(item['url'])
                self.logger.info(f"📄 Loaded {len(urls)} total URLs from {URLS_FILE}")
                return urls
        except FileNotFoundError:
            self.logger.error(f"❌ URLs file not found: {URLS_FILE}")
            return []
        except Exception as e:
            self.logger.error(f"❌ Error loading URLs: {e}")
            return []

    def load_progress(self) -> set:
        """Load previously processed URLs from checkpoint file."""
        try:
            if os.path.exists(PROGRESS_FILE):
                with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                    progress_data = json.load(f)
                    
                    # Handle both old format (processed_urls as int) and new format (as list)
                    processed_urls_data = progress_data.get('processed_urls', [])
                    
                    if isinstance(processed_urls_data, int):
                        # Old format - we have count but not actual URLs
                        # Check if we have completed chunks info
                        completed_chunks = progress_data.get('completed_chunks', 0)
                        total_processed = progress_data.get('total_processed', 0)
                        
                        self.logger.info(f"🔄 RESUMING: Found progress with {total_processed} URLs processed ({completed_chunks} chunks completed)")
                        
                        # For now, return empty set and let the multiprocessing logic handle chunk resumption
                        # The chunk-based progress will be handled in the multiprocessing section
                        return set()
                    else:
                        # New format - we have actual URLs list
                        processed_urls = set(processed_urls_data)
                        self.logger.info(f"Loaded {len(processed_urls)} previously processed URLs from checkpoint")
                        return processed_urls
            else:
                self.logger.info("No checkpoint file found, starting fresh")
                return set()
        except Exception as e:
            self.logger.error(f"Error loading progress file: {e}")
            return set()
    
    def save_progress(self, processed_urls: set, additional_data: dict = None):
        """Save current progress to checkpoint file."""
        try:
            progress_data = {
                'processed_urls': list(processed_urls),
                'last_updated': datetime.now().isoformat(),
                'total_processed': len(processed_urls)
            }
            
            # Add any additional data if provided
            if additional_data:
                progress_data.update(additional_data)
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
            
            with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
                json.dump(progress_data, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"Progress saved: {len(processed_urls)} URLs processed")
            
        except Exception as e:
            self.logger.error(f"Error saving progress: {e}")
    
    def display_startup_info(self, total_urls: int):
        """Display startup information"""
        self.logger.info("Enhanced HungerStation Scraper")
        self.logger.info(f"Session ID: {self.session_id}")
        self.logger.info(f"Total URLs: {total_urls:,}")
        self.logger.info(f"Concurrent Requests: {MAX_CONCURRENT_REQUESTS}")
        self.logger.info(f"Autosave Interval: {AUTOSAVE_INTERVAL}")
        self.logger.info(f"Base Delay: {BASE_DELAY}s")
        self.logger.info(f"Max Retries: {MAX_RETRIES}")
        self.logger.info(f"Output Directory: {OUTPUT_DIR}")
        self.logger.info(f"Proxy IP: {self.proxy_info.get('ip', 'Unknown')}")
        self.logger.info(f"Location: {self.proxy_info.get('city', 'Unknown')}, {self.proxy_info.get('country', 'Unknown')}")
        self.logger.info(f"ISP: {self.proxy_info.get('isp', 'Unknown')}")
        self.logger.info("Scraper Ready")
        
    def calculate_eta(self, processed: int, total: int, elapsed_time: float) -> str:
        """Calculate estimated time of arrival"""
        if processed == 0:
            return "Calculating..."
        
        rate = processed / elapsed_time
        remaining = total - processed
        eta_seconds = remaining / rate if rate > 0 else 0
        
        if eta_seconds < 60:
            return f"{eta_seconds:.0f}s"
        elif eta_seconds < 3600:
            return f"{eta_seconds/60:.1f}m"
        else:
            return f"{eta_seconds/3600:.1f}h"
        
    def create_proxy_auth(self) -> aiohttp.BasicAuth:
        """Create proxy authentication with simplified sticky session format"""
        proxy_user = f"customer-{PROXY_CONFIG['username']}-cc-{PROXY_CONFIG['country'].lower()}"
        return aiohttp.BasicAuth(proxy_user, PROXY_CONFIG["password"])
    
    def get_headers(self) -> Dict[str, str]:
        """Get randomized headers"""
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ]
        
        return {
            "User-Agent": random.choice(user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0"
        }
    
    def get_curl_cffi_headers(self) -> Dict[str, str]:
        """Get headers optimized for curl_cffi with better anti-detection"""
        return {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'Cache-Control': 'max-age=0',
        }
    
    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True
    )
    async def make_request(self, session: aiohttp.ClientSession, url: str) -> Tuple[Optional[str], int]:
        """Make HTTP request with tenacity retry and advanced error handling"""
        async with self.semaphore:
            await self.rate_limiter.acquire()
            
            try:
                proxy_url = PROXY_CONFIG["server"]
                proxy_auth = self.create_proxy_auth()
                
                timeout = aiohttp.ClientTimeout(total=30, connect=10)
                
                async with session.get(
                    url,
                    headers=self.get_headers(),
                    proxy=proxy_url,
                    proxy_auth=proxy_auth,
                    timeout=timeout,
                    ssl=False
                ) as response:
                    
                    if response.status == 429:
                        self.rate_limiter.record_failure(is_rate_limit=True)
                        self.logger.warning(f"429 Too Many Requests for {url}. Backing off...")
                        
                        # Dynamic backoff based on consecutive 429s
                        backoff_time = min(BASE_DELAY * (2 ** self.rate_limiter.consecutive_429s), MAX_DELAY)
                        jitter = random.uniform(0, backoff_time * 0.1)
                        await asyncio.sleep(backoff_time + jitter)
                        
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=response.history,
                            status=429,
                            message="Rate limited"
                        )
                    
                    if response.status == 200:
                        self.rate_limiter.record_success()
                        content = await response.text()
                        return content, response.status
                    else:
                        self.rate_limiter.record_failure()
                        self.logger.warning(f"HTTP {response.status} for {url}")
                        return None, response.status
                        
            except aiohttp.ClientResponseError as e:
                if e.status == 429:
                    self.rate_limiter.record_failure(is_rate_limit=True)
                    self.logger.error(f"429 error persists for {url} after backoff")
                    raise
                else:
                    self.rate_limiter.record_failure()
                    self.logger.error(f"Client response error for {url}: {e}")
                    return None, e.status
            except Exception as e:
                self.rate_limiter.record_failure()
                self.logger.error(f"Request failed for {url}: {str(e)}")
                raise
    
    async def make_request_with_curl_cffi(self, url: str) -> Tuple[Optional[str], int]:
        """Make HTTP request using curl_cffi for better anti-detection with exponential backoff"""
        try:
            await self.rate_limiter.acquire()
            
            headers = self.get_curl_cffi_headers()
            
            # Prepare proxy configuration for curl_cffi using correct Oxylabs format
            proxy_user = f"customer-{PROXY_CONFIG['username']}-cc-{PROXY_CONFIG['country'].lower()}"
            proxy_url = f"http://{proxy_user}:{PROXY_CONFIG['password']}@{PROXY_CONFIG['server'].replace('http://', '')}"
            
            # Use curl_cffi with Chrome impersonation AND proxy
            response = await AsyncSession(
                impersonate="chrome120",
                proxies={"http": proxy_url, "https": proxy_url}
            ).get(
                url, 
                headers=headers,
                timeout=TIMEOUT_SECONDS,
                allow_redirects=True
            )
            
            if response.status_code == 429:
                self.rate_limiter.record_failure(is_rate_limit=True)
                retry_after = int(response.headers.get('Retry-After', BASE_DELAY * 2))
                
                # Enhanced exponential backoff for rate limiting
                backoff_multiplier = min(2 ** self.rate_limiter.consecutive_429s, 8)  # Cap at 8x
                enhanced_delay = max(retry_after, BASE_DELAY * backoff_multiplier)
                jitter = random.uniform(0, enhanced_delay * 0.2)  # 20% jitter
                total_delay = min(enhanced_delay + jitter, MAX_DELAY)
                
                await asyncio.sleep(total_delay)
                raise Exception(f"Rate limited, retry after {int(total_delay)}s")
            
            if response.status_code == 200:
                self.rate_limiter.record_success()
                return response.text, response.status_code
            else:
                self.rate_limiter.record_failure()
                return None, response.status_code
                
        except asyncio.TimeoutError:
            self.rate_limiter.record_failure()
            logging.warning(f"Timeout for URL: {url}")
            raise
        except Exception as e:
            self.rate_limiter.record_failure()
            logging.warning(f"Error with curl_cffi for URL {url}: {e}")
            raise
    
    def extract_json_ld_data(self, html_content: str) -> Optional[Dict]:
        """Extract JSON-LD data from HTML content"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            json_ld_scripts = soup.find_all('script', type='application/ld+json')
            
            for script in json_ld_scripts:
                try:
                    json_data = json.loads(script.string)
                    # Look for Restaurant or LocalBusiness schema
                    if isinstance(json_data, dict):
                        if json_data.get('@type') in ['Restaurant', 'LocalBusiness', 'FoodEstablishment']:
                            return json_data
                    elif isinstance(json_data, list):
                        for item in json_data:
                            if isinstance(item, dict) and item.get('@type') in ['Restaurant', 'LocalBusiness', 'FoodEstablishment']:
                                return item
                except json.JSONDecodeError:
                    continue
            
            return None
        except Exception as e:
            self.logger.error(f"Error extracting JSON-LD data: {str(e)}")
            return None
    
    def parse_restaurant_data(self, json_ld: Dict, url: str) -> RestaurantData:
        """Parse JSON-LD data into RestaurantData object"""
        try:
            restaurant = RestaurantData(url=url, json_ld_raw=json_ld)
            
            # Extract vendor ID from URL or @id
            vendor_id = ""
            if '@id' in json_ld:
                # Extract from @id: https://hungerstation.com/sa-en/restaurant/riyadh/al-muruj/51676
                vendor_id = json_ld['@id'].split('/')[-1]
            elif url:
                # Extract from URL as fallback
                vendor_id = url.split('/')[-1]
            restaurant.vendor_id = vendor_id
            
            # Extract basic information
            restaurant.name = json_ld.get('name', '')
            restaurant.description = json_ld.get('description', '')
            
            # Extract and filter cuisine types
            cuisine_data = json_ld.get('servesCuisine', [])
            if isinstance(cuisine_data, list):
                all_cuisines = cuisine_data
                restaurant.cuisine_type = ', '.join(all_cuisines)
                # Filter cuisines to only include allowed ones
                restaurant.filtered_cuisines = [c for c in all_cuisines if c in ALLOWED_CUISINES]
            else:
                restaurant.cuisine_type = str(cuisine_data) if cuisine_data else ''
                if cuisine_data and cuisine_data in ALLOWED_CUISINES:
                    restaurant.filtered_cuisines = [cuisine_data]
            
            # Extract address
            address_data = json_ld.get('address', {})
            if isinstance(address_data, dict):
                address_parts = []
                for key in ['streetAddress', 'addressLocality', 'addressRegion', 'postalCode']:
                    if address_data.get(key):
                        address_parts.append(str(address_data[key]))
                restaurant.address = ', '.join(address_parts)
            elif isinstance(address_data, str):
                restaurant.address = address_data
            
            # Extract geo coordinates
            geo_data = json_ld.get('geo', {})
            if isinstance(geo_data, dict):
                restaurant.latitude = str(geo_data.get('latitude', ''))
                restaurant.longitude = str(geo_data.get('longitude', ''))
            
            # Extract contact information
            restaurant.telephone = json_ld.get('telephone', '')
            
            # Extract price range
            restaurant.price_range = json_ld.get('priceRange', '')
            
            # Extract rating information
            rating_data = json_ld.get('aggregateRating', {})
            if rating_data:
                restaurant.rating = str(rating_data.get('ratingValue', ''))
                restaurant.review_count = str(rating_data.get('reviewCount', ''))
            
            # Extract opening hours
            opening_hours = json_ld.get('openingHours', [])
            if opening_hours:
                if isinstance(opening_hours, list):
                    restaurant.opening_hours = '; '.join(opening_hours)
                else:
                    restaurant.opening_hours = str(opening_hours)
            
            # Extract image
            image = json_ld.get('image', '')
            if isinstance(image, list) and image:
                restaurant.image_url = image[0] if isinstance(image[0], str) else image[0].get('url', '')
            elif isinstance(image, dict):
                restaurant.image_url = image.get('url', '')
            elif isinstance(image, str):
                restaurant.image_url = image
            
            # Extract menu items if available
            menu = json_ld.get('hasMenu', {})
            if menu and isinstance(menu, dict):
                menu_items = menu.get('hasMenuSection', [])
                if menu_items:
                    for section in menu_items:
                        if isinstance(section, dict):
                            items = section.get('hasMenuItem', [])
                            for item in items:
                                if isinstance(item, dict):
                                    restaurant.menu_items.append({
                                        'name': item.get('name', ''),
                                        'description': item.get('description', ''),
                                        'price': item.get('offers', {}).get('price', '')
                                    })
            
            return restaurant
            
        except Exception as e:
            self.logger.error(f"Error parsing restaurant data: {str(e)}")
            return RestaurantData(url=url)
    
    async def scrape_restaurant(self, session: aiohttp.ClientSession, url: str) -> Optional[RestaurantData]:
        """Scrape individual restaurant with enhanced error handling using curl_cffi"""
        try:
            # Try curl_cffi first for better anti-detection
            try:
                html_content, status_code = await self.make_request_with_curl_cffi(url)
            except Exception as curl_error:
                # Fallback to aiohttp if curl_cffi fails
                self.logger.warning(f"curl_cffi failed for {url}, falling back to aiohttp: {curl_error}")
                html_content, status_code = await self.make_request(session, url)
            
            if html_content is None:
                self.logger.warning(f"Failed to fetch content for {url} (Status: {status_code})")
                return None
            
            # Extract JSON-LD data
            json_ld = self.extract_json_ld_data(html_content)
            if not json_ld:
                self.logger.warning(f"No JSON-LD data found for {url}")
                return None
            
            # Parse restaurant data
            restaurant_data = self.parse_restaurant_data(json_ld, url)
            
            # Validate required fields
            if not restaurant_data.name or not restaurant_data.vendor_id:
                self.logger.warning(f"Missing required data for {url}")
                return None
            
            self.logger.info(f"Successfully scraped: {restaurant_data.name} (ID: {restaurant_data.vendor_id})")
            return restaurant_data
            
        except Exception as e:
            self.logger.error(f"Error scraping restaurant {url}: {str(e)}")
            return None
    
    async def scrape_restaurants_concurrent(self, urls: List[str]) -> List[RestaurantData]:
        """Scrape restaurants with intelligent batch processing and adaptive concurrency"""
        restaurant_data = []
        batch_size = 50  # Process in smaller batches for better memory management
        
        # Create optimized connector with connection pooling
        connector = aiohttp.TCPConnector(
            limit=MAX_CONCURRENT_REQUESTS * 2,  # Connection pool size
            limit_per_host=MAX_CONCURRENT_REQUESTS,
            ttl_dns_cache=300,  # DNS cache for 5 minutes
            use_dns_cache=True,
            keepalive_timeout=60,  # Keep connections alive
            enable_cleanup_closed=True
        )
        
        timeout = aiohttp.ClientTimeout(
            total=TIMEOUT_SECONDS,
            connect=10,  # Connection timeout
            sock_read=20  # Socket read timeout
        )
        
        async with aiohttp.ClientSession(
            headers=self.get_headers(),
            connector=connector,
            timeout=timeout
        ) as session:
            
            for batch_start in range(0, len(urls), batch_size):
                batch_urls = urls[batch_start:batch_start + batch_size]
                batch_num = (batch_start // batch_size) + 1
                total_batches = (len(urls) + batch_size - 1) // batch_size
                
                self.logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch_urls)} URLs)")
                
                # Adaptive concurrency based on performance
                current_concurrency = self.rate_limiter.get_current_concurrency()
                semaphore = asyncio.Semaphore(current_concurrency)
                
                async def scrape_with_semaphore(url: str) -> Optional[RestaurantData]:
                    async with semaphore:
                        return await self.scrape_restaurant_with_validation(session, url)
                
                # Process batch with adaptive concurrency
                batch_tasks = [scrape_with_semaphore(url) for url in batch_urls]
                batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
                
                # Process results and handle exceptions
                successful_results = []
                for i, result in enumerate(batch_results):
                    if isinstance(result, Exception):
                        self.logger.error(f"Exception in batch processing: {result}")
                        self.failed_count += 1
                    elif result is not None:
                        successful_results.append(result)
                        self.scraped_count += 1
                    else:
                        self.failed_count += 1
                
                restaurant_data.extend(successful_results)
                
                # Auto-save progress
                if len(restaurant_data) % AUTOSAVE_INTERVAL == 0 and restaurant_data:
                    self.save_data(restaurant_data)
                    self.logger.info(f"Auto-saved {len(restaurant_data)} restaurants")
                
                # Log batch progress
                success_rate = len(successful_results) / len(batch_urls) * 100
                self.logger.info(f"Batch {batch_num} completed: {len(successful_results)}/{len(batch_urls)} successful ({success_rate:.1f}%)")
                
                # Brief pause between batches to prevent overwhelming the server
                if batch_start + batch_size < len(urls):
                    await asyncio.sleep(random.uniform(1, 3))
        
        return restaurant_data
    
    async def scrape_restaurant_with_validation(self, session: aiohttp.ClientSession, url: str) -> Optional[RestaurantData]:
        """Scrape restaurant with validation and uniqueness checks using curl_cffi"""
        try:
            # Try curl_cffi first for better anti-detection
            try:
                html_content, status_code = await self.make_request_with_curl_cffi(url)
            except Exception as curl_error:
                # Fallback to aiohttp if curl_cffi fails
                self.logger.warning(f"curl_cffi failed for {url}, falling back to aiohttp: {curl_error}")
                html_content, status_code = await self.make_request(session, url)
            
            if html_content is None:
                return None
            
            # Extract JSON-LD data
            json_ld = self.extract_json_ld_data(html_content)
            if not json_ld:
                return None
            
            # Parse restaurant data
            restaurant_data = self.parse_restaurant_data(json_ld, url)
            
            if not restaurant_data:
                return None
            
            # Check for uniqueness based on vendor_id and coordinates
            unique_key = f"{restaurant_data.vendor_id}_{restaurant_data.latitude}_{restaurant_data.longitude}"
            
            if unique_key in self.unique_restaurants:
                self.logger.warning(f"Duplicate restaurant found (vendor_id: {restaurant_data.vendor_id}). Skipping...")
                return None
            
            # Only include restaurants with allowed cuisines
            if not restaurant_data.filtered_cuisines:
                self.logger.info(f"Restaurant {restaurant_data.name} has no allowed cuisines. Skipping...")
                return None
            
            # Add to unique set
            self.unique_restaurants.add(unique_key)
            
            self.logger.info(f"✅ Successfully processed: {restaurant_data.name} (Total: {self.scraped_count + 1:,})")
            return restaurant_data
            
        except Exception as e:
            self.logger.error(f"Error in restaurant validation for {url}: {str(e)}")
            return None
    
    def save_data(self, restaurant_data: List[RestaurantData]):
        """Save scraped data to JSON and Excel files with unique entries only"""
        try:
            # Convert to dictionaries for JSON serialization
            data_dicts = []
            for restaurant in restaurant_data:
                data_dict = {
                    'url': restaurant.url,
                    'vendor_id': restaurant.vendor_id,
                    'name': restaurant.name,
                    'description': restaurant.description,
                    'cuisine_type': restaurant.cuisine_type,
                    'filtered_cuisines': restaurant.filtered_cuisines,
                    'address': restaurant.address,
                    'telephone': restaurant.telephone,
                    'price_range': restaurant.price_range,
                    'rating': restaurant.rating,
                    'review_count': restaurant.review_count,
                    'opening_hours': restaurant.opening_hours,
                    'image_url': restaurant.image_url,
                    'latitude': restaurant.latitude,
                    'longitude': restaurant.longitude,
                    'menu_items_count': len(restaurant.menu_items),
                    'menu_items': restaurant.menu_items,
                    'json_ld_raw': restaurant.json_ld_raw,
                    'scrape_timestamp': restaurant.scrape_timestamp
                }
                data_dicts.append(data_dict)
            
            # Save to JSON
            json_path = os.path.join(OUTPUT_DIR, RESTAURANT_DATA_FILE)
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data_dicts, f, indent=2, ensure_ascii=False)
            
            # Save to Excel
            excel_path = os.path.join(OUTPUT_DIR, EXCEL_OUTPUT_FILE)
            
            # Create DataFrame for main data (excluding nested structures)
            main_data = []
            seen_entries = set()
            
            for restaurant in restaurant_data:
                # Create unique key for Excel deduplication
                unique_key = f"{restaurant.vendor_id}_{restaurant.latitude}_{restaurant.longitude}"
                
                # Skip if we've already seen this combination
                if unique_key in seen_entries:
                    continue
                
                # Only include restaurants with allowed cuisines
                if not restaurant.filtered_cuisines:
                    continue
                
                seen_entries.add(unique_key)
                
                main_data.append({
                    'Vendor ID': restaurant.vendor_id,
                    'Name': restaurant.name,
                    'URL': restaurant.url,
                    'Description': restaurant.description,
                    'Cuisine Type': restaurant.cuisine_type,
                    'Filtered Cuisines': ', '.join(restaurant.filtered_cuisines),
                    'Address': restaurant.address,
                    'Latitude': restaurant.latitude,
                    'Longitude': restaurant.longitude,
                    'Telephone': restaurant.telephone,
                    'Price Range': restaurant.price_range,
                    'Rating': restaurant.rating,
                    'Review Count': restaurant.review_count,
                    'Opening Hours': restaurant.opening_hours,
                    'Image URL': restaurant.image_url,
                    'Menu Items Count': len(restaurant.menu_items),
                    'Scrape Timestamp': restaurant.scrape_timestamp
                })
            
            # Create Excel writer with multiple sheets
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                # Main restaurant data
                main_df = pd.DataFrame(main_data)
                main_df.to_excel(writer, sheet_name='Restaurants', index=False)
                
                # Menu items data
                menu_data = []
                for restaurant in restaurant_data:
                    # Only include menu items for restaurants with allowed cuisines
                    if not restaurant.filtered_cuisines:
                        continue
                        
                    for item in restaurant.menu_items:
                        menu_data.append({
                            'Restaurant Name': restaurant.name,
                            'Restaurant URL': restaurant.url,
                            'Vendor ID': restaurant.vendor_id,
                            'Item Name': item.get('name', ''),
                            'Item Description': item.get('description', ''),
                            'Item Price': item.get('price', '')
                        })
                
                if menu_data:
                    menu_df = pd.DataFrame(menu_data)
                    menu_df.to_excel(writer, sheet_name='Menu Items', index=False)
            
            self.logger.info(f"Data saved to {json_path} and {excel_path} ({len(main_data)} unique entries)")
            
        except Exception as e:
            self.logger.error(f"Error saving data: {str(e)}")
    
    def load_restaurant_urls(self) -> List[str]:
        """Load restaurant URLs from JSON-LD data file or fallback to URLs file"""
        # Load previously processed URLs for resume functionality
        self.processed_urls_set = self.load_progress()
        
        # First try to load from JSON-LD data file
        jsonld_path = os.path.join(OUTPUT_DIR, JSONLD_INPUT_FILE)
        if os.path.exists(jsonld_path):
            try:
                with open(jsonld_path, 'r', encoding='utf-8') as f:
                    jsonld_data = json.load(f)
                    
                # Extract URLs from JSON-LD data
                urls = []
                for item in jsonld_data:
                    if isinstance(item, dict) and 'url' in item:
                        urls.append(item['url'])
                
                if urls:
                    # Filter out already processed URLs
                    original_count = len(urls)
                    urls = [url for url in urls if url not in self.processed_urls_set]
                    filtered_count = len(urls)
                    
                    if original_count > filtered_count:
                        self.logger.info(f"Resume functionality: Skipping {original_count - filtered_count} already processed URLs")
                        self.logger.info(f"Remaining URLs to process: {filtered_count}")
                    
                    self.logger.info(f"Loaded {original_count} restaurant URLs from JSON-LD data file ({filtered_count} new)")
                    return urls
                    
            except Exception as e:
                self.logger.warning(f"Error loading JSON-LD data file: {str(e)}")
        
        # Fallback to original URLs file
        urls_path = os.path.join(OUTPUT_DIR, RESTAURANT_URLS_FILE)
        try:
            with open(urls_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # Handle different URL file formats
                if isinstance(data, list):
                    if len(data) > 0 and isinstance(data[0], dict):
                        # New format with metadata
                        urls = [item['url'] for item in data if 'url' in item]
                        self.logger.info(f"Loaded {len(urls)} restaurant URLs (new format)")
                    else:
                        # Old format - simple list of URLs
                        urls = data
                        self.logger.info(f"Loaded {len(urls)} restaurant URLs (old format)")
                else:
                    self.logger.error("Invalid URL file format")
                    return []
                
                # Filter out already processed URLs
                original_count = len(urls)
                urls = [url for url in urls if url not in self.processed_urls_set]
                filtered_count = len(urls)
                
                if original_count > filtered_count:
                    self.logger.info(f"Resume functionality: Skipping {original_count - filtered_count} already processed URLs")
                    self.logger.info(f"Remaining URLs to process: {filtered_count}")
                
                return urls
        except FileNotFoundError:
            self.logger.error(f"Restaurant URLs file not found: {urls_path}")
            return []
        except Exception as e:
            self.logger.error(f"Error loading restaurant URLs: {str(e)}")
            return []
    
    async def scrape_batch_async(self, urls: List[str], process_id: int) -> List[Dict]:
        """Async batch scraping method for multiprocessing worker"""
        restaurant_data = []
        
        # Create optimized connector for this process
        connector = aiohttp.TCPConnector(
            limit=MAX_CONCURRENT_REQUESTS,
            limit_per_host=MAX_CONCURRENT_REQUESTS // 2,
            ttl_dns_cache=300,
            use_dns_cache=True,
            keepalive_timeout=60,
            enable_cleanup_closed=True
        )
        
        timeout = aiohttp.ClientTimeout(
            total=TIMEOUT_SECONDS,
            connect=10,
            sock_read=20
        )
        
        async with aiohttp.ClientSession(
            headers=self.get_headers(),
            connector=connector,
            timeout=timeout
        ) as session:
            
            # Process URLs in smaller batches within this process
            batch_size = PROCESS_BATCH_SIZE
            for i in range(0, len(urls), batch_size):
                batch_urls = urls[i:i + batch_size]
                
                # Create semaphore for this batch
                semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS // 2)
                
                async def scrape_with_semaphore(url: str) -> Optional[RestaurantData]:
                    async with semaphore:
                        return await self.scrape_restaurant_with_validation(session, url)
                
                # Process batch concurrently
                batch_tasks = [scrape_with_semaphore(url) for url in batch_urls]
                batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
                
                # Process results
                for result in batch_results:
                    if isinstance(result, Exception):
                        self.logger.error(f"Process {process_id} exception: {result}")
                    elif result is not None:
                        # Convert RestaurantData to dict for multiprocessing
                        result_dict = {
                            'url': result.url,
                            'vendor_id': result.vendor_id,
                            'name': result.name,
                            'description': result.description,
                            'cuisine_type': result.cuisine_type,
                            'filtered_cuisines': result.filtered_cuisines,
                            'address': result.address,
                            'telephone': result.telephone,
                            'price_range': result.price_range,
                            'rating': result.rating,
                            'review_count': result.review_count,
                            'opening_hours': result.opening_hours,
                            'image_url': result.image_url,
                            'latitude': result.latitude,
                            'longitude': result.longitude,
                            'menu_items': result.menu_items,
                            'json_ld_raw': result.json_ld_raw,
                            'scrape_timestamp': result.scrape_timestamp
                        }
                        restaurant_data.append(result_dict)
                
                # Brief pause between batches
                if i + batch_size < len(urls):
                    await asyncio.sleep(random.uniform(0.5, 1.5))
        
        return restaurant_data

    async def run_scraping_multiprocess(self):
        """Main scraping execution with multiprocessing + async and enhanced progress tracking"""
        self.logger.info("Starting Enhanced HungerStation Scraper with Multiprocessing")
        
        # Load progress data for chunk-based resumption
        progress_data = self.load_progress_data()
        previously_processed = set()  # Will be handled by chunk logic
        
        # Load restaurant URLs (all URLs, not filtered)
        all_urls = self.load_all_restaurant_urls()
        if not all_urls:
            self.logger.error("No URLs to scrape. Please run URL extraction first.")
            return

        # Check for chunk-based resumption
        completed_chunks_from_progress = 0
        total_urls_from_progress = len(all_urls)
        
        if progress_data:
            completed_chunks_from_progress = progress_data.get('completed_chunks', 0)
            total_processed_from_progress = progress_data.get('total_processed', 0)
            total_urls_from_progress = progress_data.get('total_urls', len(all_urls))
            
            if completed_chunks_from_progress > 0:
                self.logger.info(f"🔄 CHUNK-BASED RESUME DETECTED!")
                self.logger.info(f"📊 Previous session: {total_processed_from_progress} URLs processed, {completed_chunks_from_progress} chunks completed")
                self.logger.info(f"✅ Resuming from chunk {completed_chunks_from_progress + 1}")
            else:
                self.logger.info(f"🆕 FRESH START: No previous progress found, starting from beginning")
        else:
            self.logger.info(f"🆕 FRESH START: No previous progress found, starting from beginning")

        # Initialize progress tracking
        self.total_urls = len(all_urls)
        self.processed_urls = 0
        self.remaining_urls = self.total_urls
        self.scraping_start_time = time.time()

        # Test proxy connection and populate proxy info
        self.proxy_info = self.test_proxy_connection()

        # Display startup information
        self.display_startup_info(len(all_urls))
        self.logger.info(f"Using {NUM_PROCESSES} processes for parallel execution")
        self.logger.info(f"🚀 Starting multiprocess scraping of {self.total_urls} restaurants...")

        # Start timing
        self.start_time = time.time()

        # Split URLs into chunks for multiprocessing
        url_chunks = chunk_urls(all_urls, NUM_PROCESSES)
        self.logger.info(f"Split {len(all_urls)} URLs into {len(url_chunks)} chunks")

        # Track progress across processes - resume from completed chunks
        completed_chunks = completed_chunks_from_progress
        total_chunks = len(url_chunks)
        
        # If resuming, update processed count based on completed chunks
        if completed_chunks > 0:
            urls_in_completed_chunks = sum(len(url_chunks[i]) for i in range(min(completed_chunks, len(url_chunks))))
            self.processed_urls = urls_in_completed_chunks
            self.remaining_urls = self.total_urls - self.processed_urls
            self.logger.info(f"📊 Resuming: {self.processed_urls} URLs already processed, {self.remaining_urls} remaining")
        
        # Use ProcessPoolExecutor for multiprocessing
        all_results = []
        
        with ProcessPoolExecutor(max_workers=NUM_PROCESSES) as executor:
            # Submit only remaining chunks (skip completed ones)
            future_to_chunk = {}
            for i in range(completed_chunks, len(url_chunks)):
                chunk = url_chunks[i]
                self.logger.info(f"📦 Submitting chunk {i+1}/{total_chunks} with {len(chunk)} URLs")
                future = executor.submit(scrape_urls_worker, chunk, i)
                future_to_chunk[future] = (i, len(chunk))
            
            # If no chunks to process (all completed)
            if not future_to_chunk:
                self.logger.info("✅ All chunks already completed! No work to do.")
                return
            
            # Collect results as they complete
            for future in as_completed(future_to_chunk):
                chunk_id, chunk_size = future_to_chunk[future]
                try:
                    chunk_results = future.result()
                    all_results.extend(chunk_results)
                    completed_chunks += 1
                    self.processed_urls += chunk_size
                    self.remaining_urls = self.total_urls - self.processed_urls
                    
                    # Calculate progress metrics
                    current_time = time.time()
                    elapsed_time = current_time - self.scraping_start_time
                    
                    if elapsed_time > 0:
                        self.urls_per_minute = (self.processed_urls / elapsed_time) * 60
                        
                        # Calculate ETA
                        if self.urls_per_minute > 0:
                            remaining_minutes = self.remaining_urls / self.urls_per_minute
                            eta_seconds = remaining_minutes * 60
                            
                            # Format ETA
                            if eta_seconds < 60:
                                eta_str = f"{int(eta_seconds)}s"
                            elif eta_seconds < 3600:
                                eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
                            else:
                                hours = int(eta_seconds // 3600)
                                minutes = int((eta_seconds % 3600) // 60)
                                eta_str = f"{hours}h {minutes}m"
                        else:
                            eta_str = "Calculating..."
                    
                    # Enhanced progress logging for multiprocessing
                    progress_percentage = (self.processed_urls / self.total_urls) * 100
                    chunk_progress = (completed_chunks / total_chunks) * 100
                    
                    self.logger.info(
                        f"🔄 Process {chunk_id} completed: {len(chunk_results)} restaurants | "
                        f"📊 Overall Progress: {self.processed_urls}/{self.total_urls} ({progress_percentage:.1f}%) | "
                        f"📦 Chunks: {completed_chunks}/{total_chunks} ({chunk_progress:.1f}%) | "
                        f"⏱️ Speed: {self.urls_per_minute:.1f} URLs/min | "
                        f"🕒 ETA: {eta_str} | "
                        f"⏳ Remaining: {self.remaining_urls} URLs"
                    )
                    
                    # Save checkpoint progress after each chunk completion
                    processed_urls_from_chunk = set(url_chunks[chunk_id])
                    self.processed_urls_set.update(processed_urls_from_chunk)
                    
                    # Save progress checkpoint
                    self.save_progress(self.processed_urls_set, {
                        'completed_chunks': completed_chunks,
                        'total_chunks': total_chunks,
                        'total_urls': self.total_urls,
                        'processed_urls': self.processed_urls
                    })
                    
                except Exception as e:
                    self.logger.error(f"Chunk {chunk_id} failed: {e}")
                    # Still mark URLs as processed to avoid re-processing failed chunks
                    processed_urls_from_chunk = set(url_chunks[chunk_id])
                    self.processed_urls_set.update(processed_urls_from_chunk)
        
        end_time = time.time()
        
        # Convert dict results back to RestaurantData objects
        restaurant_data = []
        for result_dict in all_results:
            restaurant = RestaurantData(
                url=result_dict['url'],
                vendor_id=result_dict['vendor_id'],
                name=result_dict['name'],
                description=result_dict['description'],
                cuisine_type=result_dict['cuisine_type'],
                filtered_cuisines=result_dict['filtered_cuisines'],
                address=result_dict['address'],
                telephone=result_dict['telephone'],
                price_range=result_dict['price_range'],
                rating=result_dict['rating'],
                review_count=result_dict['review_count'],
                opening_hours=result_dict['opening_hours'],
                image_url=result_dict['image_url'],
                latitude=result_dict['latitude'],
                longitude=result_dict['longitude'],
                menu_items=result_dict['menu_items'],
                json_ld_raw=result_dict['json_ld_raw'],
                scrape_timestamp=result_dict['scrape_timestamp']
            )
            restaurant_data.append(restaurant)
        
        # Save results
        if restaurant_data:
            self.logger.info("Saving data...")
            self.save_data(restaurant_data)
        
        # Display final summary
        self.display_final_summary(urls, end_time - self.start_time)
        
        return restaurant_data

    async def run_scraping(self):
        """Main scraping execution - choose between multiprocessing and regular async"""
        urls = self.load_restaurant_urls()
        if not urls:
            self.logger.error("No URLs to scrape. Please run URL extraction first.")
            return
        
        # Use multiprocessing for large datasets, regular async for smaller ones
        if len(urls) > 200:
            self.logger.info(f"Large dataset ({len(urls)} URLs) - using multiprocessing + async")
            return await self.run_scraping_multiprocess()
        else:
            self.logger.info(f"Small dataset ({len(urls)} URLs) - using regular async")
            
            # Test proxy connection and populate proxy info
            self.proxy_info = self.test_proxy_connection()
            
            # Display startup information
            self.display_startup_info(len(urls))
            
            # Start scraping
            self.start_time = time.time()
            restaurant_data = await self.scrape_restaurants_with_progress(urls)
            
            end_time = time.time()
            
            # Save results
            if restaurant_data:
                self.logger.info("Saving data...")
                self.save_data(restaurant_data)
            
            # Display final summary
            self.display_final_summary(urls, end_time - self.start_time)
            
            return restaurant_data

    async def scrape_restaurants_with_progress(self, urls: List[str]):
        """Scrape restaurants with enhanced progress tracking and ETA calculation"""
        restaurant_data = []
        
        # Initialize progress tracking
        self.total_urls = len(urls)
        self.processed_urls = 0
        self.remaining_urls = self.total_urls
        self.scraping_start_time = time.time()
        self.last_progress_update = self.scraping_start_time
        
        self.logger.info(f"🚀 Starting scraping of {self.total_urls} restaurants...")
        
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=MAX_CONCURRENT_REQUESTS * 2),
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        ) as session:
            
            # Process URLs in batches
            batch_size = 50
            for i in range(0, len(urls), batch_size):
                batch = urls[i:i + batch_size]
                
                # Create tasks for this batch
                tasks = [self.scrape_single_restaurant(session, url) for url in batch]
                
                # Execute batch
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Process results
                for j, result in enumerate(batch_results):
                    current_url = batch[j]
                    
                    if isinstance(result, Exception):
                        self.logger.error(f"Exception during scraping: {result}")
                        self.failed_count += 1
                    elif result:
                        restaurant_data.append(result)
                        self.scraped_count += 1
                        # Add to processed URLs set for resume functionality
                        self.processed_urls_set.add(current_url)
                    else:
                        self.failed_count += 1
                        # Still mark as processed even if failed to avoid re-processing
                        self.processed_urls_set.add(current_url)
                    
                    # Update progress tracking
                    self.processed_urls += 1
                    self.remaining_urls = self.total_urls - self.processed_urls
                
                # Save checkpoint progress periodically
                if self.processed_urls - self.last_checkpoint_save >= self.checkpoint_save_interval:
                    self.save_progress(self.processed_urls_set, {
                        'scraped_count': self.scraped_count,
                        'failed_count': self.failed_count,
                        'total_urls': self.total_urls,
                        'processed_urls': self.processed_urls
                    })
                    self.last_checkpoint_save = self.processed_urls
                
                # Calculate and display enhanced progress
                current_time = time.time()
                elapsed_time = current_time - self.scraping_start_time
                
                if elapsed_time > 0:
                    self.urls_per_minute = (self.processed_urls / elapsed_time) * 60
                    
                    # Calculate ETA
                    if self.urls_per_minute > 0:
                        remaining_minutes = self.remaining_urls / self.urls_per_minute
                        eta_seconds = remaining_minutes * 60
                        
                        # Format ETA
                        if eta_seconds < 60:
                            eta_str = f"{int(eta_seconds)}s"
                        elif eta_seconds < 3600:
                            eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
                        else:
                            hours = int(eta_seconds // 3600)
                            minutes = int((eta_seconds % 3600) // 60)
                            eta_str = f"{hours}h {minutes}m"
                        
                        self.estimated_completion_time = current_time + eta_seconds
                    else:
                        eta_str = "Calculating..."
                
                # Enhanced progress logging
                progress_percentage = (self.processed_urls / self.total_urls) * 100
                success_rate = (self.scraped_count / max(self.processed_urls, 1)) * 100
                
                self.logger.info(
                    f"📊 Progress: {self.processed_urls}/{self.total_urls} ({progress_percentage:.1f}%) | "
                    f"✅ Success: {self.scraped_count} ({success_rate:.1f}%) | "
                    f"❌ Failed: {self.failed_count} | "
                    f"⏱️ Speed: {self.urls_per_minute:.1f} URLs/min | "
                    f"🕒 ETA: {eta_str} | "
                    f"⏳ Remaining: {self.remaining_urls} URLs"
                )
                
                # Auto-save periodically
                if self.processed_urls % AUTOSAVE_INTERVAL == 0 and restaurant_data:
                    self.logger.info(f"Auto-saving progress... ({len(restaurant_data)} restaurants)")
                    self.save_data(restaurant_data)
        
        return restaurant_data



    async def scrape_single_restaurant(self, session, url: str):
        """Scrape a single restaurant"""
        try:
            self.logger.info(f"Scraping restaurant: {url}")
            
            # Make request using the existing make_request method
            result = await self.make_request(session, url)
            if not result or result[0] is None:
                self.logger.warning(f"Failed to get content for {url}")
                return None
            
            html_content, status_code = result
            
            # Extract JSON-LD data using existing method
            json_ld = self.extract_json_ld_data(html_content)
            if not json_ld:
                self.logger.warning(f"No JSON-LD data found for {url}")
                return None
            
            # Parse restaurant data using existing method
            restaurant = self.parse_restaurant_data(json_ld, url)
            if not restaurant:
                self.logger.warning(f"Failed to parse restaurant data for {url}")
                return None
            
            # Check if restaurant has any allowed cuisines
            if not restaurant.filtered_cuisines:
                self.logger.info(f"Excluded - no allowed cuisines found for '{restaurant.cuisine_type}': {restaurant.name}")
                return None
            
            self.logger.info(f"Successfully scraped: {restaurant.name}")
            return restaurant
            
        except Exception as e:
            self.logger.error(f"Error scraping {url}: {str(e)}")
            return None

    def display_final_summary(self, urls: List[str], duration: float):
        """Display enhanced final summary with detailed statistics"""
        success_rate = (self.scraped_count / len(urls) * 100) if urls else 0
        
        self.logger.info("=" * 50)
        self.logger.info("🎯 FINAL SCRAPING SUMMARY")
        self.logger.info("=" * 50)
        self.logger.info(f"📋 Session ID: {self.session_id}")
        self.logger.info(f"🔗 Total URLs: {len(urls):,}")
        self.logger.info(f"✅ Successfully Scraped: {self.scraped_count:,}")
        self.logger.info(f"❌ Failed: {self.failed_count:,}")
        self.logger.info(f"📊 Success Rate: {success_rate:.1f}%")
        self.logger.info(f"⏱️ Total Duration: {duration/60:.1f} minutes ({duration:.2f} seconds)")
        self.logger.info(f"🚀 Average Time per Restaurant: {duration/len(urls):.2f}s" if urls else "N/A")
        self.logger.info(f"💾 Data Location: {OUTPUT_DIR}")
        self.logger.info("=" * 50)
        
        # Success message with emoji
        if success_rate > 90:
            self.logger.info("🎉 Scraping completed successfully with excellent results!")
        elif success_rate > 80:
            self.logger.info("✅ Scraping completed successfully!")
        elif success_rate > 50:
            self.logger.warning("⚠️ Scraping completed with some issues")
        else:
            self.logger.error("❌ Scraping completed with many failures")

async def main():
    """Main function"""
    scraper = EnhancedHungerStationScraper()
    await scraper.run_scraping()

if __name__ == "__main__":
    asyncio.run(main())
