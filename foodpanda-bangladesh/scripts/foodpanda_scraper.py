"""
Foodpanda Philippines Restaurant Scraper
Using Hidden API with curl_cffi and async concurrency

Phase 1: Scrape all restaurant listings
Phase 2: Scrape individual restaurant menus (to be implemented)
"""

import asyncio
import json
import pandas as pd
from datetime import datetime
from curl_cffi import requests as curl_requests
from typing import List, Dict
import time


class FoodpandaScraper:
    """Scraper for Foodpanda Philippines using hidden API"""
    
    def __init__(self, city_id="2020706", country="ph"):
        """
        Initialize scraper
        
        Args:
            city_id: City ID (default: 2020706 for Manila)
            country: Country code (default: ph for Philippines)
        """
        self.base_url = "https://ph.fd-api.com/vendors-gateway/api/v1/pandora/vendors"
        self.city_id = city_id
        self.country = country
        
        # Headers from curl conversion
        self.headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9,hi;q=0.8,es;q=0.7,ar;q=0.6,ko;q=0.5',
            'Connection': 'keep-alive',
            'Origin': 'https://www.foodpanda.ph',
            'Referer': 'https://www.foodpanda.ph/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'cross-site',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            'X-FP-API-KEY': 'volo',
            'dps-session-id': '',
            'perseus-client-id': '1765950567428.247100920987977000.9sva3bo7sy',
            'perseus-session-id': '1765949338067.936898593407816248.s03fcxolyd',
            'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'user-logged-in': 'false',
            'x-disco-client-id': 'pd-microfrontend/web-acquisition',
        }
    
    async def fetch_page(self, offset: int, limit: int = 48) -> Dict:
        """
        Fetch a single page of restaurant listings
        
        Args:
            offset: Starting offset for pagination
            limit: Number of results per page
            
        Returns:
            API response as dictionary
        """
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
            # Use curl_cffi with async session
            # curl_cffi mimics real browser behavior better than requests
            response = curl_requests.get(
                self.base_url,
                params=params,
                headers=self.headers,
                impersonate="chrome110",  # Impersonate Chrome browser
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Error: Status code {response.status_code} for offset {offset}")
                return None
                
        except Exception as e:
            print(f"Error fetching offset {offset}: {e}")
            return None
    
    async def fetch_all_listings(self, max_results: int = None, limit_per_page: int = 48) -> List[Dict]:
        """
        Fetch all restaurant listings with pagination
        
        Args:
            max_results: Maximum number of results to fetch (None for all)
            limit_per_page: Results per page
            
        Returns:
            List of all restaurant data
        """
        print(f"\n{'='*60}")
        print(f"🍽️  Foodpanda Philippines - Restaurant Scraper")
        print(f"{'='*60}")
        print(f"📍 City ID: {self.city_id}")
        print(f"🎯 Fetching listings...")
        print(f"{'='*60}\n")
        
        all_restaurants = []
        offset = 0
        page = 1
        
        while True:
            print(f"📄 Fetching page {page} (offset: {offset})...")
            
            # Fetch page
            data = await self.fetch_page(offset, limit_per_page)
            
            if not data:
                print("⚠️  No data received, stopping...")
                break
            
            # Extract vendor data
            vendors = data.get('data', {}).get('items', [])
            
            if not vendors:
                print("✅ No more restaurants found, reached the end")
                break
            
            # Add vendors to collection
            all_restaurants.extend(vendors)
            print(f"   ✓ Found {len(vendors)} restaurants (Total: {len(all_restaurants)})")
            
            # Check if we've reached max_results
            if max_results and len(all_restaurants) >= max_results:
                all_restaurants = all_restaurants[:max_results]
                print(f"✅ Reached target of {max_results} restaurants")
                break
            
            # Check if there are more pages
            # If we got less than limit, we've reached the end
            if len(vendors) < limit_per_page:
                print("✅ Reached last page")
                break
            
            # Move to next page
            offset += limit_per_page
            page += 1
            
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.5)
        
        print(f"\n{'='*60}")
        print(f"✅ Total Restaurants Fetched: {len(all_restaurants)}")
        print(f"{'='*60}\n")
        
        return all_restaurants
    
    def parse_restaurant_data(self, vendors: List[Dict]) -> List[Dict]:
        """
        Parse vendor data into clean restaurant information
        
        Args:
            vendors: Raw vendor data from API
            
        Returns:
            List of parsed restaurant dictionaries
        """
        restaurants = []
        
        for vendor in vendors:
            try:
                # Safely get city name
                city_name = ''
                if isinstance(vendor.get('city'), dict):
                    city_name = vendor['city'].get('name', '')
                
                # Safely get chain info
                chain_name = ''
                if isinstance(vendor.get('chain'), dict):
                    chain_name = vendor['chain'].get('name', '')
                
                # Safely get cuisine names
                cuisines_list = []
                if isinstance(vendor.get('cuisines'), list):
                    cuisines_list = [c.get('name', '') for c in vendor['cuisines'] if isinstance(c, dict)]
                cuisines_str = ', '.join(cuisines_list)
                
                # Safely get metadata
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
                print(f"  ⚠️  Error parsing restaurant: {e}")
                # Print the vendor object for debugging
                import json
                print(f"  Debug - vendor type: {type(vendor)}")
                if isinstance(vendor, dict):
                    print(f"  Debug - vendor keys: {list(vendor.keys())[:10]}")
                continue
        
        return restaurants
    
    def save_results(self, restaurants: List[Dict], output_prefix: str = "foodpanda_restaurants"):
        """
        Save results to JSON and CSV
        
        Args:
            restaurants: List of restaurant dictionaries
            output_prefix: Prefix for output files
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save to JSON
        json_file = f"{output_prefix}_{timestamp}.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(restaurants, f, ensure_ascii=False, indent=2)
        print(f"💾 JSON saved to: {json_file}")
        
        # Save to CSV
        csv_file = f"{output_prefix}_{timestamp}.csv"
        df = pd.DataFrame(restaurants)
        df.to_csv(csv_file, index=False, encoding='utf-8')
        print(f"📊 CSV saved to: {csv_file}")
        
        # Print summary statistics
        print(f"\n📊 Summary Statistics:")
        print(f"   Total Restaurants: {len(restaurants)}")
        if restaurants:
            ratings = [r['rating'] for r in restaurants if r['rating'] and r['rating'] > 0]
            if ratings:
                avg_rating = sum(ratings) / len(ratings)
                print(f"   Average Rating: {avg_rating:.2f}")
                print(f"   Restaurants with Ratings: {len(ratings)}")
            
            with_delivery = sum(1 for r in restaurants if r.get('is_delivery_enabled'))
            with_pickup = sum(1 for r in restaurants if r.get('is_pickup_enabled'))
            with_discount = sum(1 for r in restaurants if r.get('has_discount'))
            print(f"   Delivery Available: {with_delivery}")
            print(f"   Pickup Available: {with_pickup}")
            print(f"   With Discounts: {with_discount}")
            
            # Top cuisines
            cuisines = {}
            for r in restaurants:
                if r.get('cuisines'):
                    for cuisine in r['cuisines'].split(', '):
                        cuisine = cuisine.strip()
                        if cuisine:
                            cuisines[cuisine] = cuisines.get(cuisine, 0) + 1
            
            if cuisines:
                print(f"\n   Top 10 Cuisines:")
                for cuisine, count in sorted(cuisines.items(), key=lambda x: x[1], reverse=True)[:10]:
                    print(f"     - {cuisine}: {count}")
            
            # Top cities
            cities = {}
            for r in restaurants:
                if r.get('city'):
                    cities[r['city']] = cities.get(r['city'], 0) + 1
            
            if cities:
                print(f"\n   Top Cities:")
                for city, count in sorted(cities.items(), key=lambda x: x[1], reverse=True)[:5]:
                    print(f"     - {city}: {count}")


async def main():
    """Main execution function"""
    
    # Initialize scraper
    scraper = FoodpandaScraper(city_id="2020706", country="ph")
    
    # Fetch all listings (set max_results=None for all, or specify a number)
    raw_vendors = await scraper.fetch_all_listings(max_results=None)
    
    if not raw_vendors:
        print("❌ No data fetched!")
        return
    
    # Parse the data
    print("🔄 Parsing restaurant data...")
    restaurants = scraper.parse_restaurant_data(raw_vendors)
    
    # Save results
    scraper.save_results(restaurants)
    
    # Display sample
    print(f"\n📝 Sample Restaurant Data (first 3):")
    for i, r in enumerate(restaurants[:3], 1):
        print(f"\n{i}. {r['name']}")
        print(f"   Rating: ⭐{r['rating']} ({r['review_count']} reviews)")
        print(f"   Cuisines: {r['cuisines']}")
        print(f"   Delivery: {'Yes' if r['is_delivery_enabled'] else 'No'} | Pickup: {'Yes' if r['is_pickup_enabled'] else 'No'}")
        print(f"   Distance: {r['distance_km']:.2f} km | City: {r['city']}")


if __name__ == "__main__":
    asyncio.run(main())
