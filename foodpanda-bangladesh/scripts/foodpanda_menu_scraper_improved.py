"""
Foodpanda Menu Scraper - IMPROVED VERSION
Features:
- Loguru logging for failed APIs
- Checkpoint/resume system
- Deduplication (auto-skip completed)
- Auto-save every N restaurants
"""

import asyncio
import os
import json
import pandas as pd
from datetime import datetime
from curl_cffi import requests as curl_requests
from typing import Dict, List, Set
import random
from pathlib import Path
from loguru import logger


class ImprovedProxyFoodpandaScraper:
    """Enhanced scraper with logging, checkpoints, and deduplication"""
    
    def __init__(self, proxy_username: str, proxy_password: str, max_concurrent: int = 20):
        """Initialize with Oxylabs proxy credentials"""
        self.proxy_username = proxy_username
        self.proxy_password = proxy_password
        self.max_concurrent = max_concurrent
        self.proxy_host = "pr.oxylabs.io:7777"
        self.countries = ['PH', 'US', 'SG', 'MY', 'TH', 'ID']
        
        self.api_url_template = "https://ph.fd-api.com/api/v5/vendors/{vendor_code}"
        
        self.base_headers = {
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
        
        # Setup logging
        logger.remove()  # Remove default handler
        logger.add("foodpanda_scraper.log", 
                  format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
                  level="INFO",
                  rotation="10 MB")
        logger.add(lambda msg: print(msg, end=""), level="INFO", format="{message}")
        
        # Stats
        self.success_count = 0
        self.error_count = 0
        self.total_menu_items = 0
        
        # Checkpoint system
        self.checkpoint_file = "menu_checkpoint.json"
        self.completed_codes: Set[str] = set()
        self.all_menus: List[Dict] = []
        self.save_interval = 10  # Save every 10 restaurants
    
    def load_checkpoint(self):
        """Load previous progress if exists"""
        try:
            if Path(self.checkpoint_file).exists():
                with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.completed_codes = set(data.get('completed_codes', []))
                    self.all_menus = data.get('menus', [])
                    logger.info(f"ðŸ“‚ Loaded checkpoint: {len(self.completed_codes)} already completed")
                    return True
        except Exception as e:
            logger.warning(f"âš ï¸  Could not load checkpoint: {e}")
        return False
    
    def save_checkpoint(self):
        """Save current progress"""
        try:
            with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'completed_codes': list(self.completed_codes),
                    'menus': self.all_menus,
                    'timestamp': datetime.now().isoformat()
                }, f, ensure_ascii=False, indent=2)
            logger.debug(f"ðŸ’¾ Checkpoint saved ({len(self.all_menus)} total)")
        except Exception as e:
            logger.error(f"âŒ Failed to save checkpoint: {e}")
    
    def get_proxy_url(self, country: str = None) -> str:
        """Generate Oxylabs proxy URL with country rotation"""
        if country is None:
            country = random.choice(self.countries)
        return f"http://customer-{self.proxy_username}-cc-{country}:{self.proxy_password}@{self.proxy_host}"
    
    async def fetch_restaurant_menu(self, vendor_code: str, restaurant_name: str) -> Dict:
        """Fetch menu using rotating residential proxy"""
        api_url = self.api_url_template.format(vendor_code=vendor_code)
        
        params = {
            'include': 'menus,bundles,multiple_discounts',
            'language_id': '1',
            'opening_type': 'delivery',
            'basket_currency': 'PHP'
        }
        
        try:
            proxy = self.get_proxy_url()
            await asyncio.sleep(random.uniform(0.1, 0.3))
            
            response = curl_requests.get(
                api_url,
                params=params,
                headers=self.base_headers,
                proxies={'http': proxy, 'https': proxy},
                impersonate="chrome110",
                timeout=30
            )
            
            if response.status_code != 200:
                logger.warning(f"API Failed: {vendor_code} ({restaurant_name[:30]}) - HTTP {response.status_code}")
                self.error_count += 1
                return None
            
            data = response.json()
            vendor_data = data.get('data', {})
            
            # Parse restaurant info
            restaurant_info = {
                'vendor_code': vendor_code,
                'name': vendor_data.get('name', restaurant_name),
                'rating': vendor_data.get('rating', 0),
                'review_count': vendor_data.get('review_number', 0),
                'cuisines': [c.get('name', '') for c in vendor_data.get('cuisines', [])],
                'city': vendor_data.get('city', {}).get('name', '') if isinstance(vendor_data.get('city'), dict) else '',
            }
            
            # Parse menu
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
                            'price': f"â‚± {price}" if price > 0 else "",
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
            logger.success(f"âœ… {vendor_code}: {result['total_categories']} cat, {result['total_items']} items")
            return result
            
        except Exception as e:
            logger.error(f"Exception: {vendor_code} ({restaurant_name[:30]}) - {e}")
            self.error_count += 1
            return None
    
    async def scrape_all_fast(self, restaurants: List[Dict]) -> List[Dict]:
        """Fast concurrent scraping with deduplication"""
        logger.info(f"\n{'='*60}")
        logger.info(f"ðŸš€ Foodpanda IMPROVED Menu Scraper")
        logger.info(f"{'='*60}")
        logger.info(f"ðŸ“Š Total Restaurants: {len(restaurants)}")
        logger.info(f"âš¡ Max Concurrent: {self.max_concurrent}")
        logger.info(f"ðŸŒ Proxy: Oxylabs Residential ({len(self.countries)} countries)")
        logger.info(f"{'='*60}\n")
        
        # Load checkpoint
        self.load_checkpoint()
        
        # Filter out already completed (deduplication)
        pending = [r for r in restaurants if r.get('code') not in self.completed_codes]
        logger.info(f"ðŸŽ¯ Already completed: {len(self.completed_codes)}")
        logger.info(f"ðŸŽ¯ Pending: {len(pending)}\n")
        
        if not pending:
            logger.info("âœ… All restaurants already scraped!")
            return self.all_menus
        
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def fetch_with_semaphore(restaurant, index):
            async with semaphore:
                vendor_code = restaurant.get('code', '')
                restaurant_name = restaurant.get('name', '')
                
                if not vendor_code:
                    return None
                
                if index % 20 == 0:
                    logger.info(f"ðŸ“¥ [{len(self.completed_codes)+1}/{len(restaurants)}] {restaurant_name[:40]}")
                
                menu_data = await self.fetch_restaurant_menu(vendor_code, restaurant_name)
                
                if menu_data:
                    self.all_menus.append(menu_data)
                    self.completed_codes.add(vendor_code)
                    
                    # Auto-save every N restaurants
                    if len(self.all_menus) % self.save_interval == 0:
                        self.save_checkpoint()
                
                return menu_data
        
        # Create tasks
        tasks = [fetch_with_semaphore(restaurant, i+1) for i, restaurant in enumerate(pending)]
        
        # Execute
        import time
        start = time.time()
        await asyncio.gather(*tasks)
        elapsed = time.time() - start
        
        # Final save
        self.save_checkpoint()
        
        logger.info(f"\n{'='*60}")
        logger.info(f"âœ… Scraping Complete!")
        logger.info(f"{'='*60}")
        logger.info(f"âœ… Success: {self.success_count}/{len(restaurants)} ({self.success_count/len(restaurants)*100:.1f}%)")
        logger.info(f"âŒ Errors: {self.error_count}")
        logger.info(f"ðŸ“Š Total Menu Items: {self.total_menu_items}")
        logger.info(f"â±ï¸  Total Time: {elapsed/60:.1f} minutes")
        logger.info(f"âš¡ Speed: {len(pending)/elapsed*60:.1f} restaurants/min")
        logger.info(f"{'='*60}\n")
        
        return self.all_menus
    
    def save_results(self, menus: List[Dict], output_prefix: str = "foodpanda_menus_final"):
        """Save final results"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # JSON
        json_file = f"{output_prefix}_{timestamp}.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(menus, f, ensure_ascii=False, indent=2)
        logger.info(f"ðŸ’¾ JSON: {json_file}")
        
        # CSV
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
        logger.info(f"ðŸ“Š CSV: {csv_file} ({len(flat_data)} items)")


async def main():
    """Main execution"""
    
    PROXY_USERNAME = os.getenv("OXYLABS_PROXY_USERNAME", "")
    PROXY_PASSWORD = os.getenv("OXYLABS_PROXY_PASSWORD", "")
    logger.info("ðŸ“‚ Loading restaurants...")
    import glob
    json_files = glob.glob("foodpanda_restaurants_*.json")
    if not json_files:
        logger.error("âŒ No restaurant data found!")
        return
    
    latest_file = max(json_files, key=lambda x: x.split('_')[-1])
    logger.info(f"   Using: {latest_file}\n")
    
    with open(latest_file, 'r', encoding='utf-8') as f:
        restaurants = json.load(f)
    
    # Initialize scraper
    scraper = ImprovedProxyFoodpandaScraper(
        proxy_username=PROXY_USERNAME,
        proxy_password=PROXY_PASSWORD,
        max_concurrent=20
    )
    
    # Scrape
    menus = await scraper.scrape_all_fast(restaurants)
    
    # Save
    if menus:
        scraper.save_results(menus)


if __name__ == "__main__":
    asyncio.run(main())

