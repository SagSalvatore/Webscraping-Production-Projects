"""Fetch Sophie Lebreuilly iframe content directly."""
import asyncio
import re
import json
from curl_cffi.requests import AsyncSession

async def fetch_iframe():
    # Full iframe URL from user
    url = "https://www.adelyashop.com/Adelyaview/sophielebreuilly/storelocator/Boulangerie-Sophie.html?lang=fr"
    
    async with AsyncSession(impersonate="chrome120") as session:
        print(f"Fetching: {url}")
        response = await session.get(url, timeout=30)
        html = response.text
        
        print(f"Status: {response.status_code}")
        print(f"Page size: {len(html)} bytes")
        
        if response.status_code != 200:
            print(f"Error! Response: {html[:500]}")
            return
        
        # Look for store elements
        # From screenshoot: data-codegroup="689293533", store-wrapper class
        store_wrappers = re.findall(r'<div[^>]*class="[^"]*store-wrapper[^"]*"[^>]*>', html)
        print(f"\nStore wrappers: {len(store_wrappers)}")
        if store_wrappers:
            print(f"Sample: {store_wrappers[0][:300]}")
        
        # Find store titles/names
        titles = re.findall(r'class="[^"]*title[^"]*"[^>]*>([^<]+)<', html)
        print(f"\nTitles found: {len(titles)}")
        for t in titles[:5]:
            print(f"  {t.strip()}")
        
        # Look for address elements
        addresses = re.findall(r'class="[^"]*address[^"]*"[^>]*>([\s\S]*?)</div>', html)
        print(f"\nAddresses found: {len(addresses)}")
        
        # Look for JSON data
        json_patterns = [
            r'stores\s*[=:]\s*(\[[\s\S]*?\])',
            r'"items"\s*:\s*(\[[\s\S]*?\])',
            r'data\s*=\s*(\[[\s\S]*?\])',
        ]
        for pattern in json_patterns:
            matches = re.findall(pattern, html)
            if matches:
                print(f"\nJSON pattern '{pattern}' found: {len(matches)} matches")
        
        # Save HTML for inspection
        with open("France/output/sophie_iframe.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("\nSaved to sophie_iframe.html")

asyncio.run(fetch_iframe())
