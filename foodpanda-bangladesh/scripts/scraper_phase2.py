"""
Foodpanda Menu Scraper - Multi-City Version (Phase 2)
Uses Oxylabs proxy for speed, includes logging and checkpoints

Usage:
    py scraper_phase2.py --city cebu
"""

import asyncio
import os
import json
import argparse
import pandas as pd
from datetime import datetime
from curl_cffi import requests as curl_requests
from typing import Dict, List, Set
from pathlib import Path
from loguru import logger
import random
import glob


class ProxyMenuScraper:
    """Multi-city menu scraper with proxy support"""
    
    def __init__(self, city_name: str, proxy_username: str, proxy_password: str, api_domain: str = "ph.fd-api.com", web_domain: str = "www.foodpanda.ph", currency: str = "PHP", max_concurrent: int = 20):
        self.city_name = city_name
        self.proxy_username = proxy_username
        self.proxy_password = proxy_password
        self.api_domain = api_domain
        self.web_domain = web_domain
        self.currency = currency
        self.max_concurrent = max_concurrent
        self.proxy_host = "pr.oxylabs.io:7777"
        self.countries = ['PH', 'US', 'SG', 'MY', 'TH', 'ID', 'BD']
        
        self.api_url_template = f"https://{self.api_domain}/api/v5/vendors/{{vendor_code}}"
        
        self.base_headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9,hi;q=0.8,es;q=0.7,ar;q=0.6,ko;q=0.5,fr;q=0.4',
            'Connection': 'keep-alive',
            'Origin': f'https://{self.web_domain}',
            'Referer': f'https://{self.web_domain}/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'cross-site',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
            'X-FP-API-KEY': 'volo',
            'X-PD-Language-ID': '1',
            'dps-session-id': '',
            'perseus-client-id': '1775715674181.094153446061380756.7re61yfif4',
            'perseus-session-id': '1775723782188.143293031528117312.c5y3tim0x7',
            'sec-ch-ua': '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
        
        # Stats
        self.success_count = 0
        self.error_count = 0
        self.total_menu_items = 0
        
        # Checkpoint
        self.completed_codes: Set[str] = set()
        self.all_menus: List[Dict] = []
        self.save_interval = 20
    
    def get_proxy_url(self, country: str = None) -> str:
        if country is None:
            country = random.choice(self.countries)
        return f"http://customer-{self.proxy_username}-cc-{country}:{self.proxy_password}@{self.proxy_host}"
    
    def load_checkpoint(self, checkpoint_file: Path) -> bool:
        try:
            if checkpoint_file.exists():
                with open(checkpoint_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.completed_codes = set(data.get('completed_codes', []))
                    self.all_menus = data.get('menus', [])
                    logger.info(f"ðŸ“‚ Loaded checkpoint: {len(self.completed_codes)} done")
                    return True
        except Exception as e:
            logger.warning(f"Could not load checkpoint: {e}")
        return False
    
    def save_checkpoint(self, checkpoint_file: Path):
        try:
            with open(checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'completed_codes': list(self.completed_codes),
                    'menus': self.all_menus,
                    'timestamp': datetime.now().isoformat()
                }, f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
    
    async def fetch_restaurant_menu(self, vendor_code: str, restaurant_name: str) -> Dict:
        api_url = self.api_url_template.format(vendor_code=vendor_code)
        
        params = {
            'include': 'menus,bundles,multiple_discounts',
            'language_id': '1',
            'opening_type': 'delivery',
            'basket_currency': self.currency
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
                logger.warning(f"API Failed: {vendor_code} - HTTP {response.status_code}")
                self.error_count += 1
                return None
            
            data = response.json()
            vendor_data = data.get('data', {})
            
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
                    category_data = {'category_name': category.get('name', ''), 'items': []}
                    
                    for product in category.get('products', []):
                        variations = product.get('product_variations', [])
                        price = variations[0].get('price', 0) if variations else 0
                        
                        category_data['items'].append({
                            'name': product.get('name', ''),
                            'price': f"{self.currency} {price}" if price > 0 else "",
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
            logger.error(f"Exception: {vendor_code} - {e}")
            self.error_count += 1
            return None
    
    async def scrape_all_menus(self, restaurants: List[Dict], output_dir: Path) -> List[Dict]:
        logger.info(f"\n{'='*60}")
        logger.info(f"ðŸš€ Foodpanda Menu Scraper - Phase 2")
        logger.info(f"{'='*60}")
        logger.info(f"ðŸ“ City: {self.city_name}")
        logger.info(f"ðŸ“Š Total Restaurants: {len(restaurants)}")
        logger.info(f"âš¡ Max Concurrent: {self.max_concurrent}")
        logger.info(f"ðŸŒ Proxy: Oxylabs Residential")
        logger.info(f"{'='*60}\n")
        
        checkpoint_file = output_dir / "menu_checkpoint.json"
        self.load_checkpoint(checkpoint_file)
        
        # Filter already completed
        pending = [r for r in restaurants if r.get('code') not in self.completed_codes]
        logger.info(f"ðŸŽ¯ Already done: {len(self.completed_codes)}, Pending: {len(pending)}\n")
        
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
                
                if index % 50 == 0:
                    logger.info(f"ðŸ“¥ [{len(self.completed_codes)+1}/{len(restaurants)}] {restaurant_name[:40]}")
                
                menu_data = await self.fetch_restaurant_menu(vendor_code, restaurant_name)
                
                if menu_data:
                    self.all_menus.append(menu_data)
                    self.completed_codes.add(vendor_code)
                    
                    if len(self.all_menus) % self.save_interval == 0:
                        self.save_checkpoint(checkpoint_file)
                        logger.info(f"ðŸ’¾ Checkpoint: {len(self.all_menus)} saved")
                
                return menu_data
        
        tasks = [fetch_with_semaphore(r, i+1) for i, r in enumerate(pending)]
        
        import time
        start = time.time()
        await asyncio.gather(*tasks)
        elapsed = time.time() - start
        
        # Final save
        self.save_checkpoint(checkpoint_file)
        
        logger.info(f"\n{'='*60}")
        logger.info(f"âœ… Scraping Complete!")
        logger.info(f"{'='*60}")
        logger.info(f"âœ… Success: {self.success_count}/{len(restaurants)}")
        logger.info(f"âŒ Errors: {self.error_count}")
        logger.info(f"ðŸ“Š Total Menu Items: {self.total_menu_items}")
        logger.info(f"â±ï¸  Time: {elapsed/60:.1f} min ({len(pending)/elapsed*60:.1f}/min)")
        logger.info(f"{'='*60}\n")
        
        return self.all_menus
    
    def save_results(self, menus: List[Dict], output_dir: Path):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # JSON
        json_file = output_dir / f"menus_{timestamp}.json"
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
                        'restaurant_city': restaurant['city'],
                        'category_name': category['category_name'],
                        'item_name': item['name'],
                        'price': item['price'],
                        'price_value': item['price_value'],
                        'description': item['description'],
                        'is_sold_out': item['is_sold_out']
                    })
        
        csv_file = output_dir / f"menus_{timestamp}.csv"
        df = pd.DataFrame(flat_data)
        df.to_csv(csv_file, index=False, encoding='utf-8')
        logger.info(f"ðŸ“Š CSV: {csv_file} ({len(flat_data)} items)")


async def main():
    parser = argparse.ArgumentParser(description="Foodpanda Menu Scraper - Phase 2")
    parser.add_argument("--city", type=str, required=True, help="City key (e.g., cebu)")
    args = parser.parse_args()
    
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    # Setup logging
    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level="INFO", format="{message}")
    
    # Proxy credentials
    PROXY_USERNAME = os.getenv("OXYLABS_PROXY_USERNAME", "")
    PROXY_PASSWORD = os.getenv("OXYLABS_PROXY_PASSWORD", "")
    config_path = Path(__file__).parent.parent / "config" / "cities.json"
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    city_config = config['cities'][args.city]
    logger.info(f"ðŸŒ City: {city_config['city_name']}")
    
    # Find latest restaurant file
    data_dir = Path(__file__).parent.parent / "data" / args.city
    restaurant_files = list(data_dir.glob("restaurants_*.json"))
    
    if not restaurant_files:
        logger.error(f"âŒ No restaurant data found in {data_dir}")
        logger.error("Run Phase 1 first: py scripts/scraper_phase1.py --city " + args.city)
        return
    
    latest_file = max(restaurant_files, key=lambda x: x.stem.split('_')[-1])
    logger.info(f"ðŸ“‚ Using: {latest_file.name}\n")
    
    with open(latest_file, 'r', encoding='utf-8') as f:
        restaurants = json.load(f)
    
    # Initialize scraper
    scraper = ProxyMenuScraper(
        city_name=city_config['city_name'],
        proxy_username=PROXY_USERNAME,
        proxy_password=PROXY_PASSWORD,
        api_domain=city_config['api_domain'],
        web_domain=city_config['web_domain'],
        currency=city_config['currency'],
        max_concurrent=20
    )
    
    # Scrape
    menus = await scraper.scrape_all_menus(restaurants, data_dir)
    
    # Save
    if menus:
        scraper.save_results(menus, data_dir)


if __name__ == "__main__":
    asyncio.run(main())

