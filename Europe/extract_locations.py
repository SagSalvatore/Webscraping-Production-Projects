"""
Script to extract Location (City) from Address column using regex patterns.
Handles various German address formats.
"""
import pandas as pd
import re
from pathlib import Path
from loguru import logger

# Setup logger
logger.add("location_extraction.log", rotation="1 MB")


def extract_city_from_address(address: str) -> str:
    """
    Extract city name from German address format using regex.
    
    Common formats:
    1. "Street 123, 12345 City"
    2. "Street 123, 12345 City, Germany"
    3. "Street 123 / 12345 City"
    4. "Street 123, 12345 City-District"
    5. "12345 City" (just postal code and city)
    
    Args:
        address: Full address string
        
    Returns:
        Extracted city name or empty string if not found
    """
    if not address or pd.isna(address):
        return ""
    
    address = str(address).strip()
    
    # Pattern 1: German format with comma - "Street, 12345 City" or "Street, 12345 City, Germany"
    # The city follows the 5-digit postal code
    pattern1 = re.compile(
        r'[\,\s/]+\s*(\d{5})\s+([A-Za-zäöüßÄÖÜ\s\-\(\)\.]+?)(?:\s*,\s*(?:Germany|Deutschland))?$',
        re.IGNORECASE
    )
    match = pattern1.search(address)
    if match:
        city = match.group(2).strip()
        # Clean up common suffixes
        city = re.sub(r'\s*\(.*?\)\s*$', '', city)  # Remove parenthetical info
        return city.strip().strip(',').strip()
    
    # Pattern 2: Format "Street / 12345 City" (slash separator)
    pattern2 = re.compile(
        r'/\s*(\d{5})\s+([A-Za-zäöüßÄÖÜ\s\-\(\)\.]+)',
        re.IGNORECASE
    )
    match = pattern2.search(address)
    if match:
        city = match.group(2).strip()
        city = re.sub(r'\s*\(.*?\)\s*$', '', city)
        return city.strip().strip(',').strip()
    
    # Pattern 3: Just "12345 City" anywhere in the string
    pattern3 = re.compile(
        r'(\d{5})\s+([A-Za-zäöüßÄÖÜ][A-Za-zäöüßÄÖÜ\s\-\.]+)',
        re.IGNORECASE
    )
    match = pattern3.search(address)
    if match:
        city = match.group(2).strip()
        # Remove trailing comma or country
        city = re.sub(r',?\s*(?:Germany|Deutschland).*$', '', city, flags=re.IGNORECASE)
        city = re.sub(r'\s*\(.*?\)\s*$', '', city)
        return city.strip().strip(',').strip()
    
    # Pattern 4: Try to extract city from formats like "City-District"
    # If we find a known German city prefix
    cities_pattern = re.compile(
        r'\b(Berlin|München|Munich|Hamburg|Köln|Cologne|Frankfurt|Stuttgart|Düsseldorf|'
        r'Dortmund|Essen|Leipzig|Bremen|Dresden|Hannover|Nürnberg|Nuremberg|Duisburg|'
        r'Bochum|Wuppertal|Bonn|Mannheim|Bielefeld|Karlsruhe|Mainz|Wiesbaden|Münster|'
        r'Kiel|Magdeburg|Rostock|Lübeck|Erfurt|Halle|Potsdam|Heidelberg|Darmstadt|'
        r'Offenbach|Worms|Speyer|Ludwigshafen|Kassel|Aachen|Braunschweig|Koblenz|'
        r'Freiburg|Augsburg|Chemnitz|Schwerin|Trier|Würzburg|Ulm|Saarbrücken|'
        r'Ingolstadt|Wolfsburg|Osnabrück|Oldenburg|Leverkusen|Hildesheim|Salzgitter|'
        r'Pforzheim|Reutlingen|Heilbronn|Göttingen|Paderborn|Kaiserslautern|Cottbus|'
        r'Gera|Jena|Weimar|Brandenburg|Dessau|Zwickau|Plauen|Görlitz)[A-Za-zäöüßÄÖÜ\-\s]*',
        re.IGNORECASE
    )
    match = cities_pattern.search(address)
    if match:
        return match.group(0).strip().strip(',').strip()
    
    return ""


def process_csv(input_path: str, output_path: str = None) -> pd.DataFrame:
    """
    Process CSV file and extract locations from addresses.
    
    Args:
        input_path: Path to input CSV file
        output_path: Optional path for output CSV
        
    Returns:
        DataFrame with extracted locations
    """
    logger.info(f"Loading CSV from {input_path}")
    df = pd.read_csv(input_path)
    
    # Identify the address column
    address_col = None
    for col in df.columns:
        if 'address' in col.lower():
            address_col = col
            break
    
    if not address_col:
        logger.error("No address column found!")
        return df
    
    logger.info(f"Using address column: '{address_col}'")
    
    # Extract locations
    extracted_locations = []
    empty_count = 0
    
    for idx, row in df.iterrows():
        address = row[address_col]
        city = extract_city_from_address(address)
        extracted_locations.append(city)
        
        if not city:
            empty_count += 1
            logger.debug(f"Could not extract city from: {address}")
    
    # Update or create Locations column
    df['Locations'] = extracted_locations
    
    logger.info(f"Processed {len(df)} rows")
    logger.info(f"Successfully extracted: {len(df) - empty_count} ({100*(len(df)-empty_count)/len(df):.1f}%)")
    logger.info(f"Could not extract: {empty_count} ({100*empty_count/len(df):.1f}%)")
    
    # Save output
    if output_path:
        if output_path.endswith('.xlsx'):
            df.to_excel(output_path, index=False)
        else:
            df.to_csv(output_path, index=False)
        logger.info(f"Saved to {output_path}")
    
    return df


if __name__ == "__main__":
    # Process the SAMPLE.csv file
    input_file = Path("SAMPLE.csv")
    output_file = Path("SAMPLE_with_locations.xlsx")
    
    df = process_csv(str(input_file), str(output_file))
    
    # Show some results
    print("\n" + "="*60)
    print("Sample Results:")
    print("="*60)
    print(df[['Bakery/Company Name', 'Locations']].head(20).to_string())
    
    # Show extraction statistics
    print("\n" + "="*60)
    print("Location Extraction Statistics:")
    print("="*60)
    print(f"Total rows: {len(df)}")
    print(f"Locations extracted: {df['Locations'].notna().sum() - (df['Locations'] == '').sum()}")
    print(f"Unique locations: {df[df['Locations'] != '']['Locations'].nunique()}")
    
    # Show top cities
    print("\nTop 15 Cities:")
    print(df[df['Locations'] != '']['Locations'].value_counts().head(15))
