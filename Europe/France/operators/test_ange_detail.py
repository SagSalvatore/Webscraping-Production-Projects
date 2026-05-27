"""Check if Ange detail pages have more data."""
import asyncio
from curl_cffi.requests import AsyncSession
import re
import json

async def check_detail_page():
    url = 'https://www.boulangerie-ange.fr/stores/boulangerie-ange-riom/'
    async with AsyncSession(impersonate='chrome120') as s:
        r = await s.get(url, timeout=30)
        html = r.text
        
        print(f"Page length: {len(html)}")
        
        # Look for LD+JSON
        ld_pattern = re.compile(
            r'<script\s+type=["\']application/ld\+json["\']>\s*([\s\S]*?)</script>',
            re.IGNORECASE
        )
        matches = ld_pattern.findall(html)
        
        if matches:
            print(f'Found {len(matches)} LD+JSON blocks')
            for i, m in enumerate(matches):
                try:
                    data = json.loads(m)
                    print(f'\nBlock {i+1}:')
                    print(json.dumps(data, indent=2)[:2000])
                except Exception as e:
                    print(f'Block {i+1}: Parse error - {e}')
        else:
            print('No LD+JSON found')
        
        # Look for address in other patterns
        print("\n--- Looking for address ---")
        
        # Check for tel/phone
        tel = re.findall(r'tel:([^"\'<]+)', html)
        if tel:
            print(f"Phone: {tel[:3]}")
        
        # Look for postal code pattern (French: 5 digits)
        postal = re.findall(r'\b(\d{5})\b', html[:10000])
        if postal:
            print(f"Postal codes found: {list(set(postal))[:5]}")

asyncio.run(check_detail_page())
