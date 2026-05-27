"""Debug Bröd & Salt JS extraction."""
import asyncio
import re
import json
from curl_cffi.requests import AsyncSession

async def debug():
    url = "https://brodsalt.se/"
    async with AsyncSession(impersonate="chrome120") as session:
        r = await session.get(url, timeout=30)
        html = r.text
        
        # Find the locations object
        pattern = r'const\s+locations\s*=\s*\{'
        match = re.search(pattern, html)
        if match:
            start = match.start()
            print(f"Found 'const locations' at position {start}")
            
            # Extract using bracket counting
            js_start = html.find('{', match.start())
            bracket_count = 0
            js_end = js_start
            
            for i, char in enumerate(html[js_start:], js_start):
                if char == '{':
                    bracket_count += 1
                elif char == '}':
                    bracket_count -= 1
                    if bracket_count == 0:
                        js_end = i + 1
                        break
            
            js_obj = html[js_start:js_end]
            print(f"JS object length: {len(js_obj)}")
            print(f"First 500 chars:\n{js_obj[:500]}")
            print(f"\nLast 200 chars:\n{js_obj[-200:]}")
            
            # Save for inspection
            with open("Sweden/output/brod_salt_raw.js", "w", encoding="utf-8") as f:
                f.write(js_obj)
            print("\nSaved to brod_salt_raw.js")
        else:
            print("Could not find locations object!")

asyncio.run(debug())
