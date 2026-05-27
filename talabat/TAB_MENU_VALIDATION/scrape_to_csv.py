import json
import time
import random
import os
import csv
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from colorama import init, Fore
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

init(autoreset=True)

# Proxy configuration. Set these in your local environment before running.
PROXY_USERNAME = os.getenv("OXYLABS_USERNAME", "")
PROXY_PASSWORD = os.getenv("OXYLABS_PASSWORD", "")
PROXY_COUNTRY = os.getenv("OXYLABS_COUNTRY", "ae")

# Scraping settings
CONCURRENT_WORKERS = 2
MIN_DELAY_BETWEEN_REQUESTS = 3
MAX_DELAY_BETWEEN_REQUESTS = 6
RETRY_ATTEMPTS = 3
SESSION_REFRESH_INTERVAL = 50

# Thread-safe storage
output_lock = threading.Lock()
all_menu_items = []
failed_urls = []

def create_proxy_session():
    """Create requests session with proper proxy configuration"""
    if not PROXY_USERNAME or not PROXY_PASSWORD:
        raise RuntimeError("Set OXYLABS_USERNAME and OXYLABS_PASSWORD before running this script.")

    session_id = random.randint(10000, 99999)
    proxy_user = f"customer-{PROXY_USERNAME}-cc-{PROXY_COUNTRY}-sessid-{session_id}"
    proxy_url = f"http://{proxy_user}:{PROXY_PASSWORD}@pr.oxylabs.io:7777"
    
    session = requests.Session()
    session.proxies = {
        'http': proxy_url,
        'https': proxy_url
    }
    
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    })
    
    return session

def extract_menu_data(session, url, worker_id):
    """Extract restaurant data using requests"""
    try:
        print(f"{Fore.YELLOW}🔧 Worker {worker_id}: Processing {url}")
        
        clean_url = url.split('#')[0].strip()
        response = session.get(clean_url, timeout=20)
        response.raise_for_status()
        
        if 'rate limit' in response.text.lower() or response.status_code == 429:
            print(f"{Fore.RED}⚠️ Worker {worker_id}: Rate limiting detected")
            return "RATE_LIMITED"
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract restaurant name
        restaurant_name = "Unknown"
        name_selectors = ['a[data-testid="branch-name"]', 'h1', 'h2']
        for selector in name_selectors:
            element = soup.select_one(selector)
            if element and element.text.strip():
                restaurant_name = element.text.strip()
                break
        
        # Extract menu from __NEXT_DATA__
        menu_items = []
        script_tag = soup.find('script', {'id': '__NEXT_DATA__'})
        if script_tag:
            try:
                next_data = json.loads(script_tag.string)
                page_props = next_data.get('props', {}).get('pageProps', {})
                menu_state = page_props.get('initialMenuState', {})
                menu_data = menu_state.get('menuData', {})
                items = menu_data.get("items", [])
                
                for item in items:
                    if item and isinstance(item, dict):
                        menu_items.append({
                            "url": url,
                            "restaurant_name": restaurant_name,
                            "category": item.get("originalSection", "Unknown"),
                            "item_name": item.get("name", "Unknown"),
                            "description": item.get("description", ""),
                            "price": item.get('price', 'N/A'),
                            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        })
                
                print(f"{Fore.GREEN}✅ Worker {worker_id}: Extracted {len(menu_items)} items from {restaurant_name}")
                
            except Exception as e:
                print(f"{Fore.RED}⚠️ Worker {worker_id}: Menu extraction failed: {e}")
        
        return menu_items
        
    except Exception as e:
        print(f"{Fore.RED}❌ Worker {worker_id}: Failed to process {url}: {e}")
        return None

def worker_function(worker_id, url_batch):
    """Worker function to process URLs"""
    print(f"{Fore.CYAN}🚀 Worker {worker_id}: Starting with {len(url_batch)} URLs")
    
    session = None
    processed_count = 0
    
    try:
        for i, url in enumerate(url_batch):
            # Create or refresh session periodically
            if session is None or processed_count % SESSION_REFRESH_INTERVAL == 0:
                if session:
                    session.close()
                session = create_proxy_session()
                print(f"{Fore.BLUE}🔄 Worker {worker_id}: Session {'created' if processed_count == 0 else 'refreshed'}")
            
            # Process URL with retry
            for attempt in range(RETRY_ATTEMPTS):
                try:
                    result = extract_menu_data(session, url, worker_id)
                    
                    if result == "RATE_LIMITED":
                        backoff_time = (2 ** attempt) * 30
                        print(f"{Fore.YELLOW}⏳ Worker {worker_id}: Rate limited, waiting {backoff_time} seconds...")
                        time.sleep(backoff_time)
                        continue
                    
                    if result:
                        with output_lock:
                            all_menu_items.extend(result)
                        processed_count += 1
                        break
                    else:
                        if attempt < RETRY_ATTEMPTS - 1:
                            print(f"{Fore.YELLOW}🔄 Worker {worker_id}: Retrying {url} (attempt {attempt + 2})")
                            time.sleep(5)
                        else:
                            with output_lock:
                                failed_urls.append(url)
                
                except Exception as e:
                    print(f"{Fore.RED}❌ Worker {worker_id}: Error on attempt {attempt + 1}: {e}")
                    if attempt < RETRY_ATTEMPTS - 1:
                        time.sleep(5)
                    else:
                        with output_lock:
                            failed_urls.append(url)
            
            # Delay between requests
            delay = random.uniform(MIN_DELAY_BETWEEN_REQUESTS, MAX_DELAY_BETWEEN_REQUESTS)
            time.sleep(delay)
    
    except Exception as e:
        print(f"{Fore.RED}❌ Worker {worker_id}: Critical error: {e}")
    
    finally:
        if session:
            session.close()
            print(f"{Fore.BLUE}🔐 Worker {worker_id}: Session closed")
    
    print(f"{Fore.GREEN}✅ Worker {worker_id}: Completed")

def save_to_csv(data, output_dir):
    """Save scraped data to CSV file"""
    if not data:
        print(f"{Fore.RED}❌ No data to save")
        return
    
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_filename = os.path.join(output_dir, f"talabat_menu_data_{timestamp}.csv")
    
    # Define CSV columns
    fieldnames = ["url", "restaurant_name", "category", "item_name", "description", "price", "scraped_at"]
    
    try:
        with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)
        
        print(f"{Fore.GREEN}✅ CSV saved: {csv_filename}")
        print(f"{Fore.GREEN}📊 Total items: {len(data)}")
        
        # Also save JSON for backup
        json_filename = os.path.join(output_dir, f"talabat_menu_data_{timestamp}.json")
        with open(json_filename, 'w', encoding='utf-8') as jsonfile:
            json.dump(data, jsonfile, indent=2, ensure_ascii=False)
        print(f"{Fore.GREEN}✅ JSON backup saved: {json_filename}")
        
    except Exception as e:
        print(f"{Fore.RED}❌ Failed to save CSV: {e}")

def main():
    print(f"{Fore.CYAN}{'='*60}")
    print(f"{Fore.CYAN}🚀 Talabat Menu Scraper - CSV Export")
    print(f"{Fore.CYAN}{'='*60}\n")
    
    # Load URLs from JSON
    urls_file = "talabat_urls.json"
    try:
        with open(urls_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            urls = data.get('urls', [])
    except Exception as e:
        print(f"{Fore.RED}❌ Failed to load {urls_file}: {e}")
        return
    
    if not urls:
        print(f"{Fore.RED}❌ No URLs found in {urls_file}")
        return
    
    print(f"{Fore.BLUE}📊 Loaded {len(urls)} URLs from {urls_file}")
    print(f"{Fore.BLUE}👷 Using {CONCURRENT_WORKERS} workers\n")
    
    # Split URLs into batches for workers
    batch_size = max(1, len(urls) // CONCURRENT_WORKERS)
    url_batches = [urls[i:i+batch_size] for i in range(0, len(urls), batch_size)]
    
    # Process in parallel
    with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
        futures = [executor.submit(worker_function, i, batch) for i, batch in enumerate(url_batches)]
        
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"{Fore.RED}❌ Worker failed: {e}")
    
    # Save results
    output_dir = "complete_menu_data"
    save_to_csv(all_menu_items, output_dir)
    
    # Summary
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"{Fore.GREEN}✅ Scraping Complete!")
    print(f"{Fore.CYAN}{'='*60}")
    print(f"{Fore.BLUE}📊 Total items scraped: {len(all_menu_items)}")
    print(f"{Fore.BLUE}✅ Successful URLs: {len(urls) - len(failed_urls)}")
    print(f"{Fore.BLUE}❌ Failed URLs: {len(failed_urls)}")
    
    if failed_urls:
        print(f"\n{Fore.YELLOW}⚠️ Failed URLs:")
        for url in failed_urls:
            print(f"  - {url}")

if __name__ == "__main__":
    main()
