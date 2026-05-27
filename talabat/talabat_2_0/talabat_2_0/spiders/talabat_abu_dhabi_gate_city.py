import scrapy
import json
import os
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import logging


class TalabatAbuDhabiGateCitySpider(scrapy.Spider):
    name = "talabat_abu_dhabi_international_airport"
    allowed_domains = ["www.talabat.com"]
    
    def __init__(self):
        self.start_urls = ["https://www.talabat.com/uae/restaurants/6815/abu-dhabi-international-airport"]
        self.page_count = 0
        self.max_pages = 218  # Based on your requirement
        self.area_name = "abu-dhabi-international-airport"
        self.output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), self.area_name)
        self.all_restaurant_urls = []  # Collect ALL URLs from all pages (with duplicates)
        self.restaurant_urls = set()  # Final unique URLs (populated at the end)
        self.failed_pages = []  # Track failed pages for retry
        self.auto_save_interval = 10  # Save every 10 pages
        self.auto_skip_errors = True  # Skip errors and continue
        self.ensure_output_directory()
        self.setup_detailed_logging()

    def ensure_output_directory(self):
        """Ensure the output directory exists"""
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            self.logger.info(f"Created output directory: {self.output_dir}")

    def setup_detailed_logging(self):
        """Setup detailed logging to a text file for debugging"""
        log_file = os.path.join(self.output_dir, f'phase1_scraping_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt')
        
        # Get the root logger and add file handler
        root_logger = logging.getLogger()
        
        # Create file handler for detailed logging
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        
        # Add handler to root logger
        root_logger.addHandler(file_handler)
        root_logger.setLevel(logging.DEBUG)
        
        self.log_file_path = log_file
        self.logger.info(f"📝 Detailed logging enabled. Log file: {log_file}")
        self.logger.info(f"🚀 Starting Phase 1: URL Collection for {self.area_name}")
        self.logger.info(f"🎯 Target: {self.max_pages} pages")
        self.logger.info(f"💾 Auto-save interval: Every {self.auto_save_interval} pages")
        self.logger.info(f"🔄 Auto-skip errors: {self.auto_skip_errors}")

    def finalize_url_collection(self):
        """Perform final deduplication and save unique URLs"""
        self.logger.info("🔄 Performing final deduplication...")
        
        # Convert to set to remove duplicates, then back to list for JSON serialization
        unique_urls = list(set(self.all_restaurant_urls))
        self.restaurant_urls = set(unique_urls)  # Update the final set
        
        duplicates_removed = len(self.all_restaurant_urls) - len(unique_urls)
        
        self.logger.info(f"📊 Final Statistics:")
        self.logger.info(f"   Total URLs collected: {len(self.all_restaurant_urls)}")
        self.logger.info(f"   Unique URLs: {len(unique_urls)}")
        self.logger.info(f"   Duplicates removed: {duplicates_removed}")
        
        # Save the final unique URLs
        self.save_restaurant_urls(unique_urls)
        
    def save_restaurant_urls(self, urls_list):
        """Save the final unique restaurant URLs to JSON file"""
        try:
            # Prepare data structure
            url_data = {
                'area': self.area_name,
                'total_pages_scraped': self.page_count,
                'total_urls_found': len(self.all_restaurant_urls),
                'unique_urls_count': len(urls_list),
                'duplicates_removed': len(self.all_restaurant_urls) - len(urls_list),
                'collected_at': datetime.now().isoformat(),
                'urls': urls_list,
                'failed_pages': self.failed_pages
            }
            
            # Save to JSON file
            output_file = os.path.join(self.output_dir, 'restaurant_urls.json')
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(url_data, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"💾 Saved {len(urls_list)} unique restaurant URLs to: {output_file}")
            
            # Also create a summary file
            summary_file = os.path.join(self.output_dir, 'url_collection_summary.json')
            summary_data = {
                'area_name': self.area_name,
                'scraping_completed_at': datetime.now().isoformat(),
                'pages_scraped': self.page_count,
                'max_pages_limit': self.max_pages,
                'total_urls_found': len(self.all_restaurant_urls),
                'unique_urls_saved': len(urls_list),
                'duplicates_removed': len(self.all_restaurant_urls) - len(urls_list),
                'failed_pages_count': len(self.failed_pages),
                'success_rate': f"{((self.page_count - len(self.failed_pages)) / self.page_count * 100):.1f}%" if self.page_count > 0 else "0%"
            }
            
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary_data, f, indent=2, ensure_ascii=False)
                
            self.logger.info(f"📋 Summary saved to: {summary_file}")
            
        except Exception as e:
            self.logger.error(f"❌ Error saving URLs: {str(e)}")

    def auto_save_progress(self):
        """Auto-save progress at regular intervals (now saves all URLs collected so far)"""
        try:
            temp_file = os.path.join(self.output_dir, f'temp_restaurant_urls_page_{self.page_count}.json')
            
            # Save current progress including duplicates
            progress_data = {
                'area': self.area_name,
                'current_page': self.page_count,
                'total_urls_so_far': len(self.all_restaurant_urls),
                'collected_at': datetime.now().isoformat(),
                'urls': self.all_restaurant_urls,  # Save all URLs including duplicates
                'failed_pages': self.failed_pages
            }
            
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(progress_data, f, indent=2, ensure_ascii=False)
                
            self.logger.info(f"💾 Auto-saved progress to: {temp_file}")
            
        except Exception as e:
            self.logger.error(f"❌ Error auto-saving progress: {str(e)}")

    def start_requests(self):
        """Start scraping from the main listing page"""
        for url in self.start_urls:
            yield scrapy.Request(
                url, 
                callback=self.parse,
                headers=self.get_stealth_headers()
            )

    def get_stealth_headers(self, referer=None):
        """Get comprehensive stealth headers to avoid detection"""
        headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Cache-Control': 'max-age=0',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin' if referer else 'none',
            'Sec-Fetch-User': '?1',
            'sec-ch-ua': '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
        if referer:
            headers['Referer'] = referer
        return headers

    def parse(self, response):
        """Parse restaurant listing pages and extract restaurant URLs only (Phase 1)"""
        try:
            # Get page number from meta or URL
            page_number = response.meta.get('page_number', 1)
            self.logger.info(f"🔍 Parsing listing page {page_number}: {response.url}")
            self.logger.debug(f"Response status: {response.status}")
            
            # Extract restaurant URLs from the current page
            restaurant_links = response.css('a[href*="/restaurant/"]::attr(href)').getall()
            
            # Alternative selectors if the first one doesn't work
            if not restaurant_links:
                self.logger.warning("Primary selector failed, trying alternative selector")
                restaurant_links = response.xpath('//a[contains(@href, "/restaurant/")]/@href').getall()
            
            if not restaurant_links:
                self.logger.error(f"❌ No restaurant links found on page {page_number}")
                self.failed_pages.append({
                    'page': page_number,
                    'url': response.url,
                    'reason': 'No restaurant links found',
                    'timestamp': datetime.now().isoformat()
                })
                if not self.auto_skip_errors:
                    raise Exception("No restaurant links found")
            
            # Process and collect ALL URLs from this page (no duplicate filtering yet)
            page_urls = []
            for link in restaurant_links:
                if link.startswith('/'):
                    link = response.urljoin(link)
                
                # Extract restaurant ID for validation
                restaurant_id = self.extract_restaurant_id(link)
                if restaurant_id:
                    page_urls.append(link)
                    self.all_restaurant_urls.append(link)  # Add ALL URLs (including duplicates)
                    self.logger.debug(f"Collected restaurant URL: {link} (ID: {restaurant_id})")
            
            self.logger.info(f"📊 Found {len(page_urls)} restaurant URLs on page {page_number}")
            self.logger.info(f"📈 Total URLs collected so far: {len(self.all_restaurant_urls)} (including duplicates)")
            
            # If we found no restaurants at all on this page, we might have reached the end
            if len(page_urls) == 0:
                self.logger.warning(f"⚠️ No restaurants found on page {page_number} - might have reached the end")
                # Perform final deduplication and save before stopping
                self.finalize_url_collection()
                return
            
            # Continue to all pages regardless of whether we found restaurants or not
            # This ensures we scrape all 218 pages as requested
            
            # Handle pagination
            self.page_count += 1
            
            # Auto-save progress at regular intervals
            if self.page_count % self.auto_save_interval == 0:
                self.auto_save_progress()
            
            if self.page_count < self.max_pages:
                # Build next page URL directly
                next_page = self.page_count + 1
                base_url = response.url.split('?')[0]  # Remove existing query params
                next_url = f"{base_url}?page={next_page}"
                
                self.logger.info(f"➡️ Following to next page: {next_page} of {self.max_pages}")
                self.logger.debug(f"Next URL: {next_url}")
                yield scrapy.Request(
                    next_url, 
                    callback=self.parse,
                    headers=self.get_stealth_headers(response.url),
                    errback=self.handle_error,
                    dont_filter=True,  # Allow duplicate URLs for pagination
                    meta={'page_number': next_page}  # Track page number
                )
            else:
                self.logger.info(f"✅ Maximum page limit reached. Collected {len(self.all_restaurant_urls)} total URLs")
                # Perform final deduplication and save
                self.finalize_url_collection()
                
        except Exception as e:
            page_number = response.meta.get('page_number', self.page_count + 1)
            self.logger.error(f"❌ Error parsing page {page_number}: {str(e)}")
            self.failed_pages.append({
                'page': page_number,
                'url': response.url,
                'reason': str(e),
                'timestamp': datetime.now().isoformat()
            })
            if not self.auto_skip_errors:
                raise

    def handle_error(self, failure):
        """Handle request errors"""
        self.logger.error(f"❌ Request failed: {failure.request.url}")
        self.logger.error(f"Error: {failure.value}")
        
        self.failed_pages.append({
            'page': self.page_count + 1,
            'url': failure.request.url,
            'reason': str(failure.value),
            'timestamp': datetime.now().isoformat()
        })
        
        if self.auto_skip_errors:
            self.logger.info("🔄 Auto-skipping error and continuing...")
        else:
            raise failure.value

    def extract_restaurant_id(self, url):
        """Extract restaurant ID from URL"""
        import re
        match = re.search(r'/restaurant/(\d+)', url)
        if match:
            return match.group(1)
        return None

    def closed(self, reason):
        """Save collected URLs for Phase 2 processing"""
        try:
            # Convert set to list for JSON serialization
            urls_list = list(self.restaurant_urls)
            
            # Save URLs to JSON file for Phase 2
            urls_file = os.path.join(self.output_dir, 'restaurant_urls.json')
            urls_data = {
                'area': self.area_name,
                'total_urls': len(urls_list),
                'collected_at': datetime.now().isoformat(),
                'urls': urls_list
            }
            
            with open(urls_file, 'w', encoding='utf-8') as f:
                json.dump(urls_data, f, ensure_ascii=False, indent=2)
            
            # Save detailed summary with failed pages info
            summary_file = os.path.join(self.output_dir, 'url_collection_summary.json')
            summary = {
                'phase': 'Phase 1 - URL Collection',
                'timestamp': datetime.now().isoformat(),
                'total_urls_collected': len(urls_list),
                'pages_scraped': self.page_count,
                'pages_failed': len(self.failed_pages),
                'failed_pages': self.failed_pages,
                'area': self.area_name,
                'reason': reason,
                'success_rate': f"{((self.page_count - len(self.failed_pages)) / max(self.page_count, 1)) * 100:.2f}%",
                'auto_save_interval': self.auto_save_interval,
                'auto_skip_errors': self.auto_skip_errors,
                'log_file': getattr(self, 'log_file_path', 'Not available'),
                'next_step': 'Run talabat_complete_spider.py for Phase 2 detailed scraping'
            }
            
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            
            # Save failed pages separately for easy debugging
            if self.failed_pages:
                failed_file = os.path.join(self.output_dir, 'failed_pages.json')
                with open(failed_file, 'w', encoding='utf-8') as f:
                    json.dump(self.failed_pages, f, ensure_ascii=False, indent=2)
                self.logger.warning(f"⚠️ {len(self.failed_pages)} pages failed. Details saved to: {failed_file}")
            
            # Final auto-save
            self.auto_save_progress()
                
            self.logger.info(f"🎉 Phase 1 Complete! Collected {len(urls_list)} restaurant URLs")
            self.logger.info(f"📁 URLs saved to: {urls_file}")
            self.logger.info(f"📊 Summary saved to: {summary_file}")
            self.logger.info(f"📝 Detailed log saved to: {getattr(self, 'log_file_path', 'Not available')}")
            self.logger.info(f"✅ Success rate: {((self.page_count - len(self.failed_pages)) / max(self.page_count, 1)) * 100:.2f}%")
            self.logger.info(f"🚀 Next: Run 'talabat_complete_spider.py' for Phase 2 detailed scraping")
            
        except Exception as e:
            self.logger.error(f"❌ Error saving URLs: {str(e)}")
            if not self.auto_skip_errors:
                raise