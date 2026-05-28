"""
Foodpanda Data Mapper
Merges Phase 1 (restaurant details) with Phase 2 (menu data)

Output fields:
- Restaurant Name, Address, Latitude, Longitude, Cuisines
- Menu Category, Menu Item, Price, Description
- City, Post Code, Web Path
"""

import json
import pandas as pd
from typing import Dict, List
from pathlib import Path
import glob


def load_restaurant_data(restaurant_file: str) -> Dict[str, Dict]:
    """
    Load Phase 1 restaurant data and index by vendor_code
    
    Returns:
        Dictionary mapping vendor_code -> restaurant details
    """
    print(f"📂 Loading restaurant data from: {restaurant_file}")
    
    with open(restaurant_file, 'r', encoding='utf-8') as f:
        restaurants = json.load(f)
    
    # Index by vendor code
    restaurant_map = {}
    for restaurant in restaurants:
        code = restaurant.get('code')
        if code:
            restaurant_map[code] = restaurant
    
    print(f"   ✅ Loaded {len(restaurant_map)} restaurants")
    return restaurant_map


def load_menu_data(menu_file: str) -> List[Dict]:
    """Load Phase 2 menu data"""
    print(f"📂 Loading menu data from: {menu_file}")
    
    with open(menu_file, 'r', encoding='utf-8') as f:
        menus = json.load(f)
    
    print(f"   ✅ Loaded {len(menus)} restaurant menus")
    return menus


def merge_data(restaurant_map: Dict[str, Dict], menus: List[Dict]) -> List[Dict]:
    """
    Merge restaurant details with menu data
    
    Returns:
        List of dictionaries, one row per menu item with full restaurant details
    """
    print(f"\n🔗 Merging restaurant and menu data...")
    
    merged_data = []
    matched_count = 0
    unmatched_count = 0
    
    for menu in menus:
        vendor_code = menu['restaurant']['vendor_code']
        
        # Find matching restaurant from Phase 1
        restaurant = restaurant_map.get(vendor_code)
        
        if not restaurant:
            print(f"   ⚠️  No match found for vendor_code: {vendor_code}")
            unmatched_count += 1
            # Use menu data only
            restaurant = {
                'name': menu['restaurant']['name'],
                'address': '',
                'latitude': '',
                'longitude': '',
                'city': menu['restaurant'].get('city', ''),
                'post_code': '',
                'web_path': '',
                'cuisines': ', '.join(menu['restaurant'].get('cuisines', []))
            }
        else:
            matched_count += 1
            # Format cuisines
            cuisines_list = restaurant.get('cuisines', [])
            if isinstance(cuisines_list, list):
                cuisines_str = ', '.join(cuisines_list)
            else:
                cuisines_str = cuisines_list
        
        # Extract restaurant details
        restaurant_details = {
            'restaurant_name': restaurant.get('name', menu['restaurant']['name']),
            'address': restaurant.get('address', ''),
            'address_line2': restaurant.get('address_line2', ''),
            'latitude': restaurant.get('latitude', ''),
            'longitude': restaurant.get('longitude', ''),
            'city': restaurant.get('city', ''),
            'post_code': restaurant.get('post_code', ''),
            'web_path': restaurant.get('web_path', ''),
            'cuisines': cuisines_str if restaurant else ', '.join(menu['restaurant'].get('cuisines', [])),
            'rating': restaurant.get('rating', menu['restaurant'].get('rating', 0)),
            'review_count': restaurant.get('review_count', menu['restaurant'].get('review_count', 0)),
            'vendor_code': vendor_code
        }
        
        # Add each menu item as a separate row
        for category in menu['menu']:
            for item in category['items']:
                row = {
                    **restaurant_details,
                    'menu_category': category['category_name'],
                    'menu_item': item['name'],
                    'price': item['price'],
                    'price_value': item['price_value'],
                    'description': item['description'],
                    'image_url': item.get('image_url', ''),
                    'is_sold_out': item.get('is_sold_out', False)
                }
                merged_data.append(row)
    
    print(f"   ✅ Matched: {matched_count}/{len(menus)} restaurants")
    print(f"   ⚠️  Unmatched: {unmatched_count}/{len(menus)} restaurants")
    print(f"   📊 Total rows created: {len(merged_data)}")
    
    return merged_data


def save_merged_data(merged_data: List[Dict], output_prefix: str = "foodpanda_final"):
    """Save merged data to CSV and JSON"""
    from datetime import datetime
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Convert to DataFrame
    df = pd.DataFrame(merged_data)
    
    # Reorder columns for better readability
    column_order = [
        'restaurant_name',
        'address',
        'address_line2',
        'latitude',
        'longitude',
        'city',
        'post_code',
        'cuisines',
        'rating',
        'review_count',
        'menu_category',
        'menu_item',
        'price',
        'price_value',
        'description',
        'web_path',
        'vendor_code',
        'image_url',
        'is_sold_out'
    ]
    
    # Ensure all columns exist
    for col in column_order:
        if col not in df.columns:
            df[col] = ''
    
    df = df[column_order]
    
    # Sanitize data for Excel compatibility
    # Excel has issues with very long text and certain characters
    for col in df.columns:
        if df[col].dtype == 'object':  # String columns
            # Remove illegal Excel characters and limit length
            df[col] = df[col].astype(str).str[:32767]  # Excel cell limit
            # Remove control characters that cause issues
            df[col] = df[col].str.replace(r'[\x00-\x1f\x7f-\x9f]', '', regex=True)
    
    # Save Excel
    excel_file = f"{output_prefix}_{timestamp}.xlsx"
    df.to_excel(excel_file, index=False, engine='openpyxl')
    print(f"\n💾 Excel saved: {excel_file}")
    print(f"   📊 {len(df)} rows × {len(df.columns)} columns")
    
    # Save JSON
    json_file = f"{output_prefix}_{timestamp}.json"
    df.to_json(json_file, orient='records', indent=2, force_ascii=False)
    print(f"💾 JSON saved: {json_file}")
    
    # Print summary
    print(f"\n📈 Summary Statistics:")
    print(f"   Unique Restaurants: {df['restaurant_name'].nunique()}")
    print(f"   Unique Menu Categories: {df['menu_category'].nunique()}")
    print(f"   Total Menu Items: {len(df)}")
    print(f"   Unique Cities: {df['city'].nunique()}")
    print(f"   Price Range: {df['price_value'].min():.0f} - {df['price_value'].max():.0f}")
    print(f"   Avg Price: ₱{df['price_value'].mean():.0f}")
    
    # Top categories
    print(f"\n📋 Top 10 Menu Categories:")
    top_categories = df['menu_category'].value_counts().head(10)
    for cat, count in top_categories.items():
        print(f"   - {cat}: {count} items")
    
    return df


def main():
    """Main execution"""
    
    print(f"\n{'='*60}")
    print(f"🔗 Foodpanda Data Mapper")
    print(f"{'='*60}\n")
    
    # Find latest Phase 1 restaurant file
    restaurant_files = glob.glob("foodpanda_restaurants_*.json")
    if not restaurant_files:
        print("❌ No Phase 1 restaurant data found!")
        print("   Looking for: foodpanda_restaurants_*.json")
        return
    
    restaurant_file = max(restaurant_files, key=lambda x: x.split('_')[-1])
    
    # Find latest Phase 2 menu file
    menu_files = glob.glob("foodpanda_menus_*.json")
    if not menu_files:
        print("❌ No Phase 2 menu data found!")
        print("   Looking for: foodpanda_menus_*.json")
        return
    
    menu_file = max(menu_files, key=lambda x: x.split('_')[-1])
    
    # Load data
    restaurant_map = load_restaurant_data(restaurant_file)
    menus = load_menu_data(menu_file)
    
    # Merge
    merged_data = merge_data(restaurant_map, menus)
    
    # Save
    if merged_data:
        df = save_merged_data(merged_data)
        
        # Show sample
        print(f"\n📝 Sample Rows (first 3):")
        sample_cols = ['restaurant_name', 'menu_category', 'menu_item', 'price', 'city']
        print(df[sample_cols].head(3).to_string(index=False))
    else:
        print("❌ No merged data created!")
    
    print(f"\n{'='*60}")
    print(f"✅ Mapping Complete!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
