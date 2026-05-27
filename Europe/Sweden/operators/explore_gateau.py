"""Explore Gateau.se - try different API endpoints."""
import asyncio
import re
import json
from curl_cffi.requests import AsyncSession

async def explore():
    async with AsyncSession(impersonate="chrome120") as session:
        # Try the main page API
        api_url = "https://www.gateau.se/api/cda/content"
        params = {
            "contentUrl": "https://www.gateau.se/butiker/",
            "currentPageUrl": "/butiker/"
        }
        
        print("Trying main butiker page API...")
        r = await session.get(api_url, params=params, timeout=30)
        print(f"Status: {r.status_code}")
        
        if r.status_code == 200:
            try:
                data = r.json()
                print(f"Response size: {len(str(data))} chars")
                
                # Look for store links in the response
                data_str = json.dumps(data, ensure_ascii=False)
                store_links = re.findall(r'/butiker/([^/"\s<>]+)/', data_str)
                unique_stores = sorted(set(store_links))
                print(f"\nFound {len(unique_stores)} unique store slugs:")
                for store in unique_stores:
                    print(f"  - {store}")
                
                # Save full response
                with open("Sweden/output/gateau_list.json", "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print("\nSaved to gateau_list.json")
                
            except Exception as e:
                print(f"Error: {e}")
        
        # Also try a specific store API to see the structure
        print("\n" + "="*60)
        print("Trying specific store API...")
        params2 = {
            "contentUrl": "https://www.gateau.se/butiker/liljeholmstorget/",
            "currentPageUrl": "/butiker/"
        }
        r2 = await session.get(api_url, params=params2, timeout=30)
        print(f"Status: {r2.status_code}")
        
        if r2.status_code == 200:
            data2 = r2.json()
            print(f"Response size: {len(str(data2))} chars")
            if isinstance(data2, dict):
                print(f"Keys: {list(data2.keys())}")
            
            with open("Sweden/output/gateau_store.json", "w", encoding="utf-8") as f:
                json.dump(data2, f, ensure_ascii=False, indent=2)
            print("Saved to gateau_store.json")

asyncio.run(explore())
