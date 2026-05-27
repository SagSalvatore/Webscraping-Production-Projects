"""
Test script to extract LD+JSON data from a single Marie Blachère bakery URL.
This validates the approach before scaling to 800+ URLs.
"""
import asyncio
import json
import re
from curl_cffi.requests import AsyncSession
from loguru import logger

async def test_single_url():
    """Test LD+JSON extraction on single URL."""
    
    test_url = "https://boulangeries.marieblachere.com/fr/france-FR/5043/marie-blachere-abbeville/details"
    
    logger.info(f"Testing URL: {test_url}")
    
    async with AsyncSession(impersonate="chrome120") as session:
        response = await session.get(test_url)
        
        logger.info(f"Status: {response.status_code}")
        logger.info(f"Content length: {len(response.text)}")
        
        html = response.text
        
        # Extract LD+JSON from script tags
        # Pattern: <script type="application/ld+json">[...]</script>
        ld_json_pattern = re.compile(
            r'<script\s+type=["\']application/ld\+json["\']>\s*(\[[\s\S]*?\]|\{[\s\S]*?\})\s*</script>',
            re.IGNORECASE
        )
        
        matches = ld_json_pattern.findall(html)
        
        if not matches:
            logger.error("No LD+JSON found!")
            # Try alternative pattern
            alt_pattern = re.compile(r'application/ld\+json">\s*([\s\S]*?)</script>')
            matches = alt_pattern.findall(html)
            
        logger.info(f"Found {len(matches)} LD+JSON blocks")
        
        for i, match in enumerate(matches):
            try:
                data = json.loads(match)
                logger.success(f"Block {i+1}: Successfully parsed JSON")
                
                # Handle both array and object formats
                if isinstance(data, list):
                    for item in data:
                        if item.get("@type") == "Bakery":
                            logger.info("Found Bakery data:")
                            print_bakery_data(item)
                elif isinstance(data, dict):
                    if data.get("@type") == "Bakery":
                        print_bakery_data(data)
                        
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse block {i+1}: {e}")
                # Show first 500 chars
                logger.debug(f"Content: {match[:500]}...")


def print_bakery_data(data: dict):
    """Pretty print bakery data."""
    print("\n" + "=" * 60)
    print("BAKERY DATA EXTRACTED:")
    print("=" * 60)
    
    # Core fields
    print(f"Name: {data.get('name')}")
    print(f"URL: {data.get('url')}")
    print(f"Phone: {data.get('telephone')}")
    print(f"Email: {data.get('email')}")
    
    # Address
    address = data.get('address', {})
    if address:
        print(f"\nAddress:")
        print(f"  Street: {address.get('streetAddress')}")
        print(f"  City: {address.get('addressLocality')}")
        print(f"  Postal Code: {address.get('postalCode')}")
        print(f"  Country: {address.get('addressCountry')}")
    
    # Geo coordinates
    geo = data.get('geo', {})
    if geo:
        print(f"\nCoordinates:")
        print(f"  Latitude: {geo.get('latitude')}")
        print(f"  Longitude: {geo.get('longitude')}")
    
    # Opening hours
    hours = data.get('openingHoursSpecification', [])
    if hours:
        print(f"\nOpening Hours:")
        for h in hours:
            day = h.get('dayOfWeek', '').replace('http://schema.org/', '')
            opens = h.get('opens')
            closes = h.get('closes')
            print(f"  {day}: {opens} - {closes}")
    
    print("=" * 60)
    
    # Return structured data for scraper
    return {
        "store_code": data.get("@id", "").split("#")[-1] if data.get("@id") else "",
        "name": data.get("name"),
        "address": f"{address.get('streetAddress', '')}, {address.get('postalCode', '')} {address.get('addressLocality', '')}",
        "street_address": address.get("streetAddress"),
        "postal_code": address.get("postalCode"),
        "city": address.get("addressLocality"),
        "country": "France",
        "region": address.get("addressCountry", "FR"),
        "phone": data.get("telephone"),
        "email": data.get("email"),
        "latitude": geo.get("latitude"),
        "longitude": geo.get("longitude"),
        "url": data.get("url"),
        "opening_hours": {
            h.get('dayOfWeek', '').replace('http://schema.org/', ''): f"{h.get('opens')}-{h.get('closes')}"
            for h in hours
        },
        "operator": "Marie Blachère",
    }


if __name__ == "__main__":
    asyncio.run(test_single_url())
