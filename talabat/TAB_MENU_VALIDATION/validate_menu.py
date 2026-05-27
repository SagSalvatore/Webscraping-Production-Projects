import json
import time
import random
import os
import requests
import hashlib
from datetime import datetime
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import quote
from colorama import init, Fore, Style

# Initialize colorama
init(autoreset=True)

# Proxy configuration. Set these in your local environment before running.
PROXY_USERNAME = os.getenv("OXYLABS_USERNAME", "")
PROXY_PASSWORD = os.getenv("OXYLABS_PASSWORD", "")
PROXY_COUNTRY = os.getenv("OXYLABS_COUNTRY", "ae")

def create_proxy_session():
    """Create requests session with proper proxy configuration"""
    if not PROXY_USERNAME or not PROXY_PASSWORD:
        raise RuntimeError("Set OXYLABS_USERNAME and OXYLABS_PASSWORD before running this script.")

    session_id = random.randint(10000, 99999)
    # Using the proxy format from the original script
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

def clean_price(price_str):
    """Normalize price to float"""
    if not price_str:
        return 0.0
    if isinstance(price_str, (int, float)):
        return float(price_str)
    # Remove currency and whitespace
    clean = str(price_str).lower().replace('aed', '').replace('qr', '').strip()
    try:
        return float(clean)
    except ValueError:
        return 0.0

def normalize_text(text):
    """Normalize string for comparison"""
    if not text:
        return ""
    return ' '.join(str(text).split()).lower()

def extract_menu_data(session, url):
    """Scrape menu data from Talabat URL"""
    try:
        response = session.get(url, timeout=20)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # The key data is usually in the __NEXT_DATA__ script tag
        script_tag = soup.find('script', {'id': '__NEXT_DATA__'})
        if not script_tag:
            print(f"{Fore.RED}❌ No data found for {url}")
            return None

        next_data = json.loads(script_tag.string)
        page_props = next_data.get('props', {}).get('pageProps', {})
        menu_state = page_props.get('initialMenuState', {})
        menu_data = menu_state.get('menuData', {})
        items = menu_data.get("items", [])
        
        parsed_menu = []
        for item in items:
            if item and isinstance(item, dict):
                parsed_menu.append({
                    "menu category": item.get("originalSection", "Unknown").strip(),
                    "menu item(name)": item.get("name", "Unknown").strip(),
                    "description": item.get("description", "").strip(),
                    "price": float(item.get("price", 0))
                })
        return parsed_menu

    except Exception as e:
        print(f"{Fore.RED}❌ Failed to scrape {url}: {e}")
        return None

def generate_item_hash(item):
    """Create a hash of the item content to quickly check for changes"""
    content = f"{normalize_text(item['menu category'])}{normalize_text(item['menu item(name)'])}"
    # We might want to include price/description in hash or check them separately
    # For now, let's use name+category as key, and compare other fields
    return hashlib.md5(content.encode('utf-8')).hexdigest()

def validate_data(input_file):
    """Main validation logic"""
    print(f"{Fore.CYAN}🚀 Starting Validation Pipeline...")
    print(f"{Fore.CYAN}📂 Reading input: {input_file}")
    
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"{Fore.RED}❌ Could not read input file: {e}")
        return

    # If the input is a list of objects, we assume each object has 'menu url' or 'urls'
    # Based on the user's snippet: {"urls": "...", "ingredients": "..."}
    # But user also mentioned: {"menu url": "...", "menu category": ...} which looks like flattened items?
    # Let's handle both: unique URLs list vs full item list.
    
    # Strategy: Group by URL first used in the input file
    urls_to_check = set()
    expected_data_by_url = {}

    for entry in data:
        # Determine URL key - prioritize 'menu url' as requested, fallback to 'urls'
        url = entry.get('menu url') or entry.get('urls')
        
        if not url:
            continue
            
        # Strict URL Filtering per user request
        # "must be som aid or some extended url" -> usually contains '/restaurant/' or 'aid='
        if '/restaurant/' not in url and 'aid=' not in url:
             # Skip this URL as it's not the specific menu format required
             continue
            
        urls_to_check.add(url)
        if url not in expected_data_by_url:
            expected_data_by_url[url] = []
        
        # If the input file is item-level (flattened), store the expected item
        if 'menu item(name)' in entry:
            expected_data_by_url[url].append(entry)

    print(f"{Fore.BLUE}ℹ️ Found {len(urls_to_check)} valid 'extended' URLs to validate.")

    session = create_proxy_session()
    
    updated_full_data = [] # Store everything for the new file
    change_log = []

    for url in urls_to_check:
        print(f"\n{Fore.YELLOW}🔍 Checking: {url}")
        
        live_menu = extract_menu_data(session, url)
        if live_menu is None:
            # If failed, keep old data but mark error? 
            # For now, just copy old data or log error
            change_log.append(f"FAILED TO SCRAPE: {url}")
            continue
            
        # If we have expected data (granual items), compare them
        expected_items = expected_data_by_url.get(url, [])
        
        if not expected_items:
            print(f"{Fore.RED}⚠️ No expected items found in input for this URL to compare against.")
            # Add all live items as new
            for item in live_menu:
                item['menu url'] = url
                updated_full_data.append(item)
            continue

        # Comparison Logic
        # Map expected items by (Category + Name) for easy lookup
        expected_map = {generate_item_hash(item): item for item in expected_items}
        live_map = {generate_item_hash(item): item for item in live_menu}
        
        # Check for matches and mismatches
        for item_hash, live_item in live_map.items():
            live_item['menu url'] = url # Add URL back for consistency
            
            if item_hash in expected_map:
                # Item exists in both, check fields for changes
                exp_item = expected_map[item_hash]
                
                # Check Price
                if abs(clean_price(live_item['price']) - clean_price(exp_item['price'])) > 0.01:
                    change_msg = f"💰 Price Changed: {live_item['menu item(name)']} ({exp_item['price']} -> {live_item['price']})"
                    print(f"{Fore.MAGENTA}{change_msg}")
                    change_log.append(change_msg)
                
                # Check Description
                if normalize_text(live_item['description']) != normalize_text(exp_item['description']):
                    change_msg = f"📝 Desc Changed: {live_item['menu item(name)']}"
                    print(f"{Fore.MAGENTA}{change_msg}")
                    change_log.append(change_msg)
                
                # Use LIVE data for the new file (so it's updated)
                updated_full_data.append(live_item)
                
            else:
                # New Item found on live site
                print(f"{Fore.GREEN}➕ New Item: {live_item['menu item(name)']}")
                change_log.append(f"NEW ITEM: {live_item['menu item(name)']} in {url}")
                updated_full_data.append(live_item)

        # Check for Removed items (In Expected but not in Live)
        for item_hash, exp_item in expected_map.items():
            if item_hash not in live_map:
                print(f"{Fore.RED}➖ Removed Item: {exp_item['menu item(name)']}")
                change_log.append(f"REMOVED ITEM: {exp_item['menu item(name)']} from {url}")
                # Do NOT add to updated_full_data if it's gone
                
    # Save Updated File
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"validated_menu_{timestamp}.json"
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(updated_full_data, f, indent=2, ensure_ascii=False)
        
    print(f"\n{Fore.GREEN}✅ Validation Complete!")
    print(f"{Fore.GREEN}📁 Updated Data Saved to: {output_file}")
    
    # Save Report
    if change_log:
        report_file = f"validation_report_{timestamp}.txt"
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(change_log))
        print(f"{Fore.YELLOW}⚠️ Differences found! See report: {report_file}")
    else:
        print(f"{Fore.GREEN}✨ No discrepancies found.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Talabat Menu Validator")
    parser.add_argument("--input", required=True, help="Path to the JSON file to validate")
    args = parser.parse_args()
    
    validate_data(args.input)
