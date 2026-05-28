"""
Foodpanda Full Menu Scraper - Phase 2 (Production)
Scrape menus for all restaurants using async concurrency

Reads restaurants from Phase 1 JSON and scrapes menu for each vendor
"""

import asyncio
import json
import pandas as pd
from datetime import datetime
from curl_cffi import requests as curl_requests
from typing import Dict, List
import time


class FoodpandaMenuScraper:
    """Full-scale menu scraper with async concurrency"""
    
    def __init__(self, max_concurrent=10, delay_between_requests=0.5):
        """
        Initialize scraper
        
        Args:
            max_concurrent: Maximum concurrent requests
            delay_between_requests: Delay between requests in seconds
        """
        self.max_concurrent = max_concurrent
        self.delay_between_requests = delay_between_requests
        
        # API endpoint pattern
        self.api_url_template = "https://ph.fd-api.com/api/v5/vendors/{vendor_code}"
        
        # Headers (from working test)
        self.headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Api-Version': '7',
            'Authorization': '',
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
        
    async def fetch_restaurant_menu(self, vendor_code: str, restaurant_name: str = "") -> Dict:
        """
        Fetch menu for a single restaurant
        
        Args:
            vendor_code: Restaurant vendor code
            restaurant_name: Restaurant name (for logging)
            
        Returns:
            Menu data dictionary or None if failed
        """
        api_url = self.api_url_template.format(vendor_code=vendor_code)
        
        params = {
            'include': 'menus,bundles,multiple_discounts',
            'language_id': '1',
            'opening_type': 'delivery',
            'basket_currency': 'PHP'
        }
        
        try:
            # Small delay to avoid rate limiting
            await asyncio.sleep(self.delay_between_requests)
            
            response = curl_requests.get(
                api_url,
                params=params,
                headers=self.headers,
                impersonate="chrome110",
                timeout=30
            )
            
            if response.status_code != 200:
                print(f"  ⚠️  {vendor_code} ({restaurant_name}): HTTP {response.status_code}")
                self.error_count += 1
                return None
            
            data = response.json()
            vendor_data = data.get('data', {})
            
            # Extract restaurant info
            restaurant_info = {
                'vendor_code': vendor_code,
                'name': vendor_data.get('name', restaurant_name),
                'rating': vendor_data.get('rating', 0),
                'review_count': vendor_data.get('review_number', 0),
                'cuisines': [c.get('name', '') for c in vendor_data.get('cuisines', [])],
                'address': vendor_data.get('address', ''),
                'city': vendor_data.get('city', {}).get('name', '') if isinstance(vendor_data.get('city'), dict) else '',
            }
            
            # Extract menu categories
            menus = vendor_data.get('menus', [])
            menu_categories = []
            
            if menus:
                categories = menus[0].get('menu_categories', [])
                
                for category in categories:
                    category_data = {
                        'category_id': category.get('id'),
                        'category_name': category.get('name', ''),
                        'category_description': category.get('description', ''),
                        'items': []
                    }
                    
                    products = category.get('products', [])
                    
                    for product in products:
                        # Get price from product_variations
                        variations = product.get('product_variations', [])
                        price = 0
                        if variations:
                            price = variations[0].get('price', 0)
                        
                        item_data = {
                            'name': product.get('name', ''),
                            'price': f"₱ {price}" if price > 0 else "",
                            'price_value': price,
                            'description': product.get('description', ''),
                            'image_url': product.get('file_path', ''),
                            'is_sold_out': product.get('is_sold_out', False)
                        }
                        
                        category_data['items'].append(item_data)
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
            print(f"  ❌ {vendor_code} ({restaurant_name}): {e}")
            self.error_count += 1
            return None
    
    async def scrape_all_menus(self, restaurants: List[Dict]) -> List[Dict]:
        """
        Scrape menus for all restaurants with concurrency control
        
        Args:
            restaurants: List of restaurant data from Phase 1
            
        Returns:
            List of menu data
        """
        print(f"\n{'='*60}")
        print(f"🍽️  Foodpanda Full Menu Scraper")
        print(f"{'='*60}")
        print(f"📊 Total Restaurants: {len(restaurants)}")
        print(f"⚡ Max Concurrent: {self.max_concurrent}")
        print(f"⏱️  Delay: {self.delay_between_requests}s")
        print(f"{'='*60}\n")
        
        all_menus = []
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def fetch_with_semaphore(restaurant, index):
            async with semaphore:
                vendor_code = restaurant.get('code', '')
                restaurant_name = restaurant.get('name', '')
                
                if not vendor_code:
                    print(f"  ⚠️  [{index}/{len(restaurants)}] No vendor code for {restaurant_name}")
                    return None
                
                print(f"  📥 [{index}/{len(restaurants)}] {restaurant_name} ({vendor_code})")
                
                menu_data = await self.fetch_restaurant_menu(vendor_code, restaurant_name)
                
                if menu_data:
                    print(f"     ✅ {menu_data['total_categories']} categories, {menu_data['total_items']} items")
                
                return menu_data
        
        # Create tasks for all restaurants
        tasks = [
            fetch_with_semaphore(restaurant, i+1) 
            for i, restaurant in enumerate(restaurants)
        ]
        
        # Execute all tasks
        results = await asyncio.gather(*tasks)
        
        # Filter out None results
        all_menus = [r for r in results if r is not None]
        
        print(f"\n{'='*60}")
        print(f"✅ Scraping Complete!")
        print(f"{'='*60}")
        print(f"✅ Success: {self.success_count}/{len(restaurants)}")
        print(f"❌ Errors: {self.error_count}/{len(restaurants)}")
        print(f"📊 Total Menu Items: {self.total_menu_items}")
        print(f"{'='*60}\n")
        
        return all_menus
    
    def save_results(self, menus: List[Dict], output_prefix: str = "foodpanda_menus"):
        """Save menu data to JSON and CSV"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save full menu data to JSON
        json_file = f"{output_prefix}_full_{timestamp}.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(menus, f, ensure_ascii=False, indent=2)
        print(f"💾 Full menu data saved to: {json_file}")
        
        # Create flattened data for CSV (one row per menu item)
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
                        'is_sold_out': item['is_sold_out'],
                        'image_url': item['image_url']
                    })
        
        # Save to CSV
        csv_file = f"{output_prefix}_flat_{timestamp}.csv"
        df = pd.DataFrame(flat_data)
        df.to_csv(csv_file, index=False, encoding='utf-8')
        print(f"📊 Flattened CSV saved to: {csv_file}")
        print(f"   Total rows: {len(flat_data)}")
        
        # Save summary statistics
        summary_file = f"{output_prefix}_summary_{timestamp}.json"
        summary = {
            'total_restaurants': len(menus),
            'total_menu_items': len(flat_data),
            'avg_items_per_restaurant': len(flat_data) / len(menus) if menus else 0,
            'top_restaurants_by_items': [],
            'top_categories': {},
            'price_stats': {
                'min': df['price_value'].min() if not df.empty else 0,
                'max': df['price_value'].max() if not df.empty else 0,
                'avg': df['price_value'].mean() if not df.empty else 0,
                'median': df['price_value'].median() if not df.empty else 0
            }
        }
        
        # Top restaurants by menu items
        restaurant_items = df.groupby('restaurant_name')['item_name'].count().sort_values(ascending=False)
        summary['top_restaurants_by_items'] = [
            {'name': name, 'items': int(count)} 
            for name, count in restaurant_items.head(10).items()
        ]
        
        # Top categories
        category_counts = df['category_name'].value_counts()
        summary['top_categories'] = {cat: int(count) for cat, count in category_counts.head(20).items()}
        
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"📈 Summary statistics saved to: {summary_file}")


async def main():
    """Main execution"""
    
    # Load Phase 1 restaurant data
    print("📂 Loading Phase 1 restaurant data...")
    
    # Find the most recent restaurant JSON file
    import glob
    json_files = glob.glob("foodpanda_restaurants_*.json")
    if not json_files:
        print("❌ No Phase 1 restaurant data found!")
        print("   Please run the Phase 1 scraper first.")
        return
    
    latest_file = max(json_files, key=lambda x: x.split('_')[-1])
    print(f"   Using: {latest_file}")
    
    with open(latest_file, 'r', encoding='utf-8') as f:
        restaurants = json.load(f)
    
    print(f"✅ Loaded {len(restaurants)} restaurants\n")
    
    # Initialize scraper
    scraper = FoodpandaMenuScraper(
        max_concurrent=5,  # Conservative to avoid rate limiting
        delay_between_requests=0.3
    )
    
    # Scrape all menus
    menus = await scraper.scrape_all_menus(restaurants)
    
    # Save results
    if menus:
        scraper.save_results(menus)
        
        # Show sample
        print(f"\n📝 Sample Menu Data:")
        if menus:
            sample = menus[0]
            print(f"   Restaurant: {sample['restaurant']['name']}")
            print(f"   Categories: {sample['total_categories']}")
            print(f"   Total Items: {sample['total_items']}")
            if sample['menu']:
                print(f"   First Category: {sample['menu'][0]['category_name']}")
                if sample['menu'][0]['items']:
                    item = sample['menu'][0]['items'][0]
                    print(f"   Sample Item: {item['name']} - {item['price']}")
    else:
        print("\n❌ No menu data scraped!")


if __name__ == "__main__":
    asyncio.run(main())
