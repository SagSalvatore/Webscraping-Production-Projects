"""
Scrape missing Wayne's Coffee URLs - these are city listing pages.
Extracts cafe links from listing pages and scrapes JSON-LD from each.
"""
import asyncio
import re
import json
import pandas as pd
from pathlib import Path
from datetime import datetime
from curl_cffi.requests import AsyncSession
from loguru import logger

# City listing pages that contain links to individual cafes
CITY_PAGES = [
    "https://www.waynescoffee.se/kafeer/halmstad/",
    "https://www.waynescoffee.se/kafeer/helsingborg/",
    "https://www.waynescoffee.se/kafeer/malmo/",
    "https://www.waynescoffee.se/kafeer/solna/",
    "https://www.waynescoffee.se/kafeer/stockholm/",
    "https://www.waynescoffee.se/kafeer/sundsvall/",
]

def extract_json_ld(html: str) -> dict:
    """Extract JSON-LD data from HTML."""
    pattern = r'<script\s+type="application/ld\+json"[^>]*>([\s\S]*?)</script>'
    matches = re.findall(pattern, html, re.IGNORECASE)
    
    for match in matches:
        try:
            data = json.loads(match.strip())
            if isinstance(data, dict) and data.get("@type") in ["CafeOrCoffeeShop", "Restaurant", "LocalBusiness"]:
                return data
        except json.JSONDecodeError:
            continue
    return {}

def normalize_store(data: dict, url: str) -> dict:
    """Normalize JSON-LD store data to standard format."""
    address = data.get("address", {})
    geo = data.get("geo", {})
    
    try:
        lat = float(geo.get("latitude", 0))
        lng = float(geo.get("longitude", 0))
    except (ValueError, TypeError):
        lat = 0.0
        lng = 0.0
    
    hours_spec = data.get("openingHoursSpecification", [])
    opening_hours = {}
    for spec in hours_spec:
        day = spec.get("dayOfWeek", "")
        opens = spec.get("opens", "")
        closes = spec.get("closes", "")
        if day and opens and closes:
            opening_hours[day] = f"{opens}-{closes}"
    
    postal_code = address.get("postalCode", "")
    if isinstance(postal_code, str):
        postal_code = postal_code.replace(" ", "")
    
    return {
        "name": data.get("name", ""),
        "city": address.get("addressLocality", ""),
        "postal_code": postal_code,
        "street_address": address.get("streetAddress", ""),
        "address": f"{address.get('streetAddress', '')}, {address.get('postalCode', '')} {address.get('addressLocality', '')}".strip(", "),
        "country": "Sweden",
        "latitude": lat,
        "longitude": lng,
        "url": data.get("url", url),
        "phone": data.get("telephone", ""),
        "email": "",
        "opening_hours": opening_hours,
        "operator": "Wayne's Coffee",
    }

async def main():
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO",
        colorize=True,
    )
    
    logger.info("=" * 60)
    logger.info("Scraping Missing Wayne's Coffee City Pages")
    logger.info("=" * 60)
    
    all_cafe_links = set()
    
    async with AsyncSession(impersonate="chrome120") as session:
        # Step 1: Get all cafe links from city pages
        for city_url in CITY_PAGES:
            logger.info(f"Fetching city page: {city_url}")
            try:
                r = await session.get(city_url, timeout=30)
                if r.status_code == 200:
                    # Find cafe links
                    links = re.findall(r'href="(https://www\.waynescoffee\.se/kafe/[^"]+)"', r.text)
                    unique_links = set(links)
                    all_cafe_links.update(unique_links)
                    logger.success(f"  Found {len(unique_links)} cafe links")
            except Exception as e:
                logger.error(f"  Error: {e}")
        
        logger.info(f"\nTotal unique cafe links found: {len(all_cafe_links)}")
        
        # Load already scraped URLs to avoid duplicates
        existing_file = Path("Sweden/output/excel/waynes_coffee_20251226_132706.xlsx")
        existing_urls = set()
        if existing_file.exists():
            existing_df = pd.read_excel(existing_file)
            existing_urls = set(existing_df['url'].dropna().tolist())
            logger.info(f"Already scraped: {len(existing_urls)} URLs")
        
        # Filter to only new URLs
        new_links = all_cafe_links - existing_urls
        logger.info(f"New URLs to scrape: {len(new_links)}")
        
        # Step 2: Scrape each new cafe link
        results = []
        for i, url in enumerate(sorted(new_links), 1):
            logger.info(f"[{i}/{len(new_links)}] Fetching: {url}")
            try:
                r = await session.get(url, timeout=30)
                if r.status_code == 200:
                    data = extract_json_ld(r.text)
                    if data:
                        store = normalize_store(data, url)
                        results.append(store)
                        logger.success(f"  Found: {store['name']}")
                    else:
                        logger.warning(f"  No JSON-LD data")
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.error(f"  Error: {e}")
        
        # Save results
        if results:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = Path(f"Sweden/output/excel/waynes_coffee_additional_{timestamp}.xlsx")
            
            flat_results = []
            for r in results:
                flat = r.copy()
                hours = flat.pop("opening_hours", {})
                flat["opening_hours_json"] = json.dumps(hours, ensure_ascii=False)
                flat_results.append(flat)
            
            df = pd.DataFrame(flat_results)
            df.to_excel(output_path, index=False)
            logger.success(f"\n✅ Saved {len(results)} additional cafes to {output_path}")
        else:
            logger.info("\nNo new cafes found to add")

if __name__ == "__main__":
    asyncio.run(main())
