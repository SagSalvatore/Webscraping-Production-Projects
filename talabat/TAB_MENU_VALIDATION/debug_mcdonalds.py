import json
from bs4 import BeautifulSoup
from scrape_to_csv import create_proxy_session
from colorama import init, Fore

init(autoreset=True)

def debug_mcdonalds_url():
    url = "https://www.talabat.com/uae/restaurant/629890/mcdonalds-um-al-sheif-enoc-al-waslum-al-sheif?aid=1353"
    
    print(f"{Fore.CYAN}🔍 Debugging McDonald's URL")
    print(f"{Fore.CYAN}URL: {url}\n")
    
    session = create_proxy_session()
    
    try:
        response = session.get(url, timeout=20)
        print(f"{Fore.GREEN}✅ Response Status: {response.status_code}")
        print(f"{Fore.BLUE}📄 Response Length: {len(response.text)} characters\n")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Check for __NEXT_DATA__
        script_tag = soup.find('script', {'id': '__NEXT_DATA__'})
        if script_tag:
            print(f"{Fore.GREEN}✅ Found __NEXT_DATA__ script tag")
            
            try:
                next_data = json.loads(script_tag.string)
                print(f"{Fore.BLUE}📊 __NEXT_DATA__ structure:")
                
                # Navigate the structure
                page_props = next_data.get('props', {}).get('pageProps', {})
                print(f"  - pageProps keys: {list(page_props.keys())}")
                
                menu_state = page_props.get('initialMenuState', {})
                print(f"  - initialMenuState keys: {list(menu_state.keys())}")
                
                menu_data = menu_state.get('menuData', {})
                print(f"  - menuData keys: {list(menu_data.keys())}")
                
                items = menu_data.get("items", [])
                print(f"  - items count: {len(items)}")
                
                if items:
                    print(f"\n{Fore.GREEN}✅ Sample item:")
                    print(json.dumps(items[0], indent=2))
                else:
                    print(f"\n{Fore.YELLOW}⚠️ No items found in menuData")
                    print(f"\n{Fore.BLUE}Full menuData:")
                    print(json.dumps(menu_data, indent=2)[:1000])
                    
            except Exception as e:
                print(f"{Fore.RED}❌ Error parsing __NEXT_DATA__: {e}")
        else:
            print(f"{Fore.RED}❌ No __NEXT_DATA__ script tag found")
            
            # Check for restaurant name
            name_selectors = ['a[data-testid="branch-name"]', 'h1', 'h2']
            for selector in name_selectors:
                element = soup.select_one(selector)
                if element:
                    print(f"{Fore.BLUE}Found name element ({selector}): {element.text.strip()}")
        
    except Exception as e:
        print(f"{Fore.RED}❌ Error: {e}")
    
    finally:
        session.close()

if __name__ == "__main__":
    debug_mcdonalds_url()
