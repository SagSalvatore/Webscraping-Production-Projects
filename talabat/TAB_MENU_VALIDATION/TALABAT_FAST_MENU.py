import json
import time
import random
import os
import csv
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed



# === ✅ Optimized Settings
CONCURRENT_WORKERS = 2
MIN_DELAY_BETWEEN_REQUESTS = 3
MAX_DELAY_BETWEEN_REQUESTS = 6
RETRY_ATTEMPTS = 3
SESSION_REFRESH_INTERVAL = 50  # Refresh session every 50 requests

# Proxy configuration. Set these in your local environment before running.
USERNAME = os.getenv("OXYLABS_USERNAME", "")
PASSWORD = os.getenv("OXYLABS_PASSWORD", "")
COUNTRY = os.getenv("OXYLABS_COUNTRY", "ae")

# === ✅ Thread-safe storage
output_lock = threading.Lock()
output = []
failed_urls = []

def create_proxy_session():
    """Create requests session with proper proxy configuration"""
    if not USERNAME or not PASSWORD:
        raise RuntimeError("Set OXYLABS_USERNAME and OXYLABS_PASSWORD before running this script.")

    session_id = random.randint(10000, 99999)
    
    proxy_user = f"customer-{USERNAME}-cc-{COUNTRY}-sessid-{session_id}"
    proxy_url = f"http://{proxy_user}:{PASSWORD}@pr.oxylabs.io:7777"
    
    session = requests.Session()
    session.proxies = {
        'http': proxy_url,
        'https': proxy_url
    }
    
    # Add retry strategy
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # Headers to mimic browser
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    })
    
    return session, session_id

def verify_session_proxy(session):
    """Verify session is using proxy correctly"""
    try:
        response = session.get("https://httpbin.org/ip", timeout=10)
        if response.status_code == 200:
            ip_data = response.json()
            current_ip = ip_data.get('origin', 'Unknown')
            print(f"✅ Session proxy verified - IP: {current_ip}")
            return True, current_ip
        else:
            print(f"❌ Session proxy verification failed - Status: {response.status_code}")
            return False, None
    except Exception as e:
        print(f"❌ Session proxy verification failed: {e}")
        return False, None

def extract_restaurant_data_requests(session, url, worker_id):
    """Extract restaurant data using requests only"""
    try:
        print(f"🔧 Worker {worker_id}: Processing {url}")
        
        # Clean URL
        clean_url = url.split('#')[0].strip()
        
        # Make request
        response = session.get(clean_url, timeout=20)
        response.raise_for_status()
        
        # Check for rate limiting
        if 'rate limit' in response.text.lower() or response.status_code == 429:
            print(f"⚠️ Worker {worker_id}: Rate limiting detected")
            return "RATE_LIMITED"
        
        # Parse HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract restaurant name
        restaurant_name = "Unknown"
        name_selectors = [
            'a[data-testid="branch-name"]',
            'h1',
            'h2'
        ]
        
        for selector in name_selectors:
            element = soup.select_one(selector)
            if element and element.text.strip():
                restaurant_name = element.text.strip()
                break
        
        # Extract menu from __NEXT_DATA__
        menu = []
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
                        menu.append({
                            "category": item.get("originalSection", "Unknown"),
                            "name": item.get("name", "Unknown"),
                            "description": item.get("description", ""),
                            "price": f"AED {item.get('price', 'N/A')}"
                        })
                
                print(f"✅ Worker {worker_id}: Extracted {len(menu)} menu items")
                
            except Exception as e:
                print(f"⚠️ Worker {worker_id}: Menu extraction failed: {e}")
        
        # For info tab, we'll need to make a separate request or use available data
        info = {
            "Minimum Order": "Not Available via Requests",
            "Delivery Time": "Not Available via Requests", 
            "Rating": "Not Available via Requests"
        }
        
        return {
            "url": url,
            "restaurant_name": restaurant_name,
            "menu_items": menu,
            "info": info,
            "worker_id": worker_id,
            "processed_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
    except Exception as e:
        print(f"❌ Worker {worker_id}: Failed to process {url}: {e}")
        return None

def worker_function_requests(worker_id, url_batch):
    """Worker function using requests only"""
    print(f"🚀 Worker {worker_id}: Starting with {len(url_batch)} URLs")
    
    session = None
    results = []
    processed_count = 0
    
    try:
        for i, url in enumerate(url_batch):
            # Create or refresh session periodically
            if session is None or processed_count % SESSION_REFRESH_INTERVAL == 0:
                if session:
                    session.close()
                
                session, session_id = create_proxy_session()
                
                # Verify proxy
                proxy_verified, proxy_ip = verify_session_proxy(session)
                if not proxy_verified:
                    print(f"❌ Worker {worker_id}: Session proxy verification failed")
                    continue
                
                print(f"🔄 Worker {worker_id}: {'Session created' if processed_count == 0 else 'Session refreshed'} - IP: {proxy_ip}")
            
            # Process URL with retry
            for attempt in range(RETRY_ATTEMPTS):
                try:
                    result = extract_restaurant_data_requests(session, url, worker_id)
                    
                    if result == "RATE_LIMITED":
                        backoff_time = (2 ** attempt) * 30
                        print(f"⏳ Worker {worker_id}: Rate limited, waiting {backoff_time} seconds...")
                        time.sleep(backoff_time)
                        continue
                    
                    if result:
                        results.append(result)
                        
                        with output_lock:
                            output.append(result)
                            
                            if len(output) % 10 == 0:
                                save_progress(output, "talabat_requests_output.json")
                        
                        processed_count += 1
                        break
                    else:
                        if attempt < RETRY_ATTEMPTS - 1:
                            print(f"🔄 Worker {worker_id}: Retrying {url} (attempt {attempt + 2})")
                            time.sleep(5)
                        else:
                            with output_lock:
                                failed_urls.append(url)
                
                except Exception as e:
                    print(f"❌ Worker {worker_id}: Error on attempt {attempt + 1}: {e}")
                    if attempt < RETRY_ATTEMPTS - 1:
                        time.sleep(5)
                    else:
                        with output_lock:
                            failed_urls.append(url)
            
            # Delay between requests
            delay = random.uniform(MIN_DELAY_BETWEEN_REQUESTS, MAX_DELAY_BETWEEN_REQUESTS)
            time.sleep(delay)
    
    except Exception as e:
        print(f"❌ Worker {worker_id}: Critical error: {e}")
    
    finally:
        if session:
            session.close()
            print(f"🔐 Worker {worker_id}: Session closed")
    
    print(f"✅ Worker {worker_id}: Completed {len(results)} URLs")
    return results

def save_progress(results, filename="talabat_requests_output.json"):
    """Save progress to avoid data loss"""
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"💾 Progress saved: {len(results)} items")
    except Exception as e:
        print(f"Failed to save progress: {e}")

def load_progress(filename="talabat_requests_output.json"):
    """Load previous progress"""
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Failed to load progress: {e}")
    return []

# === ✅ Main execution for requests-only approach
def main():
    print("🚀 Starting Requests-Only Talabat Scraper")
    print("="*50)
    
    # Your URLs here
    urls = [
       "https://www.talabat.com/uae/restaurant/602802/baskin-robbins-qariyat-al-beri?aid=6484",




]
    
    # Load progress
    existing_results = load_progress()
    processed_urls = {result["url"] for result in existing_results}
    
    # Filter remaining URLs
    remaining_urls = [url for url in urls if url not in processed_urls]
    
    if existing_results:
        output.extend(existing_results)
        print(f"📊 Loaded {len(existing_results)} previously processed URLs")
    
    if not remaining_urls:
        print("✅ All URLs already processed!")
        return
    
    print(f"📊 Processing {len(remaining_urls)} remaining URLs")
    
    # Split into batches
    batch_size = len(remaining_urls) // CONCURRENT_WORKERS
    url_batches = [remaining_urls[i:i+batch_size] for i in range(0, len(remaining_urls), batch_size)]
    
    # Process in parallel
    with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
        futures = [executor.submit(worker_function_requests, i, batch) for i, batch in enumerate(url_batches)]
        
        for future in as_completed(futures):
            try:
                results = future.result()
                print(f"✅ Worker completed: {len(results)} successful items")
            except Exception as e:
                print(f"❌ Worker failed: {e}")
    
    # Save final results
    save_progress(output, "talabat_requests_final_20-07.json")
    print(f"✅ Final results saved. Success: {len(output)}, Failed: {len(failed_urls)}")

if __name__ == "__main__":
    main()
