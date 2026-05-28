"""
Foodpanda Full Menu Scraper - Improved Version
With rate limiting, backoff, and resume capability

Features:
- Slow and steady approach (no rate limiting)
- Exponential backoff on 403 errors
- Incremental progress saves
- Resume from last checkpoint
"""

import asyncio
import json
import pandas as pd
from datetime import datetime
from curl_cffi import requests as curl_requests
from typing import Dict, List
import time
import random


class ImprovedFoodpandaMenuScraper:
    """Robust menu scraper with anti-rate-limiting measures"""
    
    def __init__(self):
        self.api_url_template = "https://ph.fd-api.com/api/v5/vendors/{vendor_code}"
        
        # Rate limiting settings
        self.min_delay = 2.0  # Minimum delay between requests (seconds)
        self.max_delay = 5.0  # Maximum delay
        self.backoff_multiplier = 2  # Multiply delay on 403 error
        self.max_retries = 3
        
        # Headers
        self.headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Api-Version': '7',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            'X-FP-API-KEY': 'volo',
            'X-PD-Language-ID': '1',
            'dps-session-id': 'eyJzZXNzaW9uX2lkIjoiZTMzMDM4ZTNmZDhhNjNkOTVlMzIzNzU1ZjM3NTE4MjEiLCJwZXJzZXVzX2lkIjoiMTc2NTk1MDU2NzQyOC4yNDcxMDA5MjA5ODc5NzcwMDAuOXN2YTNibzdzeSIsInRpbWVzdGFtcCI6MTc2NTk1NjkxNX0=',
            'perseus-client-id': '1765950567428.247100920987977000.9sva3bo7sy',
            'perseus-session-id': '1765954909766.015526697181286464.tm8z9nrzhu',
            'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
        
        # Stats
        self.success_count = 0
        self.error_count = 0
        self.total_menu_items = 0
        self.current_delay = self.min_delay
        
    async def fetch_restaurant_menu(self, vendor_code: str, restaurant_name: str, retry_count: int = 0) -> Dict:
        """
        Fetch menu with exponential backoff on errors
        """
        api_url = self.api_url_template.format(vendor_code=vendor_code)
        
        params = {
            'include': 'menus,bundles,multiple_discounts',
            'language_id': '1',
            'opening_type': 'delivery',
            'basket_currency': 'PHP'
        }
        
        try:
            # Random delay to appear more human
            delay = random.uniform(self.current_delay, self.current_delay + 1.0)
            await asyncio.sleep(delay)
            
            response = curl_requests.get(
                api_url,
                params=params,
                headers=self.headers,
                impersonate="chrome110",
                timeout=30
            )
            
            if response.status_code == 403:
                # Rate limited - exponential backoff
                if retry_count < self.max_retries:
                    self.current_delay = min(self.current_delay * self.backoff_multiplier, 30)
                    print(f"  ⏸️  Rate limited, backing off to {self.current_delay:.1f}s delay...")
                    await asyncio.sleep(self.current_delay)
                    return await self.fetch_restaurant_menu(vendor_code, restaurant_name, retry_count + 1)
                else:
                    print(f"  ❌ {vendor_code}: Max retries reached")
                    self.error_count += 1
                    return None
            
            elif response.status_code != 200:
                print(f"  ⚠️  {vendor_code}: HTTP {response.status_code}")
                self.error_count += 1
                return None
            
            # Success - reduce delay gradually
            if self.current_delay > self.min_delay:
                self.current_delay = max(self.min_delay, self.current_delay * 0.9)
            
            data = response.json()
            vendor_data = data.get('data', {})
            
            # Parse menu data
            restaurant_info = {
                'vendor_code': vendor_code,
                'name': vendor_data.get('name', restaurant_name),
                'rating': vendor_data.get('rating', 0),
                'review_count': vendor_data.get('review_number', 0),
                'cuisines': [c.get('name', '') for c in vendor_data.get('cuisines', [])],
                'city': vendor_data.get('city', {}).get('name', '') if isinstance(vendor_data.get('city'), dict) else '',
            }
            
            menu_categories = []
            menus = vendor_data.get('menus', [])
            
            if menus:
                categories = menus[0].get('menu_categories', [])
                
                for category in categories:
                    category_data = {
                        'category_name': category.get('name', ''),
                        'items': []
                    }
                    
                    for product in category.get('products', []):
                        variations = product.get('product_variations', [])
                        price = variations[0].get('price', 0) if variations else 0
                        
                        category_data['items'].append({
                            'name': product.get('name', ''),
                            'price': f"₱ {price}" if price > 0 else "",
                            'price_value': price,
                            'description': product.get('description', ''),
                            'image_url': product.get('file_path', ''),
                            'is_sold_out': product.get('is_sold_out', False)
                        })
                        self.total_menu_items += 1
                    
                    if category_data['items']:
                        menu_categories.append(category_data)
            
            result = {
                'restaurant': restaurant_info,
                'menu': menu_categories,
                'total_categories': len(menu_categories),
                'total_items': sum(len(cat['items']) for cat in menu_categories),
                'scraped_at': datetime.now().isoformat()
            }
            
            self.success_count += 1
            return result
            
        except Exception as e:
            print(f"  ❌ {vendor_code}: {e}")
            self.error_count += 1
            return None
    
    async def scrape_with_checkpoints(self, restaurants: List[Dict], checkpoint_file: str = "checkpoint.json"):
        """
        Scrape with incremental saves for resume capability
        """
        print(f"\n{'='*60}")
        print(f"🍽️  Foodpanda Robust Menu Scraper")
        print(f"{'='*60}")
        print(f"📊 Total Restaurants: {len(restaurants)}")
        print(f"⏱️  Strategy: Slow & steady (2-5s delay)")
        print(f"💾 Checkpoint file: {checkpoint_file}")
        print(f"{'='*60}\n")
        
        # Load checkpoint if exists
        completed_codes = set()
        all_menus = []
        
        try:
            with open(checkpoint_file, 'r', encoding='utf-8') as f:
                checkpoint = json.load(f)
                completed_codes = set(checkpoint.get('completed_codes', []))
                all_menus = checkpoint.get('menus', [])
                print(f"📂 Resuming from checkpoint: {len(completed_codes)} already completed\n")
        except FileNotFoundError:
            print(f"📂 Starting fresh scrape\n")
        
        # Filter out already completed
        pending_restaurants = [r for r in restaurants if r.get('code') not in completed_codes]
        print(f"🎯 Restaurants to scrape: {len(pending_restaurants)}\n")
        
        start_time = time.time()
        
        for i, restaurant in enumerate(pending_restaurants, 1):
            vendor_code = restaurant.get('code', '')
            restaurant_name = restaurant.get('name', '')
            
            if not vendor_code:
                continue
            
            # Progress display
            elapsed = time.time() - start_time
            rate = i / elapsed if elapsed > 0 else 0
            eta_seconds = (len(pending_restaurants) - i) / rate if rate > 0 else 0
            eta_minutes = eta_seconds / 60
            
            print(f"  📥 [{len(all_menus)+1}/{len(restaurants)}] {restaurant_name[:50]} ({vendor_code})")
            print(f"     ⏱️  ETA: {eta_minutes:.1f} min | Rate: {rate*60:.1f}/min | Delay: {self.current_delay:.1f}s")
            
            menu_data = await self.fetch_restaurant_menu(vendor_code, restaurant_name)
            
            if menu_data:
                all_menus.append(menu_data)
                completed_codes.add(vendor_code)
                print(f"     ✅ {menu_data['total_categories']} categories, {menu_data['total_items']} items")
                
                # Save checkpoint every 10 restaurants
                if len(all_menus) % 10 == 0:
                    with open(checkpoint_file, 'w', encoding='utf-8') as f:
                        json.dump({
                            'completed_codes': list(completed_codes),
                            'menus': all_menus
                        }, f)
                    print(f"     💾 Checkpoint saved ({len(all_menus)} total)")
        
        # Final save
        with open(checkpoint_file, 'w', encoding='utf-8') as f:
            json.dump({
                'completed_codes': list(completed_codes),
                'menus': all_menus
            }, f)
        
        print(f"\n{'='*60}")
        print(f"✅ Scraping Complete!")
        print(f"{'='*60}")
        print(f"✅ Success: {self.success_count}/{len(restaurants)}")
        print(f"❌ Errors: {self.error_count}")
        print(f"📊 Total Menu Items: {self.total_menu_items}")
        print(f"⏱️  Total Time: {(time.time() - start_time)/60:.1f} minutes")
        print(f"{'='*60}\n")
        
        return all_menus
    
    def save_results(self, menus: List[Dict], output_prefix: str = "foodpanda_menus_complete"):
        """Save final results"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Full JSON
        json_file = f"{output_prefix}_{timestamp}.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(menus, f, ensure_ascii=False, indent=2)
        print(f"💾 Full data: {json_file}")
        
        # Flat CSV
        flat_data = []
        for menu in menus:
            restaurant = menu['restaurant']
            for category in menu['menu']:
                for item in category['items']:
                    flat_data.append({
                        'vendor_code': restaurant['vendor_code'],
                        'restaurant_name': restaurant['name'],
                        'restaurant_rating': restaurant['rating'],
                        'restaurant_reviews': restaurant['review_count'],
                        'restaurant_city': restaurant['city'],
                        'category_name': category['category_name'],
                        'item_name': item['name'],
                        'price': item['price'],
                        'price_value': item['price_value'],
                        'description': item['description'],
                        'is_sold_out': item['is_sold_out']
                    })
        
        csv_file = f"{output_prefix}_{timestamp}.csv"
        df = pd.DataFrame(flat_data)
        df.to_csv(csv_file, index=False, encoding='utf-8')
        print(f"📊 Flat CSV: {csv_file} ({len(flat_data)} items)")


async def main():
    """Main execution"""
    
    # Load Phase 1 data
    print("📂 Loading restaurants...")
    import glob
    json_files = glob.glob("foodpanda_restaurants_*.json")
    if not json_files:
        print("❌ No restaurant data found!")
        return
    
    latest_file = max(json_files, key=lambda x: x.split('_')[-1])
    print(f"   Using: {latest_file}\n")
    
    with open(latest_file, 'r', encoding='utf-8') as f:
        restaurants = json.load(f)
    
    # Initialize scraper
    scraper = ImprovedFoodpandaMenuScraper()
    
    # Scrape with checkpoints
    menus = await scraper.scrape_with_checkpoints(restaurants)
    
    # Save final results
    if menus:
        scraper.save_results(menus)


if __name__ == "__main__":
    asyncio.run(main())
