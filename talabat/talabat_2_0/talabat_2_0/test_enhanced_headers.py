import requests
import time
import random
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json

def create_enhanced_session():
    """Create requests session with enhanced anti-bot protection (no proxy)"""
    session = requests.Session()
    
    # Add retry strategy
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # Enhanced headers to mimic real browser
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0',
    })
    
    return session

def test_restaurant_access(url):
    """Test accessing restaurant page with enhanced headers"""
    print(f"🔧 Testing URL: {url}")
    
    session = create_enhanced_session()
    
    try:
        # Clean URL
        clean_url = url.split('#')[0].strip()
        
        # Add random delay before request
        delay = random.uniform(2, 5)
        print(f"⏳ Waiting {delay:.1f} seconds...")
        time.sleep(delay)
        
        # Make request
        response = session.get(clean_url, timeout=20)
        print(f"📊 Status Code: {response.status_code}")
        
        # Check for rate limiting
        if 'rate limit' in response.text.lower() or response.status_code == 429:
            print("⚠️ Rate limiting detected")
            return False
        
        if response.status_code == 403:
            print("❌ 403 Forbidden - Still blocked")
            return False
        
        if response.status_code == 200:
            print("✅ Success! Analyzing page content...")
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Check for __NEXT_DATA__
            script_tag = soup.find('script', {'id': '__NEXT_DATA__'})
            if script_tag:
                print("✅ Found __NEXT_DATA__ script!")
                try:
                    next_data = json.loads(script_tag.string)
                    page_props = next_data.get('props', {}).get('pageProps', {})
                    print(f"📋 PageProps keys: {list(page_props.keys())}")
                    
                    # Check for menu data
                    menu_state = page_props.get('initialMenuState', {})
                    if menu_state:
                        menu_data = menu_state.get('menuData', {})
                        items = menu_data.get("items", [])
                        print(f"🍽️ Found {len(items)} menu items!")
                    else:
                        print("⚠️ No menu state found in pageProps")
                        
                except Exception as e:
                    print(f"❌ Error parsing __NEXT_DATA__: {e}")
            else:
                print("❌ No __NEXT_DATA__ script found")
            
            # Check for LD+JSON script
            ld_json_scripts = soup.find_all('script', {'type': 'application/ld+json'})
            if ld_json_scripts:
                print(f"✅ Found {len(ld_json_scripts)} LD+JSON script(s)!")
                for i, script in enumerate(ld_json_scripts):
                    try:
                        ld_data = json.loads(script.string)
                        print(f"📋 LD+JSON {i+1} type: {ld_data.get('@type', 'Unknown')}")
                        
                        if ld_data.get('@type') == 'Restaurant':
                            print(f"🏪 Restaurant Name: {ld_data.get('name', 'N/A')}")
                            print(f"🍽️ Cuisine Types: {ld_data.get('servesCuisine', 'N/A')}")
                            
                            # Extract geo coordinates
                            geo = ld_data.get('geo', {})
                            if geo:
                                lat = geo.get('latitude', 'N/A')
                                lng = geo.get('longitude', 'N/A')
                                print(f"📍 Coordinates: {lat}, {lng}")
                            
                            # Extract address
                            address = ld_data.get('address', {})
                            if address:
                                street = address.get('streetAddress', 'N/A')
                                locality = address.get('addressLocality', 'N/A')
                                print(f"📍 Address: {street}, {locality}")
                                
                    except json.JSONDecodeError as e:
                        print(f"❌ Error parsing LD+JSON {i+1}: {e}")
            else:
                print("❌ No LD+JSON scripts found")
            
            # Check for restaurant name
            name_selectors = [
                'a[data-testid="branch-name"]',
                'h1',
                'h2'
            ]
            
            for selector in name_selectors:
                element = soup.select_one(selector)
                if element and element.text.strip():
                    print(f"🏪 Restaurant name: {element.text.strip()}")
                    break
            
            return True
        
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return False
    
    finally:
        session.close()

if __name__ == "__main__":
    # Test URL from previous attempts
    test_url = "https://www.talabat.com/uae/restaurant/749515/wimpy-mohammed-bin-zayed-city?aid=6484"
    
    print("🚀 Testing Enhanced Anti-Bot Protection (No Proxy)")
    print("="*60)
    
    success = test_restaurant_access(test_url)
    
    if success:
        print("\n✅ SUCCESS: Enhanced headers worked!")
        print("Ready to integrate into spider...")
    else:
        print("\n❌ FAILED: Still getting blocked")
        print("May need additional strategies...")