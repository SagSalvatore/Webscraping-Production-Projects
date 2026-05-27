import scrapy
import json
import hashlib
import re
import os
import difflib
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from datetime import datetime
from ..items import TalabatRestaurantItem, TalabatMenuItemItem


class TalabatCompleteSpider(scrapy.Spider):
    name = "talabat_complete"
    allowed_domains = ["www.talabat.com"]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.area_name = "abu-dhabi-international-airport"  # For output directory
        self.setup_output_directory()
        self.restaurant_urls = []
        self.previous_data = {}
        self.load_restaurant_urls()
        self.load_previous_data()
        
    def setup_output_directory(self):
        """Create area-specific output directory"""
        self.output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), self.area_name)
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            self.logger.info(f"Created output directory: {self.output_dir}")
    
    def load_restaurant_urls(self):
        """Load restaurant URLs from Phase 1 collection"""
        urls_file = os.path.join(self.output_dir, 'restaurant_urls.json')
        if os.path.exists(urls_file):
            try:
                with open(urls_file, 'r', encoding='utf-8') as f:
                    urls_data = json.load(f)
                    self.restaurant_urls = urls_data.get('urls', [])
                self.logger.info(f"🔗 Loaded {len(self.restaurant_urls)} restaurant URLs from Phase 1")
            except Exception as e:
                self.logger.error(f"❌ Error loading restaurant URLs: {e}")
                self.restaurant_urls = []
        else:
            self.logger.error(f"❌ No restaurant URLs found at {urls_file}")
            self.logger.error("🚨 Please run 'talabat_abu_dhabi_gate_city' spider first to collect URLs (Phase 1)")
            self.restaurant_urls = []
        
    def load_previous_data(self):
        """Load previous restaurant data for hash comparison"""
        data_file = os.path.join(self.output_dir, 'previous_data.json')
        if os.path.exists(data_file):
            try:
                with open(data_file, 'r', encoding='utf-8') as f:
                    self.previous_data = json.load(f)
                self.logger.info(f"📊 Loaded {len(self.previous_data)} previous restaurant records for comparison")
            except Exception as e:
                self.logger.error(f"❌ Error loading previous data: {e}")

    def start_requests(self):
        """Start Phase 2: Detailed scraping of collected URLs"""
        if not self.restaurant_urls:
            self.logger.error("🚨 No restaurant URLs to process. Run Phase 1 first!")
            return
        
        self.logger.info(f"🚀 Starting Phase 2: Detailed scraping of {len(self.restaurant_urls)} restaurants")
        
        for i, url in enumerate(self.restaurant_urls, 1):
            self.logger.info(f"📍 Processing restaurant {i}/{len(self.restaurant_urls)}: {url}")
            yield scrapy.Request(
                url, 
                callback=self.parse_restaurant,
                headers=self.get_stealth_headers(),
                meta={'restaurant_index': i, 'total_restaurants': len(self.restaurant_urls)}
            )

    def get_stealth_headers(self, referer=None):
        """Get comprehensive stealth headers"""
        headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Cache-Control': 'max-age=0',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin' if referer else 'none',
            'Sec-Fetch-User': '?1',
            'sec-ch-ua': '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
        if referer:
            headers['Referer'] = referer
        return headers

    def extract_restaurant_id(self, url):
        """Extract restaurant ID from URL"""
        try:
            # URL format: /restaurant/681118/restaurant-name
            match = re.search(r'/restaurant/(\d+)/', url)
            return match.group(1) if match else None
        except:
            return None

    def generate_hash(self, data):
        """Generate MD5 hash for data comparison"""
        try:
            data_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
            return hashlib.md5(data_str.encode('utf-8')).hexdigest()
        except Exception as e:
            self.logger.error(f"❌ Error generating hash: {e}")
            return ""

    def check_for_changes(self, restaurant_id, current_data):
        """Check if restaurant data has changed using difflib"""
        if restaurant_id not in self.previous_data:
            return True, {"type": "new_restaurant", "details": "First time scraping this restaurant"}
        
        previous_item = self.previous_data[restaurant_id]
        changes_detected = False
        change_summary = {
            "restaurant_id": restaurant_id,
            "restaurant_name": current_data.get('name', 'Unknown Restaurant'),
            "restaurant_url": current_data.get('url', ''),
            "timestamp": datetime.now().isoformat(),
            "changes": {}
        }
        
        # Compare basic info hash
        if current_data.get('basic_info_hash') != previous_item.get('basic_info_hash'):
            changes_detected = True
            change_summary["changes"]["basic_info"] = {
                "changed": True,
                "previous": {
                    "name": previous_item.get('name'),
                    "cuisine_type": previous_item.get('cuisine_type'),
                    "area": previous_item.get('area'),
                    "latitude": previous_item.get('latitude'),
                    "longitude": previous_item.get('longitude')
                },
                "current": {
                    "name": current_data.get('name'),
                    "cuisine_type": current_data.get('cuisine_type'),
                    "area": current_data.get('area'),
                    "latitude": current_data.get('latitude'),
                    "longitude": current_data.get('longitude')
                }
            }
        
        # Compare menu hash
        if current_data.get('menu_hash') != previous_item.get('menu_hash'):
            changes_detected = True
            change_summary["changes"]["menu"] = {
                "changed": True,
                "previous_items_count": len(previous_item.get('menu_items', [])),
                "current_items_count": len(current_data.get('menu_items', [])),
                "difflib_file": None
            }
            
            # Generate detailed diff for menu changes
            current_menu = current_data.get('menu_items', [])
            previous_menu = previous_item.get('menu_items', [])
            
            current_menu_str = json.dumps(current_menu, sort_keys=True, indent=2)
            previous_menu_str = json.dumps(previous_menu, sort_keys=True, indent=2)
            
            diff = list(difflib.unified_diff(
                previous_menu_str.splitlines(keepends=True),
                current_menu_str.splitlines(keepends=True),
                fromfile=f'previous_menu_{restaurant_id}',
                tofile=f'current_menu_{restaurant_id}',
                lineterm=''
            ))
            
            if diff:
                self.logger.info(f"🍽️ Menu changes detected for restaurant {restaurant_id}")
                # Save diff to area-specific directory
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                diff_filename = f"menu_diff_{restaurant_id}_{timestamp}.txt"
                diff_file = os.path.join(self.output_dir, diff_filename)
                try:
                    with open(diff_file, 'w', encoding='utf-8') as f:
                        f.writelines(diff)
                    change_summary["changes"]["menu"]["difflib_file"] = diff_filename
                    self.logger.info(f"📄 Menu diff saved to {diff_file}")
                except Exception as e:
                    self.logger.error(f"❌ Error saving diff file: {e}")
                
                # Analyze specific changes
                menu_analysis = self.analyze_menu_changes(previous_menu, current_menu)
                change_summary["changes"]["menu"]["analysis"] = menu_analysis
        
        if changes_detected:
            return True, change_summary
        else:
            return False, "⏭️ No changes"

    def analyze_menu_changes(self, previous_menu, current_menu):
        """Analyze specific menu changes (added, removed, modified items)"""
        previous_items = {item.get('item_id'): item for item in previous_menu}
        current_items = {item.get('item_id'): item for item in current_menu}
        
        added_items = []
        removed_items = []
        modified_items = []
        
        # Find added items
        for item_id, item in current_items.items():
            if item_id not in previous_items:
                added_items.append({
                    "name": item.get('name'),
                    "price": item.get('price'),
                    "category": item.get('category'),
                    "item_id": item_id
                })
        
        # Find removed items
        for item_id, item in previous_items.items():
            if item_id not in current_items:
                removed_items.append({
                    "name": item.get('name'),
                    "price": item.get('price'),
                    "category": item.get('category'),
                    "item_id": item_id
                })
        
        # Find modified items
        for item_id in set(previous_items.keys()) & set(current_items.keys()):
            prev_item = previous_items[item_id]
            curr_item = current_items[item_id]
            
            changes = {}
            for key in ['name', 'price', 'description', 'category', 'available']:
                if prev_item.get(key) != curr_item.get(key):
                    changes[key] = {
                        "previous": prev_item.get(key),
                        "current": curr_item.get(key)
                    }
            
            if changes:
                modified_items.append({
                    "item_id": item_id,
                    "name": curr_item.get('name'),
                    "changes": changes
                })
        
        return {
            "added_items": added_items,
            "removed_items": removed_items,
            "modified_items": modified_items,
            "summary": {
                "added_count": len(added_items),
                "removed_count": len(removed_items),
                "modified_count": len(modified_items)
            }
        }

    def extract_restaurant_data(self, next_data, response, restaurant_id, restaurant_url):
        """Extract restaurant data from both __NEXT_DATA__ and LD+JSON"""
        try:
            # Extract menu items from __NEXT_DATA__ (this part works well)
            menu_items = []
            props = next_data.get('props', {})
            page_props = props.get('pageProps', {})
            
            menu_state = page_props.get('initialMenuState', {})
            if menu_state:
                menu_data = menu_state.get('menuData', {})
                items = menu_data.get("items", [])
                
                for item in items:
                    if item and isinstance(item, dict):
                        menu_item = {
                            'name': item.get('name', 'Unknown Item'),
                            'description': item.get('description', ''),
                            'price': f"AED {item.get('price', 'N/A')}",
                            'category': item.get('originalSection', 'Unknown Category'),
                            'available': item.get('available', True),
                            'item_id': item.get('id', '')
                        }
                        menu_items.append(menu_item)
            
            # Extract restaurant info from multiple sources with fallbacks
            restaurant_name = "Unknown Restaurant"
            cuisine_types = []
            latitude = None
            longitude = None
            
            # Method 1: Extract from URL (most reliable method)
            try:
                # Extract name from URL pattern: /restaurant/ID/restaurant-name-location
                url_parts = restaurant_url.split('/')
                if len(url_parts) >= 5:
                    url_slug = url_parts[4].split('?')[0]  # Remove query parameters
                    
                    # Extract restaurant name part (before location)
                    # Pattern: "matcha-blend-khalifa-city" -> "matcha-blend"
                    slug_parts = url_slug.split('-')
                    
                    # Common location suffixes to remove
                    location_suffixes = [
                        'khalifa', 'city', 'dubai', 'abu', 'dhabi', 'sharjah', 'ajman', 
                        'mall', 'center', 'centre', 'street', 'road', 'area', 'district',
                        'tower', 'plaza', 'square', 'park', 'village', 'island', 'marina',
                        'downtown', 'airport', 'industrial', 'business', 'commercial'
                    ]
                    
                    # Find where location part starts
                    name_parts = []
                    for i, part in enumerate(slug_parts):
                        if part.lower() in location_suffixes:
                            # Found location part, take everything before it
                            name_parts = slug_parts[:i]
                            break
                    
                    # If no location suffix found, take first 2-3 parts as name
                    if not name_parts:
                        name_parts = slug_parts[:min(3, len(slug_parts))]
                    
                    if name_parts:
                        # Convert to readable name
                        restaurant_name = ' '.join(name_parts).replace('-', ' ').title()
                        self.logger.info(f"✅ Extracted restaurant name from URL: {restaurant_name}")
                        
            except Exception as e:
                self.logger.debug(f"⚠️ Error extracting from URL: {e}")
            
            # Method 2: Parse LD+JSON scripts (for additional info and validation)
            ld_json_scripts = response.css('script[type="application/ld+json"]::text').getall()
            for script_content in ld_json_scripts:
                try:
                    ld_data = json.loads(script_content)
                    if ld_data.get('@type') == 'Restaurant':
                        # Use LD+JSON name if URL extraction failed
                        if restaurant_name == "Unknown Restaurant":
                            restaurant_name = ld_data.get('name', restaurant_name)
                        
                        # Extract cuisine types
                        serves_cuisine = ld_data.get('servesCuisine', '')
                        if serves_cuisine:
                            # Split by comma and clean up
                            cuisine_types = [cuisine.strip() for cuisine in serves_cuisine.split(',')]
                        
                        # Extract geo coordinates
                        geo = ld_data.get('geo', {})
                        if geo:
                            latitude = geo.get('latitude')
                            longitude = geo.get('longitude')
                        
                        break  # Found restaurant data, no need to check other LD+JSON scripts
                        
                except json.JSONDecodeError as e:
                    self.logger.warning(f"⚠️ Error parsing LD+JSON: {e}")
                    continue
            
            # Method 3: Fallback to __NEXT_DATA__ if previous methods failed
            if restaurant_name == "Unknown Restaurant":
                try:
                    # Try to extract from __NEXT_DATA__
                    restaurant_info = page_props.get('restaurant', {})
                    if restaurant_info:
                        restaurant_name = restaurant_info.get('name', restaurant_name)
                        
                        # Try to get cuisine from restaurant info
                        if not cuisine_types:
                            cuisine_info = restaurant_info.get('cuisine', '')
                            if cuisine_info:
                                cuisine_types = [cuisine_info.strip()]
                        
                        # Try to get coordinates
                        if not latitude or not longitude:
                            location = restaurant_info.get('location', {})
                            if location:
                                latitude = location.get('latitude')
                                longitude = location.get('longitude')
                except Exception as e:
                    self.logger.debug(f"⚠️ Error extracting from __NEXT_DATA__: {e}")
            
            # Method 4: Fallback to HTML selectors as last resort
            if restaurant_name == "Unknown Restaurant":
                try:
                    # Try various CSS selectors for restaurant name
                    name_selectors = [
                        'h1[data-testid="restaurant-name"]',
                        'h1[class*="restaurant"]',
                        'h1[class*="name"]',
                        '[data-testid="branch-name"]',
                        'h1',
                        'h2[class*="restaurant"]',
                        '.restaurant-name',
                        '[class*="RestaurantName"]'
                    ]
                    
                    for selector in name_selectors:
                        element = response.css(f'{selector}::text').get()
                        if element and element.strip() and element.strip() != "Unknown Restaurant":
                            restaurant_name = element.strip()
                            self.logger.info(f"✅ Found restaurant name via CSS selector '{selector}': {restaurant_name}")
                            break
                except Exception as e:
                    self.logger.debug(f"⚠️ Error with CSS selectors: {e}")
            
            # Log the final result
            if restaurant_name == "Unknown Restaurant":
                self.logger.warning(f"⚠️ Could not extract restaurant name for {restaurant_url}")
            else:
                self.logger.debug(f"✅ Final restaurant name: {restaurant_name}")
            
            # Create restaurant item with the correct field names from items.py
            item = TalabatRestaurantItem()
            item['restaurant_id'] = restaurant_id
            item['name'] = restaurant_name
            item['url'] = restaurant_url
            item['cuisine_type'] = ', '.join(cuisine_types) if cuisine_types else ''
            item['area'] = 'Abu Dhabi International Airport'
            item['latitude'] = latitude
            item['longitude'] = longitude
            item['menu_items'] = menu_items
            item['scraped_at'] = datetime.now().isoformat()
            
            # Generate hashes for change detection
            basic_info = {
                'name': restaurant_name,
                'cuisine_type': ', '.join(cuisine_types) if cuisine_types else '',
                'area': 'Abu Dhabi International Airport',
                'latitude': latitude,
                'longitude': longitude
            }
            item['basic_info_hash'] = self.generate_hash(basic_info)
            item['menu_hash'] = self.generate_hash(menu_items)
            item['content_hash'] = self.generate_hash({'basic': basic_info, 'menu': menu_items})
            
            self.logger.info(f"✅ Extracted data for {restaurant_name} with {len(menu_items)} menu items and {len(cuisine_types)} cuisine types")
            return item
            
        except Exception as e:
            self.logger.error(f"❌ Error extracting restaurant data: {e}")
            return None

    def parse_restaurant(self, response):
        """Parse individual restaurant page for detailed information (Phase 2)"""
        restaurant_url = response.url
        restaurant_id = self.extract_restaurant_id(restaurant_url)
        restaurant_index = response.meta.get('restaurant_index', 0)
        total_restaurants = response.meta.get('total_restaurants', 0)
        
        self.logger.info(f"🔍 Processing restaurant {restaurant_index}/{total_restaurants}: {restaurant_id}")
        
        if not restaurant_id:
            self.logger.warning(f"⚠️ Could not extract restaurant ID from {restaurant_url}")
            return
        
        # Extract __NEXT_DATA__ script
        next_data_script = response.xpath('//script[@id="__NEXT_DATA__"]/text()').get()
        if not next_data_script:
            self.logger.warning(f"⚠️ No __NEXT_DATA__ found for {restaurant_url}")
            return
        
        try:
            next_data = json.loads(next_data_script)
            restaurant_data = self.extract_restaurant_data(next_data, response, restaurant_id, restaurant_url)
            
            if restaurant_data:
                # Check for changes using difflib
                has_changes, change_reason = self.check_for_changes(restaurant_id, restaurant_data)
                
                if has_changes:
                    restaurant_name = restaurant_data.get('name', 'Unknown Restaurant')
                    if isinstance(change_reason, dict) and change_reason.get("type") == "new_restaurant":
                        self.logger.info(f"🆕 New restaurant: {restaurant_name} ({restaurant_id})")
                    else:
                        self.logger.info(f"🔄 Changes detected for {restaurant_name} ({restaurant_id})")
                        
                        # Save detailed change summary to JSON file
                        if isinstance(change_reason, dict) and "changes" in change_reason:
                            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                            change_summary_file = os.path.join(self.output_dir, f"changes_{restaurant_id}_{timestamp}.json")
                            try:
                                with open(change_summary_file, 'w', encoding='utf-8') as f:
                                    json.dump(change_reason, f, indent=2, ensure_ascii=False)
                                self.logger.info(f"📋 Change summary saved to: {change_summary_file}")
                            except Exception as e:
                                self.logger.error(f"❌ Error saving change summary: {e}")
                    
                    # Update previous data for next comparison
                    self.previous_data[restaurant_id] = dict(restaurant_data)
                    yield restaurant_data
                else:
                    restaurant_name = restaurant_data.get('name', 'Unknown Restaurant')
                    self.logger.info(f"⏭️ No changes detected for {restaurant_name} ({restaurant_id}) - skipping")
            else:
                self.logger.warning(f"⚠️ Could not extract restaurant data from {restaurant_url}")
                
        except json.JSONDecodeError as e:
            self.logger.error(f"❌ Error parsing JSON from {restaurant_url}: {e}")
        except Exception as e:
            self.logger.error(f"❌ Error processing restaurant {restaurant_url}: {e}")

    def closed(self, reason):
        """Save updated data for future comparisons"""
        try:
            # Save updated previous data
            data_file = os.path.join(self.output_dir, 'previous_data.json')
            with open(data_file, 'w', encoding='utf-8') as f:
                json.dump(self.previous_data, f, ensure_ascii=False, indent=2)
            
            # Save summary statistics
            summary_file = os.path.join(self.output_dir, 'phase2_summary.json')
            summary = {
                'phase': 'Phase 2 - Detailed Scraping',
                'timestamp': datetime.now().isoformat(),
                'total_restaurants_processed': len(self.restaurant_urls),
                'restaurants_with_data': len(self.previous_data),
                'area': self.area_name,
                'reason': reason
            }
            
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
                
            self.logger.info(f"🎉 Phase 2 Complete! Processed {len(self.restaurant_urls)} restaurants")
            self.logger.info(f"📁 Data saved to: {data_file}")
            self.logger.info(f"📊 Summary saved to: {summary_file}")
            self.logger.info(f"🔄 Next run will compare against this data for changes")
            
        except Exception as e:
            self.logger.error(f"❌ Error in spider close: {str(e)}")