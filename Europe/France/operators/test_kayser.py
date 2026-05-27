"""
Test script to explore Maison Kayser page structure and find France outlets.
"""
import asyncio
import re
import json
from curl_cffi.requests import AsyncSession
from loguru import logger

async def explore_kayser():
    url = "https://maison-kayser.com/nos-boulangeries/"
    
    async with AsyncSession(impersonate="chrome120") as session:
        response = await session.get(url, timeout=30)
        html = response.text
        
        print(f"Page size: {len(html)} bytes")
        
        # Find all data-permalink URLs
        permalinks = re.findall(r'data-permalink="([^"]+)"', html)
        print(f"\nFound {len(permalinks)} permalinks total")
        
        # Show sample
        for p in permalinks[:5]:
            print(f"  - {p}")
        
        # Find France section
        france_idx = html.find('EN FRANCE')
        if france_idx != -1:
            print(f"\nFrance section found at index {france_idx}")
            
            # Look for the regions under France
            france_section = html[france_idx:france_idx+5000]
            
            # Extract French regions
            regions = re.findall(r'<li class="main-store__regions__section__list--li"[^>]*>\s*([^<]+)', france_section)
            print(f"\nFrench regions found:")
            for r in regions:
                print(f"  - {r.strip()}")
        
        # Try to find store markers/data
        print("\n--- Looking for store data ---")
        
        # Check for JSON data in scripts
        json_patterns = [
            (r'stores\s*[=:]\s*(\[[\s\S]*?\])', "stores array"),
            (r'markers\s*[=:]\s*(\[[\s\S]*?\])', "markers array"),
            (r'locations\s*[=:]\s*(\[[\s\S]*?\])', "locations array"),
            (r'"features"\s*:\s*(\[[\s\S]*?\])', "features array"),
        ]
        
        for pattern, name in json_patterns:
            matches = re.findall(pattern, html[:100000])
            if matches:
                print(f"Found {name}: {len(matches)} matches")
                for m in matches[:1]:
                    try:
                        data = json.loads(m)
                        print(f"  -> Contains {len(data)} items")
                        if data:
                            print(f"  -> Sample: {json.dumps(data[0], indent=2)[:500]}")
                    except:
                        print(f"  -> Parse failed, length: {len(m)}")
        
        # Look for store cards/elements
        store_cards = re.findall(r'<div[^>]*class="[^"]*store[^"]*"[^>]*data-permalink="([^"]+)"', html)
        print(f"\nStore cards with permalinks: {len(store_cards)}")
        
        # Save HTML for inspection
        with open("France/output/kayser_debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("\nSaved HTML for inspection")
        
        return html

asyncio.run(explore_kayser())
