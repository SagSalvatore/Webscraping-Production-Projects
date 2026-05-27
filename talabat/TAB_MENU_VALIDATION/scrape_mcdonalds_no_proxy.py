import json
import csv
import os
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from colorama import init, Fore

init(autoreset=True)

def create_session_no_proxy():
    """Create requests session WITHOUT proxy"""
    session = requests.Session()
    
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

def extract_menu_no_proxy(session, url):
    """Extract restaurant data WITHOUT proxy"""
    try:
        print(f"{Fore.YELLOW}🔧 Processing {url}")
        
        clean_url = url.split('#')[0].strip()
        response = session.get(clean_url, timeout=20)
        response.raise_for_status()
        
        print(f"{Fore.GREEN}✅ Response Status: {response.status_code}")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract restaurant name
        restaurant_name = "Unknown"
        name_selectors = ['a[data-testid="branch-name"]', 'h1', 'h2']
        for selector in name_selectors:
            element = soup.select_one(selector)
            if element and element.text.strip():
                restaurant_name = element.text.strip()
                break
        
        print(f"{Fore.BLUE}📍 Restaurant: {restaurant_name}")
        
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
                
                print(f"{Fore.BLUE}📊 Found {len(items)} items in menuData")
                
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
                
                print(f"{Fore.GREEN}✅ Extracted {len(menu_items)} menu items")
                
            except Exception as e:
                print(f"{Fore.RED}⚠️ Menu extraction failed: {e}")
        else:
            print(f"{Fore.RED}❌ No __NEXT_DATA__ found")
        
        return menu_items
        
    except Exception as e:
        print(f"{Fore.RED}❌ Failed to process {url}: {e}")
        return None

def scrape_and_append(url, output_dir="complete_menu_data"):
    """Scrape URL without proxy and append to CSV"""
    print(f"{Fore.CYAN}{'='*60}")
    print(f"{Fore.CYAN}🚀 Scraping WITHOUT Proxy")
    print(f"{Fore.CYAN}{'='*60}\n")
    
    session = create_session_no_proxy()
    print(f"{Fore.BLUE}🔄 Session created (NO PROXY)\n")
    
    menu_items = extract_menu_no_proxy(session, url)
    session.close()
    
    if not menu_items:
        print(f"\n{Fore.RED}❌ No items extracted")
        return False
    
    # Find the latest CSV file
    csv_files = [f for f in os.listdir(output_dir) if f.endswith('.csv')]
    if not csv_files:
        print(f"{Fore.RED}❌ No existing CSV file found")
        return False
    
    latest_csv = max(csv_files, key=lambda f: os.path.getmtime(os.path.join(output_dir, f)))
    csv_path = os.path.join(output_dir, latest_csv)
    
    # Append to CSV
    fieldnames = ["url", "restaurant_name", "category", "item_name", "description", "price", "scraped_at"]
    
    try:
        with open(csv_path, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writerows(menu_items)
        
        print(f"\n{Fore.GREEN}✅ Appended {len(menu_items)} items to: {csv_path}")
        
        # Also update JSON
        json_path = csv_path.replace('.csv', '.json')
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
            
            existing_data.extend(menu_items)
            
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, indent=2, ensure_ascii=False)
            
            print(f"{Fore.GREEN}✅ Updated JSON backup: {json_path}")
        
        return True
        
    except Exception as e:
        print(f"{Fore.RED}❌ Failed to append to CSV: {e}")
        return False

if __name__ == "__main__":
    url = "https://www.talabat.com/uae/restaurant/629890/mcdonalds-um-al-sheif-enoc-al-waslum-al-sheif?aid=1353"
    success = scrape_and_append(url)
    
    print(f"\n{Fore.CYAN}{'='*60}")
    if success:
        print(f"{Fore.GREEN}✅ Scraping Complete!")
    else:
        print(f"{Fore.RED}❌ Scraping Failed - No menu data available")
    print(f"{Fore.CYAN}{'='*60}")
