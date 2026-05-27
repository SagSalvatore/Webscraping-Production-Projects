#!/usr/bin/env python3
"""
Test script for URL-based restaurant name extraction
"""

def extract_restaurant_name_from_url(restaurant_url):
    """Extract restaurant name from Talabat URL"""
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
                return restaurant_name
                
    except Exception as e:
        print(f"Error extracting from URL: {e}")
        return "Unknown Restaurant"
    
    return "Unknown Restaurant"

# Test cases
test_urls = [
    "https://www.talabat.com/uae/restaurant/756793/matcha-blend-khalifa-city?aid=6484",
    "https://www.talabat.com/uae/restaurant/734258/the-grove-cafe-and-restaurant-mussafah?aid=6484",
    "https://www.talabat.com/uae/restaurant/677358/uncle-shawarma-khalifa-city-2?aid=6484",
    "https://www.talabat.com/uae/restaurant/751825/mcdonalds-al-raha-mall?aid=6484",
    "https://www.talabat.com/uae/restaurant/123456/pizza-hut-dubai-mall-downtown?aid=6484"
]

print("Testing URL-based restaurant name extraction:")
print("=" * 60)

for url in test_urls:
    extracted_name = extract_restaurant_name_from_url(url)
    print(f"URL: {url}")
    print(f"Extracted Name: {extracted_name}")
    print("-" * 60)