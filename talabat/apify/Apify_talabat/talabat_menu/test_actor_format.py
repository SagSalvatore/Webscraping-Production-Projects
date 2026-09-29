"""Quick test script to understand the Apify talabat-scraper actor input/output."""
import os
import json
from pathlib import Path
from apify_client import ApifyClient
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")
token = os.getenv("APIFY_API_TOKEN", "")
client = ApifyClient(token)

# Test 1: Query-based search to see the slug format in output
print("=" * 60)
print("TEST 1: Query-based search (2 results)")
print("=" * 60)

run_input = {
    "queries": ["pizza hut"],
    "restaurantSlugs": [],
    "country": "uae",
    "maxResults": 2,
    "scrapeMenu": True,
    "scrapeMenuChoices": False,
}

run = client.actor("thirdwatch/talabat-scraper").call(run_input=run_input)
dataset_id = getattr(run, "default_dataset_id", "") if not isinstance(run, dict) else run.get("defaultDatasetId", "")
items = list(client.dataset(dataset_id).iterate_items())
print(f"Items returned: {len(items)}")

if items:
    r = items[0]
    print(f"Name: {r.get('name')}")
    print(f"Slug: {r.get('slug')}")
    print(f"URL: {r.get('url')}")
    menu = r.get("menu_items", [])
    print(f"Menu items: {len(menu)}")
    if menu:
        print(f"First menu item: {json.dumps(menu[0], indent=2, ensure_ascii=False)[:500]}")
    
    # Now test slug-based scrape using the slug we got
    actual_slug = r.get("slug", "")
    print(f"\n{'=' * 60}")
    print(f"TEST 2: Slug-based search using slug: {actual_slug}")
    print(f"{'=' * 60}")
    
    run_input2 = {
        "queries": [],
        "restaurantSlugs": [actual_slug],
        "country": "uae",
        "maxResults": 20,
        "scrapeMenu": True,
        "scrapeMenuChoices": False,
    }
    
    run2 = client.actor("thirdwatch/talabat-scraper").call(run_input=run_input2)
    dataset_id2 = getattr(run2, "default_dataset_id", "") if not isinstance(run2, dict) else run2.get("defaultDatasetId", "")
    items2 = list(client.dataset(dataset_id2).iterate_items())
    print(f"Items returned from slug search: {len(items2)}")
    if items2:
        print(f"Name: {items2[0].get('name')}")
        print(f"Slug: {items2[0].get('slug')}")

# Test 3: Try with a URL-derived slug
print(f"\n{'=' * 60}")
print("TEST 3: URL-derived slug")
print("=" * 60)

test_url = "https://www.talabat.com/uae/restaurant/611383/texas-de-brazil-downtown-burj-khalifa?aid=1176"
from urllib.parse import urlparse
parsed = urlparse(test_url)
slug_from_url = parsed.path.rstrip("/").split("/")[-1]
print(f"Slug from URL: {slug_from_url}")

run_input3 = {
    "queries": [],
    "restaurantSlugs": [slug_from_url],
    "country": "uae",
    "maxResults": 20,
    "scrapeMenu": True,
    "scrapeMenuChoices": False,
}

run3 = client.actor("thirdwatch/talabat-scraper").call(run_input=run_input3)
dataset_id3 = getattr(run3, "default_dataset_id", "") if not isinstance(run3, dict) else run3.get("defaultDatasetId", "")
items3 = list(client.dataset(dataset_id3).iterate_items())
print(f"Items returned: {len(items3)}")
if items3:
    print(f"Name: {items3[0].get('name')}")
    print(f"Menu items: {len(items3[0].get('menu_items', []))}")
else:
    print("No results - slug format may not match")

# Test 4: Try with restaurant ID + slug format
print(f"\n{'=' * 60}")
print("TEST 4: Full path segment (ID/slug)")
print("=" * 60)

# Maybe the actor needs "611383/texas-de-brazil-downtown-burj-khalifa"
parts = parsed.path.strip("/").split("/")
# parts = ['uae', 'restaurant', '611383', 'texas-de-brazil-downtown-burj-khalifa']
if len(parts) >= 4:
    full_slug = f"{parts[2]}/{parts[3]}"
    print(f"Full slug: {full_slug}")
    
    run_input4 = {
        "queries": [],
        "restaurantSlugs": [full_slug],
        "country": "uae",
        "maxResults": 20,
        "scrapeMenu": True,
        "scrapeMenuChoices": False,
    }
    
    run4 = client.actor("thirdwatch/talabat-scraper").call(run_input=run_input4)
    dataset_id4 = getattr(run4, "default_dataset_id", "") if not isinstance(run4, dict) else run4.get("defaultDatasetId", "")
    items4 = list(client.dataset(dataset_id4).iterate_items())
    print(f"Items returned: {len(items4)}")
    if items4:
        print(f"Name: {items4[0].get('name')}")
