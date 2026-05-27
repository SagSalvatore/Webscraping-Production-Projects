"""
Combine all France bakery Excel files into one master sheet.
Output columns: Name, City, Address, Urls
"""
import pandas as pd
from pathlib import Path
from datetime import datetime

# Directories
excel_dir = Path(__file__).parent / "output" / "excel"
output_dir = excel_dir.parent

# Column mapping for each file type
column_mappings = {
    "name": ["name", "Name"],
    "city": ["city", "City"],
    "address": ["address", "full_address", "street_address", "Address"],
    "url": ["url", "urls", "Url", "Urls", "website"],
}

def find_column(df, possible_names):
    """Find a column by possible name variations."""
    for name in possible_names:
        if name in df.columns:
            return name
    return None

def process_file(filepath):
    """Process a single Excel file and extract required columns."""
    try:
        df = pd.read_excel(filepath)
        print(f"  Loaded {len(df)} rows from {filepath.name}")
        
        # Find columns
        name_col = find_column(df, column_mappings["name"])
        city_col = find_column(df, column_mappings["city"])
        address_col = find_column(df, column_mappings["address"])
        url_col = find_column(df, column_mappings["url"])
        
        # Extract data
        result = pd.DataFrame()
        result["Name"] = df[name_col] if name_col else ""
        result["City"] = df[city_col] if city_col else ""
        result["Address"] = df[address_col] if address_col else ""
        result["Urls"] = df[url_col] if url_col else ""
        
        # Add source file for reference
        result["Source"] = filepath.stem
        
        return result
    except Exception as e:
        print(f"  Error processing {filepath.name}: {e}")
        return pd.DataFrame()

def main():
    print("=" * 60)
    print("Combining France Bakery Excel Files")
    print("=" * 60)
    
    # Get all Excel files
    excel_files = list(excel_dir.glob("*.xlsx"))
    print(f"\nFound {len(excel_files)} Excel files:")
    for f in excel_files:
        print(f"  - {f.name}")
    
    # Process each file
    all_data = []
    for filepath in excel_files:
        print(f"\nProcessing: {filepath.name}")
        df = process_file(filepath)
        if len(df) > 0:
            all_data.append(df)
    
    # Combine all data
    if all_data:
        combined = pd.concat(all_data, ignore_index=True)
        
        print(f"\n{'=' * 60}")
        print(f"Total combined records: {len(combined)}")
        print(f"{'=' * 60}")
        
        # Summary by source
        print("\nRecords by operator:")
        for source, count in combined["Source"].value_counts().items():
            print(f"  {source}: {count}")
        
        # Save to France_Final.xlsx
        output_path = output_dir / "France_Final.xlsx"
        
        # Save with just the 4 main columns
        final_df = combined[["Name", "City", "Address", "Urls"]]
        final_df.to_excel(output_path, index=False)
        
        print(f"\n✅ Saved to: {output_path}")
        print(f"Total outlets: {len(final_df)}")
    else:
        print("No data to combine!")

if __name__ == "__main__":
    main()
