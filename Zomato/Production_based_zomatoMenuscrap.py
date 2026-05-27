import os
import json
import re
import time
import random
import csv
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional
import pickle


try:
    from curl_cffi import requests
    USING_CURL_CFFI = True
    print("[STEALTH] Using curl-cffi for stealth mode")
except ImportError:
    import requests
    USING_CURL_CFFI = False
    print("[WARNING] Using standard requests - install curl-cffi for better stealth")

try:
    from fake_useragent import UserAgent
    ua = UserAgent()
    print("[STEALTH] Using fake-useragent for random user agents")
except ImportError:
    ua = None
    print("[WARNING] Install fake-useragent for better stealth")

from bs4 import BeautifulSoup

@dataclass
class ScrapingResult:
    """Data class to track scraping results"""
    url: str
    restaurant_id: str
    status: str
    images_found: int
    images_downloaded: int
    error_message: str = ""
    timestamp: str = ""

class StealthZomatoScraper:
    def __init__(self, 
                 output_dir='zomato_ONLY_DUBAI_menu_images_batch_24_08',
                 batch_size=10,
                 max_workers=2,  # Reduced for stealth
                 base_delay=3,   # Increased delay
                 random_delay=True,
                 progress_file='scraping_progress_dubai.json',
                 failed_urls_file='failed_urls_dubai.csv',
                 results_file='scraping_results_dubai.csv'):
        
        self.output_dir = output_dir
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.base_delay = base_delay
        self.random_delay = random_delay
        self.progress_file = progress_file
        self.failed_urls_file = failed_urls_file
        self.results_file = results_file
        
        # Create session with stealth settings
        self.session = requests.Session() if USING_CURL_CFFI else requests.Session()
        
        # Initialize progress tracking
        self.processed_urls = set()
        self.failed_urls = []
        self.results = []
        
        # Create directories
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Setup logging FIRST - BEFORE loading progress
        self._setup_logging()
        
        # Initialize stealth settings
        self._setup_stealth_mode()
        
        # Load existing progress AFTER logger is set up
        self._load_progress()
        
        self.logger.info(f"[INIT] Initialized scraper - Batch size: {batch_size}, Workers: {max_workers}")
        self.logger.info(f"[INIT] Output dir: {output_dir}")
        self.logger.info(f"[INIT] Progress tracking: {progress_file}")

    def _setup_logging(self):
        """Setup enhanced logging with file output - Windows compatible"""
        
        # Create logs directory
        os.makedirs('logs', exist_ok=True)
        
        # Configure logging with both file and console output
        log_filename = f"logs/zomato_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
        # Create formatter without emojis for Windows compatibility
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        
        # File handler with UTF-8 encoding
        file_handler = logging.FileHandler(log_filename, encoding='utf-8')
        file_handler.setFormatter(formatter)
        
        # Console handler with error handling for encoding
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        
        # Create logger instance for this class
        self.logger = logging.getLogger(__name__ + str(id(self)))
        self.logger.setLevel(logging.INFO)
        
        # Clear any existing handlers
        self.logger.handlers.clear()
        
        # Add handlers
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        
        # Prevent propagation to root logger
        self.logger.propagate = False

    def _setup_stealth_mode(self):
        """Configure stealth mode settings"""
        
        # Rotating user agents
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        ]
        
        # Set initial headers
        self.session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        })

    def _get_random_user_agent(self):
        """Get random user agent for stealth"""
        if ua:
            try:
                return ua.random
            except:
                pass
        return random.choice(self.user_agents)

    def _get_delay(self):
        """Calculate random delay for stealth"""
        if self.random_delay:
            return self.base_delay + random.uniform(2, 6)
        return self.base_delay

    def _load_progress(self):
        """Load existing progress from file"""
        try:
            if os.path.exists(self.progress_file):
                with open(self.progress_file, 'r') as f:
                    progress_data = json.load(f)
                    self.processed_urls = set(progress_data.get('processed_urls', []))
                    self.failed_urls = progress_data.get('failed_urls', [])
                    self.logger.info(f"[PROGRESS] Loaded progress: {len(self.processed_urls)} processed URLs")
        except Exception as e:
            self.logger.warning(f"Could not load progress file: {e}")

    def _save_progress(self):
        """Save current progress to file"""
        try:
            progress_data = {
                'processed_urls': list(self.processed_urls),
                'failed_urls': self.failed_urls,
                'last_updated': datetime.now().isoformat()
            }
            with open(self.progress_file, 'w') as f:
                json.dump(progress_data, f, indent=2)
        except Exception as e:
            self.logger.error(f"Could not save progress: {e}")

    def _save_failed_urls(self):
        """Save failed URLs to CSV"""
        try:
            if self.failed_urls:
                df = pd.DataFrame(self.failed_urls)
                df.to_csv(self.failed_urls_file, index=False)
                self.logger.info(f"[SAVE] Saved {len(self.failed_urls)} failed URLs to {self.failed_urls_file}")
        except Exception as e:
            self.logger.error(f"Could not save failed URLs: {e}")

    def _save_results(self):
        """Save scraping results to CSV"""
        try:
            if self.results:
                df = pd.DataFrame([vars(result) for result in self.results])
                df.to_csv(self.results_file, index=False)
                self.logger.info(f"[SAVE] Saved {len(self.results)} results to {self.results_file}")
        except Exception as e:
            self.logger.error(f"Could not save results: {e}")

    def extract_restaurant_id(self, url):
        """Extract restaurant ID from Zomato URL"""
        try:
            match = re.search(r'/([^/]+)/menu$', url)
            if match:
                return match.group(1)
            return f"restaurant_{hash(url) % 100000}"
        except Exception as e:
            self.logger.error(f"Error extracting restaurant ID from {url}: {e}")
            return f"unknown_{int(time.time())}"

    def extract_restaurant_metadata(self, json_data, url):
        """Extract restaurant metadata from JSON data"""
        try:
            metadata = {
                "restaurant_url": url,
                "menu_url": url,
                "scraping_timestamp": datetime.now().isoformat(),
                "restaurant_info": {}
            }
            
            # Navigate through JSON to extract restaurant info
            restaurants = json_data.get('pages', {}).get('restaurant', {})
            
            for restaurant_id, restaurant_data in restaurants.items():
                sections = restaurant_data.get('sections', {})
                
                # Extract basic info
                basic_info = sections.get('SECTION_BASIC_INFO', {})
                if basic_info:
                    metadata["restaurant_info"] = {
                        "name": basic_info.get('name', ''),
                        "cuisine": basic_info.get('cuisine_string', ''),
                        "rating": basic_info.get('rating', {}).get('aggregate_rating', ''),
                        "rating_text": basic_info.get('rating', {}).get('rating_text', ''),
                        "votes": basic_info.get('rating', {}).get('votes', 0),
                        "status": basic_info.get('res_status_text', ''),
                        "timing": basic_info.get('timing', {}).get('timing_desc', ''),
                        "restaurant_id": restaurant_id
                    }
                
                # Extract contact info
                contact_info = sections.get('SECTION_RES_CONTACT', {})
                if contact_info:
                    metadata["restaurant_info"].update({
                        "address": contact_info.get('address', ''),
                        "locality": contact_info.get('locality_verbose', ''),
                        "city": contact_info.get('city_name', ''),
                        "phone": contact_info.get('phoneDetails', {}).get('phoneStr', ''),
                        "latitude": contact_info.get('latitude', ''),
                        "longitude": contact_info.get('longitude', '')
                    })
                
                break  # Process first restaurant only
            
            return metadata
            
        except Exception as e:
            self.logger.error(f"Error extracting restaurant metadata: {e}")
            return {
                "restaurant_url": url,
                "menu_url": url,
                "scraping_timestamp": datetime.now().isoformat(),
                "restaurant_info": {},
                "error": str(e)
            }

    def save_restaurant_metadata(self, metadata, restaurant_dir):
        """Save restaurant metadata to JSON file"""
        try:
            metadata_file = os.path.join(restaurant_dir, 'restaurant_info.json')
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            self.logger.info(f"[METADATA] Saved restaurant metadata to {metadata_file}")
            return True
        except Exception as e:
            self.logger.error(f"Error saving restaurant metadata: {e}")
            return False

    def fetch_page_content(self, url):
        """Fetch page content with enhanced stealth"""
        try:
            # Rotate user agent
            self.session.headers.update({
                'User-Agent': self._get_random_user_agent()
            })
            
            # Add referer for more realistic requests
            self.session.headers.update({
                'Referer': 'https://www.zomato.com/'
            })
            
            # Make request with timeout
            response = self.session.get(url, timeout=45)
            response.raise_for_status()
            
            # Check for blocking indicators
            if 'blocked' in response.text.lower() or len(response.text) < 1000:
                self.logger.warning(f"Possible blocking detected for {url}")
                return None
            
            return response.text
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching {url}: {e}")
            return None

    def extract_json_from_html(self, html_content):
        """Extract JSON data from HTML page"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            script_tag = None
            for script in soup.find_all('script'):
                if script.string and 'window.__PRELOADED_STATE__' in script.string:
                    script_tag = script.string
                    break
            
            if not script_tag:
                self.logger.error("JSON script data not found in the page")
                return None
            
            json_pattern = r'window\.__PRELOADED_STATE__ = JSON\.parse\("(.+)"\);'
            match = re.search(json_pattern, script_tag, re.DOTALL)
            
            if not match:
                self.logger.error("JSON data pattern not matched")
                return None
            
            json_str_escaped = match.group(1)
            json_str = json_str_escaped.encode('utf-8').decode('unicode_escape')
            data = json.loads(json_str)
            return data
            
        except Exception as e:
            self.logger.error(f"Error extracting JSON from HTML: {e}")
            return None

    def extract_image_urls_from_json(self, json_data):
        """Extract image URLs from JSON data"""
        image_urls = []
        
        try:
            restaurants = json_data.get('pages', {}).get('restaurant', {})
            
            for restaurant_id, restaurant_data in restaurants.items():
                sections = restaurant_data.get('sections', {})
                image_menu = sections.get('SECTION_IMAGE_MENU', {})
                menu_items = image_menu.get('menuItems', [])
                
                for menu_item in menu_items:
                    pages = menu_item.get('pages', [])
                    for page in pages:
                        url = page.get('url', '')
                        if url:
                            clean_url = url.split('?')[0]
                            if clean_url not in image_urls:
                                image_urls.append(clean_url)
            
            return image_urls
            
        except Exception as e:
            self.logger.error(f"Error extracting image URLs: {e}")
            return []

    def download_image(self, img_url, img_name):
        """Download single image with stealth"""
        try:
            # Random delay before download
            time.sleep(random.uniform(0.5, 2))
            
            response = self.session.get(img_url, timeout=30)
            response.raise_for_status()
            
            with open(img_name, 'wb') as f:
                f.write(response.content)
            
            return True
            
        except Exception as e:
            self.logger.debug(f"Failed to download {img_url}: {e}")
            return False

    def download_images(self, image_urls, restaurant_name="restaurant"):
        """Download all images with threading"""
        restaurant_dir = os.path.join(self.output_dir, restaurant_name)
        os.makedirs(restaurant_dir, exist_ok=True)
        
        download_tasks = []
        for i, img_url in enumerate(image_urls, 1):
            img_name = os.path.join(restaurant_dir, f'menu_img_{i:03d}.jpg')
            download_tasks.append((img_url, img_name))
        
        successful_downloads = 0
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_url = {
                executor.submit(self.download_image, img_url, img_name): (img_url, img_name)
                for img_url, img_name in download_tasks
            }
            
            for future in as_completed(future_to_url):
                try:
                    if future.result():
                        successful_downloads += 1
                except Exception as e:
                    self.logger.debug(f"Download thread error: {e}")
        
        return successful_downloads

    def scrape_restaurant(self, url):
        """Main method to scrape a single restaurant"""
        result = ScrapingResult(
            url=url,
            restaurant_id="",
            status="failed",
            images_found=0,
            images_downloaded=0,
            timestamp=datetime.now().isoformat()
        )
        
        try:
            # Check if already processed
            if url in self.processed_urls:
                self.logger.info(f"[SKIP] Skipping already processed: {url}")
                return result
            
            self.logger.info(f"[TARGET] Starting scrape for: {url}")
            
            # Extract restaurant identifier
            restaurant_id = self.extract_restaurant_id(url)
            result.restaurant_id = restaurant_id
            
            # Fetch page content
            html_content = self.fetch_page_content(url)
            if not html_content:
                result.error_message = "Failed to fetch page content"
                return result
            
            # Extract JSON data
            json_data = self.extract_json_from_html(html_content)
            if not json_data:
                result.error_message = "Failed to extract JSON data"
                return result
            
            # Extract restaurant metadata
            metadata = self.extract_restaurant_metadata(json_data, url)
            
            # Extract image URLs
            image_urls = self.extract_image_urls_from_json(json_data)
            result.images_found = len(image_urls)
            
            if not image_urls:
                result.error_message = "No image URLs found"
                self.logger.warning(f"[WARNING] No images found for {url}")
                return result
            
            self.logger.info(f"[IMAGES] Found {len(image_urls)} images for {restaurant_id}")
            
            # Create restaurant directory
            restaurant_dir = os.path.join(self.output_dir, restaurant_id)
            os.makedirs(restaurant_dir, exist_ok=True)
            
            # Save restaurant metadata FIRST
            metadata_saved = self.save_restaurant_metadata(metadata, restaurant_dir)
            
            # Download images
            downloaded_count = self.download_images(image_urls, restaurant_id)
            result.images_downloaded = downloaded_count
            result.status = "success"
            
            self.logger.info(f"[SUCCESS] Downloaded {downloaded_count}/{len(image_urls)} images for {restaurant_id}")
            if metadata_saved:
                self.logger.info(f"[METADATA] Restaurant info saved for {restaurant_id}")
            
            # Mark as processed
            self.processed_urls.add(url)
            
            return result
            
        except Exception as e:
            result.error_message = str(e)
            self.logger.error(f"[ERROR] Error scraping {url}: {e}")
            return result

    def process_batch(self, urls_batch, batch_num):
        """Process a batch of URLs"""
        self.logger.info(f"[BATCH] Processing batch {batch_num} ({len(urls_batch)} URLs)")
        batch_results = []
        
        for i, url in enumerate(urls_batch, 1):
            self.logger.info(f"[BATCH] Batch {batch_num} - Processing {i}/{len(urls_batch)}: {url}")
            
            result = self.scrape_restaurant(url)
            batch_results.append(result)
            
            # Track failures
            if result.status == "failed":
                self.failed_urls.append({
                    'url': url,
                    'restaurant_id': result.restaurant_id,
                    'error': result.error_message,
                    'timestamp': result.timestamp
                })
            
            # Add stealth delay between requests
            delay = self._get_delay()
            self.logger.info(f"[DELAY] Waiting {delay:.1f}s before next request...")
            time.sleep(delay)
            
            # Auto-save progress every 5 URLs
            if i % 5 == 0:
                self._save_progress()
                self._save_failed_urls()
                self._save_results()
        
        # Save progress after batch
        self._save_progress()
        self._save_failed_urls()
        
        return batch_results

    def run_batch_scraping(self, urls_list):
        """Run the complete batch scraping process"""
        total_urls = len(urls_list)
        self.logger.info(f"[START] Starting batch scraping: {total_urls} total URLs")
        self.logger.info(f"[CONFIG] Batch size: {self.batch_size}")
        
        # Filter out already processed URLs
        remaining_urls = [url for url in urls_list if url not in self.processed_urls]
        self.logger.info(f"[PROGRESS] Remaining URLs to process: {len(remaining_urls)}")
        
        # Process in batches
        total_batches = (len(remaining_urls) + self.batch_size - 1) // self.batch_size
        
        for batch_num in range(1, total_batches + 1):
            start_idx = (batch_num - 1) * self.batch_size
            end_idx = min(start_idx + self.batch_size, len(remaining_urls))
            batch_urls = remaining_urls[start_idx:end_idx]
            
            self.logger.info(f"[BATCH] Starting batch {batch_num}/{total_batches}")
            
            batch_results = self.process_batch(batch_urls, batch_num)
            self.results.extend(batch_results)
            
            # Final save after batch
            self._save_results()
            
            # Summary for batch
            successful = sum(1 for r in batch_results if r.status == "success")
            failed = len(batch_results) - successful
            total_images = sum(r.images_downloaded for r in batch_results)
            
            self.logger.info(f"[SUMMARY] Batch {batch_num} complete: {successful} success, {failed} failed, {total_images} images downloaded")
            
            # Longer delay between batches
            if batch_num < total_batches:
                batch_delay = random.uniform(15, 30)  # 15-30 second delay
                self.logger.info(f"[REST] Resting {batch_delay:.1f}s between batches...")
                time.sleep(batch_delay)
        
        # Final summary
        self._print_final_summary()

    def _print_final_summary(self):
        """Print final scraping summary"""
        successful = sum(1 for r in self.results if r.status == "success")
        failed = len(self.results) - successful
        total_images = sum(r.images_downloaded for r in self.results)
        
        self.logger.info("[COMPLETE] SCRAPING COMPLETE!")
        self.logger.info(f"[FINAL] Final Summary:")
        self.logger.info(f"   [SUCCESS] Successful: {successful}")
        self.logger.info(f"   [FAILED] Failed: {failed}")
        self.logger.info(f"   [IMAGES] Total Images: {total_images}")
        self.logger.info(f"   [OUTPUT] Output Directory: {self.output_dir}")
        self.logger.info(f"   [RESULTS] Results saved to: {self.results_file}")
        self.logger.info(f"   [FAILURES] Failed URLs saved to: {self.failed_urls_file}")

def main():
    """Main function to run batch scraping"""
    
    # Your URLs directly pasted here

    zomato_urls = [
    "https://www.zomato.com/dubai/zordaar-restaurant-al-barsha/menu",
    "https://www.zomato.com/dubai/streat-culture-doubletree-by-hilton-dubai-al-jaddaf/menu",
    "https://www.zomato.com/dubai/swiss-butter-novotel-al-barsha-al-barsha/menu",
    "https://www.zomato.com/dubai/sri-krishna-bhavan-1-al-barsha/menu",
    "https://www.zomato.com/dubai/saffronlane-meena-bazaar/menu",
    "https://www.zomato.com/dubai/vb-world-mankhool/menu",
    "https://www.zomato.com/dubai/ikigai-1-dubai-marina/menu",  # Added missing comma
    "https://www.zomato.com/dubai/public-downtown-dubai/menu",  # Fixed: now separate URL
    "https://www.zomato.com/dubai/aminia-restaurant-impz/menu",
    "https://www.zomato.com/dubai/royal-orchid-jebel-ali-village/menu",
    "https://www.zomato.com/dubai/calicut-stories-restaurant-qusais/menu",
    "https://www.zomato.com/dubai/the-desi-firangi-al-hudaiba-and-around/menu",
    "https://www.zomato.com/dubai/dhaba-lane-al-karama/menu",
    "https://www.zomato.com/dubai/firangi-by-koyla-al-nahda/menu",
    "https://www.zomato.com/dubai/stories-lounge-bar-cafe-meena-bazaar/menu",
    "https://www.zomato.com/dubai/b60-burgers-oud-metha/menu"
     ]

    
    print(f"[READY] Ready to scrape {len(zomato_urls)} restaurant URLs")
    
    # Initialize enhanced scraper with optimized settings
    scraper = StealthZomatoScraper(
        output_dir='zomato_ONLY_DUBAI_menu_images_batch',
        batch_size=5,       # Small batches for maximum stealth
        max_workers=2,      # Conservative threading
        base_delay=4,       # 4+ second delays
        random_delay=True,  # Random delays for stealth
    )
    
    # Run batch scraping
    scraper.run_batch_scraping(zomato_urls)

if __name__ == "__main__":
    main()
