import json
import csv
import os
from datetime import datetime
from scrape_to_csv import create_proxy_session, extract_menu_data
from colorama import init, Fore

init(autoreset=True)

def scrape_single_url(url, output_dir="complete_menu_data"):
    """Scrape a single URL and append to existing CSV"""
    print(f"{Fore.CYAN}{'='*60}")
    print(f"{Fore.CYAN}🚀 Scraping Single URL")
    print(f"{Fore.CYAN}{'='*60}\n")
    print(f"{Fore.YELLOW}URL: {url}\n")
    
    # Create session and scrape
    session = create_proxy_session()
    print(f"{Fore.BLUE}🔄 Session created")
    
    menu_items = extract_menu_data(session, url, worker_id=0)
    session.close()
    
    if not menu_items or menu_items == "RATE_LIMITED":
        print(f"{Fore.RED}❌ Failed to scrape the URL")
        return False
    
    print(f"{Fore.GREEN}✅ Successfully extracted {len(menu_items)} items\n")
    
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
        
        print(f"{Fore.GREEN}✅ Appended {len(menu_items)} items to: {csv_path}")
        
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
    success = scrape_single_url(url)
    
    if success:
        print(f"\n{Fore.CYAN}{'='*60}")
        print(f"{Fore.GREEN}✅ Scraping Complete!")
        print(f"{Fore.CYAN}{'='*60}")
    else:
        print(f"\n{Fore.CYAN}{'='*60}")
        print(f"{Fore.RED}❌ Scraping Failed")
        print(f"{Fore.CYAN}{'='*60}")
