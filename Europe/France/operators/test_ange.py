"""
Test script to extract Boulangerie Ange data from the store locator page.
Data is embedded in JavaScript variable: var markers = [...]
"""
import asyncio
import json
import re
from curl_cffi.requests import AsyncSession
from loguru import logger

async def test_ange_extraction():
    """Test extraction of Ange bakery data."""
    
    url = "https://www.boulangerie-ange.fr/en/your-nearest-ange/"
    
    logger.info(f"Fetching: {url}")
    
    async with AsyncSession(impersonate="chrome120") as session:
        response = await session.get(url, timeout=30)
        
        logger.info(f"Status: {response.status_code}")
        logger.info(f"Content length: {len(response.text)}")
        
        html = response.text
        
        # Pattern to extract markers array from: var markers = [...];
        # The array may span multiple lines
        pattern = re.compile(
            r'var\s+markers\s*=\s*(\[\s*\{.*?\}\s*\])\s*;?',
            re.DOTALL
        )
        
        match = pattern.search(html)
        
        if not match:
            logger.error("Could not find markers variable!")
            # Try alternative pattern
            alt_pattern = re.compile(r'var\s+markers\s*=\s*(\[[\s\S]*?\]);')
            match = alt_pattern.search(html)
        
        if match:
            json_str = match.group(1)
            logger.success(f"Found markers array ({len(json_str)} chars)")
            
            try:
                markers = json.loads(json_str)
                logger.success(f"Parsed {len(markers)} bakeries!")
                
                # Show sample data
                print("\n" + "=" * 60)
                print(f"FOUND {len(markers)} BAKERIES")
                print("=" * 60)
                
                print("\nSample data (first 5):")
                for i, m in enumerate(markers[:5]):
                    print(f"\n{i+1}. {m.get('text')}")
                    print(f"   ID: {m.get('id')}")
                    print(f"   Lat/Lng: {m.get('lat')}, {m.get('lng')}")
                    print(f"   URL: {m.get('permalink')}")
                
                # Show data structure
                print("\n\nData structure:")
                print(json.dumps(markers[0], indent=2))
                
                return markers
                
            except json.JSONDecodeError as e:
                logger.error(f"JSON parse error: {e}")
                logger.debug(f"First 500 chars: {json_str[:500]}")
        else:
            logger.error("No markers found in page!")
            
            # Save HTML for debugging
            with open("debug_ange.html", "w", encoding="utf-8") as f:
                f.write(html)
            logger.info("Saved debug HTML to debug_ange.html")
    
    return None


if __name__ == "__main__":
    asyncio.run(test_ange_extraction())
