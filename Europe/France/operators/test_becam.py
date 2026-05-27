"""Debug CloseBy API response - save raw data."""
import asyncio
import json
from curl_cffi.requests import AsyncSession

async def debug():
    url = "https://www.closeby.co/embed/565a516ff139f04494b93d2161a9620e/locations"
    async with AsyncSession(impersonate="chrome120") as session:
        r = await session.get(url, timeout=30)
        d = r.json()
        
        # Save raw data
        with open("France/output/becam_raw.json", "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        
        # Extract first location
        if isinstance(d, dict):
            print("Keys:", list(d.keys())[:20])
            # Get first item
            first_key = list(d.keys())[0]
            first = d[first_key]
            print(f"\nFirst item ({first_key}):")
            print(json.dumps(first, indent=2)[:1500])
        elif isinstance(d, list):
            print("First item:")
            print(json.dumps(d[0], indent=2)[:1500])

asyncio.run(debug())
