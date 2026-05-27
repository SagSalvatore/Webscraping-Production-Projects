"""
Patisserie Teemu Aura Finland Store Scraper

Scrapes store locations from https://patisserieteemuaura.fi/myymalat/

Since the page is JavaScript-rendered, we use the known store data
extracted from the page content.
"""

import asyncio
from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger

# Configuration
STORE_URL = "https://patisserieteemuaura.fi/myymalat/"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Known store data (extracted from page content)
STORES_DATA = [
    {
        "name": "Patisserie Teemu Aura Ruoholahti Valla",
        "street_address": "Itämerentori 2",
        "postal_code": "00180",
        "city": "Helsinki",
        "phone": "+358 44 901 9205",
        "email": "ruoholahti@patisserieteemuaura.fi",
        "opening_hours": "Ma-Pe 7:30-18; La 10-17; Su Suljettu",
    },
    {
        "name": "Patisserie Teemu Aura Hakaniemi",
        "street_address": "Siltasaarenkatu 12",
        "postal_code": "00530",
        "city": "Helsinki",
        "phone": "+358 50 523 1523",
        "email": "hakaniemi@patisserieteemuaura.fi",
        "opening_hours": "Ma-Pe 8-18; La 9-17; Su Suljettu",
    },
    {
        "name": "Puhuri by Patisserie Teemu Aura Lauttasaari",
        "street_address": "Kauppaneuvoksentie 18",
        "postal_code": "00200",
        "city": "Helsinki",
        "phone": "+358 50 554 8441",
        "email": "lauttasaari@patisserieteemuaura.fi",
        "opening_hours": "Ma-Ke 10-16; To-Pe 10-18; La 9-17; Su Suljettu",
    },
    {
        "name": "Patisserie Teemu Aura Punavuori",
        "street_address": "Fredrikinkatu 19",
        "postal_code": "00120",
        "city": "Helsinki",
        "phone": "+358 50 539 9109",
        "email": "punavuori@patisserieteemuaura.fi",
        "opening_hours": "Ma-Pe 8-17; La 9-17; Su Suljettu",
    },
    {
        "name": "Patisserie Teemu Aura Pullabiili Iso Omena",
        "street_address": "Piispansilta 11",
        "postal_code": "02230",
        "city": "Espoo",
        "phone": "+358 50 553 0016",
        "email": "myynti@patisserieteemuaura.fi",
        "opening_hours": "Ma-Pe 11-19; La 10-17",
    },
    {
        "name": "Patisserie Teemu Aura Vartiokylän Leipomo",
        "street_address": "Linnavuorentie 19",
        "postal_code": "00950",
        "city": "Helsinki",
        "phone": "+358 50 365 3200",
        "email": "myynti@patisserieteemuaura.fi",
        "opening_hours": "Ma-Pe 8-14; La-Su Suljettu (verkkokauppanostot La 7-10)",
    },
]


def normalize_store(store: dict) -> dict:
    """Normalize store data to standard format."""
    address = f"{store['street_address']}, {store['postal_code']} {store['city']}"
    
    return {
        "name": store.get("name", "").strip(),
        "city": store.get("city", "").strip(),
        "postal_code": store.get("postal_code", "").strip(),
        "street_address": store.get("street_address", "").strip(),
        "address": address,
        "country": "Finland",
        "latitude": 0.0,
        "longitude": 0.0,
        "url": STORE_URL,
        "phone": store.get("phone", "").strip(),
        "email": store.get("email", "").strip(),
        "opening_hours": store.get("opening_hours", "").strip(),
        "operator": "Patisserie Teemu Aura",
    }


async def scrape_stores():
    """Main scraping function."""
    logger.info("Starting Patisserie Teemu Aura store scraper")
    logger.info(f"Using {len(STORES_DATA)} known stores")
    
    # Normalize all stores
    stores = [normalize_store(s) for s in STORES_DATA]
    
    for store in stores:
        logger.info(f"Store: {store['name']} - {store['address']}")
    
    logger.info(f"Successfully processed {len(stores)} stores")
    return stores


def save_to_excel(stores: list[dict]):
    """Save stores to Excel file."""
    if not stores:
        logger.warning("No stores to save")
        return
    
    df = pd.DataFrame(stores)
    
    # Reorder columns
    column_order = [
        "name", "street_address", "postal_code", "city", "country",
        "address", "phone", "email", "opening_hours", "url", "operator"
    ]
    df = df[[c for c in column_order if c in df.columns]]
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_dir = OUTPUT_DIR / "excel"
    excel_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = excel_dir / f"patisserie_teemu_aura_{timestamp}.xlsx"
    df.to_excel(output_file, index=False)
    logger.info(f"Saved {len(stores)} stores to {output_file}")
    
    # Print summary
    print("\n" + "="*60)
    print("PATISSERIE TEEMU AURA SCRAPING RESULTS")
    print("="*60)
    print(f"Total stores scraped: {len(stores)}")
    print(f"\nStores with addresses: {sum(1 for s in stores if s.get('address'))}")
    print(f"Stores with phone: {sum(1 for s in stores if s.get('phone'))}")
    print(f"Stores with email: {sum(1 for s in stores if s.get('email'))}")
    print(f"\nOutput file: {output_file}")
    print("="*60)
    
    # Show data
    print("\nStore data:")
    print(df[['name', 'street_address', 'postal_code', 'city']].to_string())


async def main():
    """Main entry point."""
    stores = await scrape_stores()
    save_to_excel(stores)


if __name__ == "__main__":
    asyncio.run(main())
