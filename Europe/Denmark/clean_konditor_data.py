"""
Clean and Deduplicate KonditorBager Data
Reads existing JSON, filters noise and duplicates, and saves clean version.
"""
import json
from pathlib import Path
import pandas as pd
from loguru import logger

def clean_data():
    # Input file
    input_path = Path(__file__).parent / "output" / "json" / "konditor_bager_20251229_154247.json"
    
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    logger.info(f"Loaded {len(data)} entries")
    
    unique_stores = {}
    cnt_noise = 0
    cnt_prod = 0
    cnt_dup = 0
    
    for item in data:
        name = item.get("name", "")
        address = item.get("address", "")
        city = item.get("city", "")
        
        # 1. Filter Noise
        if "EuroSkills" in address or "Bager fra Ørum" in address or address == "N/A":
            cnt_noise += 1
            continue
            
        # 2. Use Address as Unique Key
        # This implicitly deduplicates multiple pages for the same store
        key = address.strip()
        
        # Determine if current name is "Generic" (Product name)
        is_generic_name = any(x in name.lower() for x in ["rundstykker", "boller", "kagemand", "10", "16"])
        
        # Clean name if generic
        if is_generic_name and city:
            # Construct a better name from City
            # e.g. "KonditorBager Aabenraa"
             cleaned_name = f"KonditorBager {city}"
             item["name"] = cleaned_name
             is_generic_name = False # It is now clean
        
        if key not in unique_stores:
            unique_stores[key] = item
        else:
            # If we already have this store, check if the current one has a 'better' (original) name
            # But since we clean names above, this might inevitably end up same.
            # However, if we found a "Real" store page with a valid address later, we might prefer its metadata.
            # For now, first valid find is fine, or overwrite if current is NOT generic and stored IS generic (rare case with our logic).
            pass

    clean_results = list(unique_stores.values())
    
    logger.info(f"Noise entries ignored: {cnt_noise}")
    logger.success(f"Final unique stores: {len(clean_results)}")
    
    # Save Output
    output_dir = input_path.parent
    excel_dir = input_path.parent.parent / "excel"
    
    output_json = output_dir / "konditor_bager_CLEANED.json"
    output_excel = excel_dir / "konditor_bager_CLEANED.xlsx"
    
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(clean_results, f, ensure_ascii=False, indent=2)
    logger.success(f"Saved JSON: {output_json}")
    
    df = pd.DataFrame(clean_results)
    df.to_excel(output_excel, index=False)
    logger.success(f"Saved Excel: {output_excel}")
    
    # Print sample names to verify
    logger.info("Sample Cleaned Stores:")
    for s in clean_results[:10]:
        logger.info(f"- {s['name']} -> {s['address']}")

if __name__ == "__main__":
    clean_data()
