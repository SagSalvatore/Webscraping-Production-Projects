import json, re, os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('.env')
from curl_cffi.requests import Session

url = 'https://www.talabat.com/uae/restaurant/626019/zaatar-w-zeit-sheikh-zayed-road-jumeirah-1'

oxy_user = os.getenv('OXYLABS_USERNAME', '')
oxy_pass = os.getenv('OXYLABS_PASSWORD', '')
proxy_url = None
if oxy_user and oxy_pass:
    proxy_url = f'http://customer-{oxy_user}-cc-ae-sessid-debug01:{oxy_pass}@pr.oxylabs.io:7777'

with Session(impersonate='chrome124') as s:
    r = s.get(
        url,
        proxies={'http': proxy_url, 'https': proxy_url} if proxy_url else None,
        timeout=20,
        headers={'referer': 'https://www.talabat.com/uae/restaurants'}
    )
    print('Status:', r.status_code)
    html = r.text

# 1. Title tag
m = re.search(r'<title>(.*?)</title>', html)
print('Title:', m.group(1) if m else 'NOT FOUND')

# 2. og:title
m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
print('og:title:', m.group(1) if m else 'NOT FOUND')

# 3. Schema.org JSON-LD
ld_blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
print(f'\nJSON-LD blocks found: {len(ld_blocks)}')
for i, block in enumerate(ld_blocks):
    try:
        d = json.loads(block)
        print(f'  Block {i}: type={d.get("@type","?")} keys={list(d.keys())[:8]}')
        if 'geo' in d:
            print(f'    geo: {d["geo"]}')
        if 'latitude' in d or 'longitude' in d:
            print(f'    lat={d.get("latitude")} lon={d.get("longitude")}')
        if 'name' in d:
            print(f'    name: {d["name"]}')
        if 'address' in d:
            print(f'    address: {d["address"]}')
    except Exception as e:
        print(f'  Block {i}: parse error {e}')

# 4. Look for latitude/longitude anywhere in the HTML
lat_m = re.findall(r'"latitude"\s*:\s*([\d.]+)', html)
lon_m = re.findall(r'"longitude"\s*:\s*([\d.]+)', html)
print(f'\nLatitude values found: {lat_m[:5]}')
print(f'Longitude values found: {lon_m[:5]}')

# 5. Look for "lat" or coordinates patterns
coord_m = re.findall(r'"lat"\s*:\s*([\d.]+)', html)
print(f'lat values found: {coord_m[:5]}')

# 6. Try Talabat's internal API endpoint
print('\n--- Trying internal API ---')
api_url = f'https://www.talabat.com/api/restaurant/626019'
r2 = s.get(api_url, proxies={'http': proxy_url, 'https': proxy_url} if proxy_url else None,
           timeout=10, headers={'referer': url})
print(f'API status: {r2.status_code}')
if r2.status_code == 200:
    try:
        d = r2.json()
        print('API response keys:', list(d.keys())[:10])
    except Exception:
        print('API response (text):', r2.text[:300])
