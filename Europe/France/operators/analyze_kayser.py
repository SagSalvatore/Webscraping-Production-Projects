"""Analyze Kayser store structure - extract all stores with attributes."""
import re

with open('France/output/kayser_debug.html', 'r', encoding='utf-8') as f:
    html = f.read()

# Extract stores with their attributes
# Store item has: class="store-item ..." data-lat data-lon data-permalink
store_pattern = re.compile(
    r'<div\s+class="store-item[^"]*"\s+data-lat="([^"]+)"\s+data-lon="([^"]+)"\s+'
    r'data-permalink="([^"]+)"\s+data-store="([^"]+)"\s+data-pcode="([^"]*)"',
    re.IGNORECASE
)

stores = store_pattern.findall(html)
print(f"Found {len(stores)} stores with full attributes")

if stores:
    print("\nSample stores:")
    for s in stores[:5]:
        print(f"  Lat: {s[0]}, Lon: {s[1]}")
        print(f"  URL: {s[2]}")
        print(f"  Store: {s[3]}")
        print(f"  Pcode: {s[4]}")
        print()

# If pattern didn't match, try finding raw attributes
if not stores:
    print("\nTrying flexible extraction...")
    
    # Find all store-item divs
    div_pattern = re.compile(r'<div\s+class="store-item[^"]*"[^>]+>', re.IGNORECASE)
    divs = div_pattern.findall(html)
    print(f"Found {len(divs)} store-item divs")
    
    if divs:
        # Parse attributes from each div
        for div in divs[:3]:
            print(f"\nDiv: {div[:200]}...")
            
            lat = re.search(r'data-lat="([^"]+)"', div)
            lon = re.search(r'data-lon="([^"]+)"', div)
            url = re.search(r'data-permalink="([^"]+)"', div)
            store = re.search(r'data-store="([^"]+)"', div)
            pcode = re.search(r'data-pcode="([^"]*)"', div)
            
            if lat: print(f"  lat: {lat.group(1)}")
            if lon: print(f"  lon: {lon.group(1)}")
            if url: print(f"  url: {url.group(1)}")
            if store: print(f"  store: {store.group(1)}")
            if pcode: print(f"  pcode: {pcode.group(1)}")

# Extract all unique permalinks grouped by France section
print("\n" + "="*50)
print("FINDING FRANCE STORES BY SECTION")
print("="*50)

# Find the France section boundaries
# Look for "EN FRANCE" text and then the next country section
france_start = html.find('EN FRANCE')
# Find next country section (AFRIQUE, AMERIQUE, ASIE, EUROPE headings)
next_sections = []
for country in ['AFRIQUE', 'AMÉRIQUE', 'AMERIQUE', 'ASIE', 'EUROPE']:
    idx = html.find(f'>{country}<', france_start + 100)  # Skip France itself
    if idx != -1:
        next_sections.append(idx)

if next_sections:
    france_end = min(next_sections)
else:
    france_end = len(html)

print(f"France section: {france_start} to {france_end}")

# Extract store-items from France section only
france_html = html[france_start:france_end]

# Try various patterns for store items in this section
france_urls = re.findall(r'data-permalink="(https://maison-kayser\.com/boulangerie/[^"]+)"', france_html)
print(f"France URLs found: {len(france_urls)}")
for url in france_urls[:10]:
    print(f"  {url}")
