"""Debug script to understand Banette JSON structure."""
import asyncio
import re
import json
from curl_cffi.requests import AsyncSession

async def debug_banette():
    url = "https://www.banette.fr/nos-boulangeries"
    
    async with AsyncSession(impersonate="chrome120") as session:
        response = await session.get(url, timeout=60)
        html = response.text
        
        print(f"Page size: {len(html)} bytes")
        
        # Find jsonLocations
        idx = html.find('jsonLocations:')
        if idx != -1:
            print(f"\nFound jsonLocations at index {idx}")
            
            # Extract a sample
            sample = html[idx:idx+500]
            print(f"\nSample:\n{sample}")
            
            # Find the start of items array
            items_idx = html.find('"items":[', idx)
            if items_idx != -1:
                print(f"\nFound items array at index {items_idx}")
                
                # Try to find the end of the items array
                # Count brackets
                start = items_idx + len('"items":')
                bracket_count = 0
                end = start
                
                for i, char in enumerate(html[start:start+3000000]):
                    if char == '[':
                        bracket_count += 1
                    elif char == ']':
                        bracket_count -= 1
                        if bracket_count == 0:
                            end = start + i + 1
                            break
                
                print(f"Array ends at index {end}")
                items_str = html[start:end]
                print(f"Items array length: {len(items_str)}")
                
                # Parse the items
                try:
                    items = json.loads(items_str)
                    print(f"\n✅ Successfully parsed {len(items)} items!")
                    
                    # Show first item
                    print(f"\nFirst item:\n{json.dumps(items[0], indent=2)}")
                    
                    # Save items to file for inspection
                    with open("France/output/banette_raw.json", "w", encoding="utf-8") as f:
                        json.dump(items, f, ensure_ascii=False, indent=2)
                    print("\nSaved to banette_raw.json")
                    
                except json.JSONDecodeError as e:
                    print(f"\n❌ JSON parse error: {e}")
                    # Show problematic area
                    err_pos = e.pos if hasattr(e, 'pos') else 0
                    print(f"Error at position {err_pos}")
                    print(f"Context: ...{items_str[max(0,err_pos-50):err_pos+50]}...")
        else:
            print("jsonLocations not found!")

asyncio.run(debug_banette())
