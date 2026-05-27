import requests
import json
from validate_menu import create_proxy_session, extract_menu_data, normalize_text

def debug_url():
    url = "https://www.talabat.com/uae/restaurant/754233/DUBAI-MARINA?&aid=1272"
    session = create_proxy_session()
    
    print(f"Scraping {url}...")
    menu = extract_menu_data(session, url)
    
    if not menu:
        print("Failed to scrape.")
        return

    print("\n--- Live 'Shakshuka' Items ---")
    found = False
    for item in menu:
        if "shakshuka" in item['menu item(name)'].lower():
            found = True
            print(f"Name: '{item['menu item(name)']}'")
            print(f"Category: '{item['menu category']}'")
            print(f"Normalized Hash Key: '{normalize_text(item['menu category'])}{normalize_text(item['menu item(name)'])}'")
            print("-" * 20)
            
    if not found:
        print("No 'Shakshuka' found in live menu.")

    print("\n--- Expected 'Shakshuka' (from test_sample) ---")
    # From test_sample.json
    exp_name = "Shakshuka"
    exp_cat = "Breakfast"
    print(f"Name: '{exp_name}'")
    print(f"Category: '{exp_cat}'")
    print(f"Normalized Hash Key: '{normalize_text(exp_cat)}{normalize_text(exp_name)}'")

if __name__ == "__main__":
    debug_url()
