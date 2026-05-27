"""Analyze Sophie Lebreuilly iframe HTML structure."""
import re

html = open('France/output/sophie_iframe.html', 'r', encoding='utf-8').read()

# Find all li elements containing stores
li_pattern = re.compile(
    r'<li[^>]*bis_size[^>]*>([\s\S]*?)</li>',
    re.IGNORECASE
)

matches = li_pattern.findall(html)
print(f"Found {len(matches)} li elements")

# Look for store-wrapper divs
wrappers = re.findall(r'<div class="store-wrapper"[^>]*>', html)
print(f"Store wrappers: {len(wrappers)}")

# Extract data from each wrapper
for i, wrapper in enumerate(wrappers[:2]):
    print(f"\n--- Store {i+1} ---")
    print(wrapper)

# Find title elements
titles = re.findall(r'<h5[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</h5>', html)
print(f"\nTitles: {len(titles)}")
for t in titles[:5]:
    print(f"  {t.strip()}")

# Find addresses - look for specific patterns
# From the screenshot, addresses seem to be in specific divs
addr_divs = re.findall(r'<div[^>]*>([^<]*(?:rue|avenue|place|boulevard)[^<]*)</div>', html, re.IGNORECASE)
print(f"\nAddress-like divs: {len(addr_divs)}")
for a in addr_divs[:5]:
    print(f"  {a.strip()}")

# Check for any text containing postal codes
postal_matches = re.findall(r'(\d{5}[^<]{0,50})', html)
print(f"\nPostal code patterns: {len(postal_matches)}")
for p in postal_matches[:5]:
    print(f"  {p}")
