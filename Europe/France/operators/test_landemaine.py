"""Explore Maison Landemaine page structure."""
import asyncio
import re
from curl_cffi.requests import AsyncSession

async def explore_landemaine():
    url = "https://maisonlandemaine.com/boulangeries/"
    
    async with AsyncSession(impersonate="chrome120") as session:
        response = await session.get(url, timeout=30)
        html = response.text
        
        print(f"Status: {response.status_code}")
        print(f"Page size: {len(html)} bytes")
        
        # Look for storeLocator elements
        store_list = re.search(r'id="storeLocator__storeList"', html)
        print(f"\nstoreLocator__storeList found: {store_list is not None}")
        
        # Look for store divs
        store_divs = re.findall(r'id="store\d+"', html)
        print(f"Store divs: {len(store_divs)}")
        
        # Look for infobox elements
        infoboxes = re.findall(r'class="[^"]*infobox[^"]*"', html)
        print(f"Infobox elements: {len(infoboxes)}")
        
        # Look for store-location class
        locations = re.findall(r'class="[^"]*store-location[^"]*"[^>]*>([\s\S]*?)</div>', html)
        print(f"\nStore locations found: {len(locations)}")
        for loc in locations[:5]:
            # Clean HTML
            clean = re.sub(r'<[^>]+>', ' ', loc).strip()
            clean = re.sub(r'\s+', ' ', clean)
            print(f"  - {clean[:80]}")
        
        # Look for store-address
        addresses = re.findall(r'class="[^"]*store-address[^"]*"[^>]*>([\s\S]*?)</div>', html)
        print(f"\nStore addresses found: {len(addresses)}")
        for addr in addresses[:3]:
            clean = re.sub(r'<[^>]+>', ' ', addr).strip()
            clean = re.sub(r'\s+', ' ', clean)
            print(f"  - {clean[:100]}")
        
        # Save HTML for inspection
        with open("France/output/landemaine.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("\nSaved HTML")

asyncio.run(explore_landemaine())
