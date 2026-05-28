"""
Foodpanda Restaurant Scraper - Multi-City Version
Phase 1: Scrape restaurant listings for any city

Usage:
    py scraper_phase1.py --city cebu
    py scraper_phase1.py --city bacoor
"""

import asyncio
import json
import argparse
import pandas as pd
from datetime import datetime
from curl_cffi import requests as curl_requests
from typing import List, Dict
from pathlib import Path
from loguru import logger


class FoodpandaRestaurantScraper:
    """Multi-city restaurant listing scraper"""
    
    def __init__(self, city_id: str, city_name: str, country: str = "ph", api_domain: str = "ph.fd-api.com", web_domain: str = "www.foodpanda.ph"):
        self.base_url = f"https://{api_domain}/vendors-gateway/api/v1/pandora/vendors"
        self.city_id = city_id
        self.city_name = city_name
        self.country = country
        self.api_domain = api_domain
        self.web_domain = web_domain
        
        # Headers from the curl conversion
        self.headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9,hi;q=0.8,es;q=0.7,ar;q=0.6,ko;q=0.5',
            'Connection': 'keep-alive',
            'Origin': f'https://{self.web_domain}',
            'Referer': f'https://{self.web_domain}/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'cross-site',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            'X-FP-API-KEY': 'volo',
            'dps-session-id': '',
            'perseus-client-id': '1766034373990.119171180769509435.1kpn21ejmt',
            'perseus-session-id': '1766034373990.884164461226278196.msimm1umei',
            'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand);v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'user-logged-in': 'false',
            'x-disco-client-id': 'pd-microfrontend/web-acquisition',
        }
    
    async def fetch_page(self, offset: int, limit: int = 48) -> Dict:
        """Fetch a single page of restaurant listings"""
        params = {
            'configuration': '',
            'country': self.country,
            'city_id': self.city_id,
            'include': '',
            'language_id': '1',
            'sort': '',
            'offset': str(offset),
            'limit': str(limit),
            'vertical': 'restaurants',
        }
        
        try:
            response = curl_requests.get(
                self.base_url,
                params=params,
                headers=self.headers,
                impersonate="chrome110",
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"HTTP {response.status_code} for offset {offset}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching offset {offset}: {e}")
            return None
    
    async def fetch_all_listings(self, max_results: int = None, limit_per_page: int = 48) -> List[Dict]:
        """Fetch all restaurant listings with pagination"""
        logger.info(f"\n{'='*60}")
        logger.info(f"🍽️  Foodpanda Restaurant Scraper - Phase 1")
        logger.info(f"{'='*60}")
        logger.info(f"📍 City: {self.city_name} (ID: {self.city_id})")
        logger.info(f"🎯 Fetching listings...")
        logger.info(f"{'='*60}\n")
        
        all_restaurants = []
        offset = 0
        page = 1
        
        while True:
            logger.info(f"📄 Fetching page {page} (offset: {offset})...")
            
            data = await self.fetch_page(offset, limit_per_page)
            
            if not data:
                logger.warning("No data received, stopping...")
                break
            
            vendors = data.get('data', {}).get('items', [])
            
            if not vendors:
                logger.success("No more restaurants found, reached the end")
                break
            
            all_restaurants.extend(vendors)
            logger.info(f"   ✓ Found {len(vendors)} restaurants (Total: {len(all_restaurants)})")
            
            if max_results and len(all_restaurants) >= max_results:
                all_restaurants = all_restaurants[:max_results]
                logger.success(f"Reached target of {max_results} restaurants")
                break
            
            if len(vendors) < limit_per_page:
                logger.success("Reached last page")
                break
            
            offset += limit_per_page
            page += 1
            
            await asyncio.sleep(0.5)
        
        logger.info(f"\n{'='*60}")
        logger.info(f"✅ Total Restaurants Fetched: {len(all_restaurants)}")
        logger.info(f"{'='*60}\n")
        
        return all_restaurants
    
    def parse_restaurant_data(self, vendors: List[Dict]) -> List[Dict]:
        """Parse vendor data into clean restaurant information"""
        restaurants = []
        
        for vendor in vendors:
            try:
                city_name = ''
                if isinstance(vendor.get('city'), dict):
                    city_name = vendor['city'].get('name', '')
                
                chain_name = ''
                if isinstance(vendor.get('chain'), dict):
                    chain_name = vendor['chain'].get('name', '')
                
                cuisines_list = []
                if isinstance(vendor.get('cuisines'), list):
                    cuisines_list = [c.get('name', '') for c in vendor['cuisines'] if isinstance(c, dict)]
                cuisines_str = ', '.join(cuisines_list)
                
                metadata = vendor.get('metadata', {})
                if not isinstance(metadata, dict):
                    metadata = {}
                
                restaurant = {
                    'id': vendor.get('id', ''),
                    'code': vendor.get('code', ''),
                    'name': vendor.get('name', ''),
                    'description': vendor.get('description', ''),
                    'rating': vendor.get('rating', 0),
                    'review_count': vendor.get('review_number', 0),
                    'cuisines': cuisines_str,
                    'chain_name': chain_name,
                    'minimum_order': vendor.get('minimum_order_amount', 0),
                    'minimum_delivery_fee': vendor.get('minimum_delivery_fee', 0),
                    'minimum_delivery_time': vendor.get('minimum_delivery_time', 0),
                    'minimum_pickup_time': vendor.get('minimum_pickup_time', 0),
                    'distance_km': vendor.get('distance', 0),
                    'is_new': vendor.get('is_new', False),
                    'is_pickup_enabled': vendor.get('is_pickup_enabled', False),
                    'is_delivery_enabled': vendor.get('is_delivery_enabled', False),
                    'is_preorder_enabled': vendor.get('is_preorder_enabled', False),
                    'latitude': vendor.get('latitude', ''),
                    'longitude': vendor.get('longitude', ''),
                    'address': vendor.get('address', ''),
                    'address_line2': vendor.get('address_line2', ''),
                    'city': city_name,
                    'post_code': vendor.get('post_code', ''),
                    'budget_range': vendor.get('budget', ''),
                    'hero_image': vendor.get('hero_image', ''),
                    'hero_listing_image': vendor.get('hero_listing_image', ''),
                    'logo_url': vendor.get('logo', ''),
                    'is_active': vendor.get('is_active', False),
                    'is_premium': vendor.get('is_premium', False),
                    'is_promoted': vendor.get('is_promoted', False),
                    'has_online_payment': vendor.get('has_online_payment', False),
                    'has_discount': metadata.get('has_discount', False),
                    'is_delivery_available': metadata.get('is_delivery_available', False),
                    'is_pickup_available': metadata.get('is_pickup_available', False),
                    'is_temporary_closed': metadata.get('is_temporary_closed', False),
                    'redirection_url': vendor.get('redirection_url', ''),
                    'web_path': vendor.get('web_path', ''),
                    'url_key': vendor.get('url_key', ''),
                    'scraped_at': datetime.now().isoformat()
                }
                restaurants.append(restaurant)
            except Exception as e:
                logger.error(f"Error parsing restaurant: {e}")
                continue
        
        return restaurants
    
    def save_results(self, restaurants: List[Dict], output_dir: str):
        """Save results to JSON and CSV in city-specific folder"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save to JSON
        json_file = output_path / f"restaurants_{timestamp}.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(restaurants, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 JSON saved: {json_file}")
        
        # Save to CSV
        csv_file = output_path / f"restaurants_{timestamp}.csv"
        df = pd.DataFrame(restaurants)
        df.to_csv(csv_file, index=False, encoding='utf-8')
        logger.info(f"📊 CSV saved: {csv_file}")
        
        # Print summary
        logger.info(f"\n📊 Summary Statistics:")
        logger.info(f"   Total Restaurants: {len(restaurants)}")
        if restaurants:
            ratings = [r['rating'] for r in restaurants if r['rating'] and r['rating'] > 0]
            if ratings:
                avg_rating = sum(ratings) / len(ratings)
                logger.info(f"   Average Rating: {avg_rating:.2f}")
            
            with_delivery = sum(1 for r in restaurants if r.get('is_delivery_enabled'))
            with_pickup = sum(1 for r in restaurants if r.get('is_pickup_enabled'))
            logger.info(f"   Delivery Available: {with_delivery}")
            logger.info(f"   Pickup Available: {with_pickup}")


def load_city_config(city_key: str) -> Dict:
    """Load city configuration from cities.json"""
    config_path = Path(__file__).parent.parent / "config" / "cities.json"
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    if city_key not in config['cities']:
        raise ValueError(f"City '{city_key}' not found in config. Available: {list(config['cities'].keys())}")
    
    return config['cities'][city_key]


async def main():
    """Main execution"""
    parser = argparse.ArgumentParser(description="Foodpanda Restaurant Scraper - Phase 1")
    parser.add_argument("--city", type=str, required=True, help="City key (e.g., cebu, bacoor)")
    args = parser.parse_args()
    
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    # Setup logging
    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level="INFO", format="{message}")
    
    # Load city config
    city_config = load_city_config(args.city)
    
    logger.info(f"🌍 Selected city: {city_config['city_name']}")
    
    # Initialize scraper
    scraper = FoodpandaRestaurantScraper(
        city_id=city_config['city_id'],
        city_name=city_config['city_name'],
        country=city_config['country'],
        api_domain=city_config['api_domain'],
        web_domain=city_config['web_domain']
    )
    
    # Fetch all listings
    raw_vendors = await scraper.fetch_all_listings(max_results=None)
    
    if not raw_vendors:
        logger.error("❌ No data fetched!")
        return
    
    # Parse data
    logger.info("🔄 Parsing restaurant data...")
    restaurants = scraper.parse_restaurant_data(raw_vendors)
    
    # Save results
    output_dir = Path(__file__).parent.parent / "data" / args.city
    scraper.save_results(restaurants, str(output_dir))
    
    # Sample output
    logger.info(f"\n📝 Sample Restaurant Data (first 3):")
    for i, r in enumerate(restaurants[:3], 1):
        logger.info(f"\n{i}. {r['name']}")
        logger.info(f"   Rating: ⭐{r['rating']} ({r['review_count']} reviews)")
        logger.info(f"   Cuisines: {r['cuisines']}")
        logger.info(f"   City: {r['city']}")


if __name__ == "__main__":
    asyncio.run(main())
