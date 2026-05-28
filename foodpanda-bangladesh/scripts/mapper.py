"""
Foodpanda Data Mapper - Multi-City Version
Merges Phase 1 (restaurants) with Phase 2 (menus)

Usage:
    py mapper.py --city cebu
"""

import json
import argparse
import pandas as pd
from pathlib import Path
from datetime import datetime
from loguru import logger
import glob


def load_restaurant_data(restaurant_file: Path) -> dict:
    """Load Phase 1 restaurant data and index by vendor_code"""
    logger.info(f"📂 Loading restaurants: {restaurant_file.name}")
    
    with open(restaurant_file, 'r', encoding='utf-8') as f:
        restaurants = json.load(f)
    
    restaurant_map = {r.get('code'): r for r in restaurants if r.get('code')}
    logger.info(f"   ✅ {len(restaurant_map)} restaurants indexed")
    return restaurant_map


def load_menu_data(menu_file: Path) -> list:
    """Load Phase 2 menu data"""
    logger.info(f"📂 Loading menus: {menu_file.name}")
    
    with open(menu_file, 'r', encoding='utf-8') as f:
        menus = json.load(f)
    
    logger.info(f"   ✅ {len(menus)} restaurant menus loaded")
    return menus


def merge_data(restaurant_map: dict, menus: list) -> list:
    """Merge restaurant details with menu data"""
    logger.info(f"\n🔗 Merging data...")
    
    merged_data = []
    matched = 0
    unmatched = 0
    
    for menu in menus:
        vendor_code = menu['restaurant']['vendor_code']
        restaurant = restaurant_map.get(vendor_code)
        
        if restaurant:
            matched += 1
            cuisines = restaurant.get('cuisines', '')
            if isinstance(cuisines, list):
                cuisines = ', '.join(cuisines)
        else:
            unmatched += 1
            cuisines = ', '.join(menu['restaurant'].get('cuisines', []))
            restaurant = {}
        
        # Base restaurant info
        base_info = {
            'restaurant_name': restaurant.get('name', menu['restaurant']['name']),
            'address': restaurant.get('address', ''),
            'address_line2': restaurant.get('address_line2', ''),
            'latitude': restaurant.get('latitude', ''),
            'longitude': restaurant.get('longitude', ''),
            'city': restaurant.get('city', menu['restaurant'].get('city', '')),
            'post_code': restaurant.get('post_code', ''),
            'cuisines': cuisines,
            'rating': restaurant.get('rating', menu['restaurant'].get('rating', 0)),
            'review_count': restaurant.get('review_count', menu['restaurant'].get('review_count', 0)),
            'web_path': restaurant.get('web_path', ''),
            'vendor_code': vendor_code
        }
        
        # Add each menu item as a row
        for category in menu['menu']:
            for item in category['items']:
                row = {
                    **base_info,
                    'menu_category': category['category_name'],
                    'menu_item': item['name'],
                    'price': item['price'],
                    'price_value': item['price_value'],
                    'description': item['description'],
                    'image_url': item.get('image_url', ''),
                    'is_sold_out': item.get('is_sold_out', False)
                }
                merged_data.append(row)
    
    logger.info(f"   ✅ Matched: {matched}/{len(menus)}")
    logger.info(f"   ⚠️  Unmatched: {unmatched}")
    logger.info(f"   📊 Total rows: {len(merged_data)}")
    
    return merged_data


def save_merged_data(merged_data: list, output_dir: Path, city_name: str):
    """Save merged data to Excel and JSON"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    df = pd.DataFrame(merged_data)
    
    # Reorder columns
    column_order = [
        'restaurant_name', 'address', 'address_line2', 'latitude', 'longitude',
        'city', 'post_code', 'cuisines', 'rating', 'review_count',
        'menu_category', 'menu_item', 'price', 'price_value', 'description',
        'web_path', 'vendor_code', 'image_url', 'is_sold_out'
    ]
    
    for col in column_order:
        if col not in df.columns:
            df[col] = ''
    df = df[column_order]
    
    # Clean for Excel
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].astype(str).str[:32767]
            df[col] = df[col].str.replace(r'[\x00-\x1f\x7f-\x9f]', '', regex=True)
    
    # Save Excel
    excel_file = output_dir / f"final_{city_name}_{timestamp}.xlsx"
    df.to_excel(excel_file, index=False, engine='openpyxl')
    logger.info(f"\n💾 Excel: {excel_file}")
    logger.info(f"   📊 {len(df)} rows × {len(df.columns)} columns")
    
    # Save JSON
    json_file = output_dir / f"final_{city_name}_{timestamp}.json"
    df.to_json(json_file, orient='records', indent=2, force_ascii=False)
    logger.info(f"💾 JSON: {json_file}")
    
    # Summary
    logger.info(f"\n📈 Summary:")
    logger.info(f"   Unique Restaurants: {df['restaurant_name'].nunique()}")
    logger.info(f"   Unique Categories: {df['menu_category'].nunique()}")
    logger.info(f"   Total Menu Items: {len(df)}")
    logger.info(f"   Price Range: ₱{df['price_value'].min():.0f} - ₱{df['price_value'].max():.0f}")
    logger.info(f"   Avg Price: ₱{df['price_value'].mean():.0f}")
    
    return df


def main():
    parser = argparse.ArgumentParser(description="Foodpanda Data Mapper")
    parser.add_argument("--city", type=str, required=True, help="City key (e.g., cebu)")
    args = parser.parse_args()
    
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    # Setup logging
    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level="INFO", format="{message}")
    
    logger.info(f"\n{'='*60}")
    logger.info(f"🔗 Foodpanda Data Mapper")
    logger.info(f"{'='*60}")
    logger.info(f"📍 City: {args.city}\n")
    
    # Paths
    data_dir = Path(__file__).parent.parent / "data" / args.city
    
    # Find latest files
    restaurant_files = list(data_dir.glob("restaurants_*.json"))
    menu_files = list(data_dir.glob("menus_*.json"))
    
    if not restaurant_files:
        logger.error(f"❌ No restaurant data found in {data_dir}")
        return
    
    if not menu_files:
        logger.error(f"❌ No menu data found in {data_dir}")
        return
    
    latest_restaurant = max(restaurant_files, key=lambda x: x.stem.split('_')[-1])
    latest_menu = max(menu_files, key=lambda x: x.stem.split('_')[-1])
    
    # Load data
    restaurant_map = load_restaurant_data(latest_restaurant)
    menus = load_menu_data(latest_menu)
    
    # Merge
    merged_data = merge_data(restaurant_map, menus)
    
    # Save
    if merged_data:
        save_merged_data(merged_data, data_dir, args.city)
    
    logger.info(f"\n{'='*60}")
    logger.info(f"✅ Mapping Complete!")
    logger.info(f"{'='*60}\n")


if __name__ == "__main__":
    main()
