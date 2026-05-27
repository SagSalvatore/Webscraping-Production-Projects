"""
Data exporters for saving outlet data to JSON and Excel formats.
"""
import json
from pathlib import Path
from typing import Any, Union
from datetime import datetime

import pandas as pd
from loguru import logger


def save_to_json(
    data: Union[list, dict],
    filepath: Union[str, Path],
    indent: int = 2,
    ensure_ascii: bool = False,
) -> Path:
    """
    Save data to a JSON file.
    
    Args:
        data: Data to save (list or dict)
        filepath: Output file path
        indent: JSON indentation level
        ensure_ascii: Whether to escape non-ASCII characters
        
    Returns:
        Path to the saved file
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii, default=str)
    
    logger.info(f"Saved JSON data to {filepath}")
    return filepath


def save_to_excel(
    data: Union[list[dict], pd.DataFrame],
    filepath: Union[str, Path],
    sheet_name: str = "Outlets",
    index: bool = False,
) -> Path:
    """
    Save data to an Excel file.
    
    Args:
        data: Data to save (list of dicts or DataFrame)
        filepath: Output file path
        sheet_name: Name of the Excel sheet
        index: Whether to include DataFrame index
        
    Returns:
        Path to the saved file
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    if isinstance(data, list):
        df = pd.DataFrame(data)
    else:
        df = data
    
    # Clean up columns that might contain complex objects
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].apply(
                lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x
            )
    
    df.to_excel(filepath, sheet_name=sheet_name, index=index, engine="openpyxl")
    logger.info(f"Saved Excel data to {filepath} ({len(df)} rows)")
    return filepath


def export_outlets(
    outlets: list[dict],
    operator_name: str,
    country: str,
    output_dir: Union[str, Path],
    columns: list[str] = None,
) -> tuple[Path, Path]:
    """
    Export outlet data to both JSON and Excel formats.
    
    Args:
        outlets: List of outlet dictionaries
        operator_name: Name of the bakery operator
        country: Country name
        output_dir: Base output directory for the country
        columns: Optional list of columns to include in Excel
        
    Returns:
        Tuple of (json_path, excel_path)
    """
    output_dir = Path(output_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Create safe filename
    safe_name = operator_name.lower().replace(" ", "_").replace("/", "_")
    
    # JSON output
    json_dir = output_dir / "json"
    json_path = json_dir / f"{safe_name}_{timestamp}.json"
    save_to_json(outlets, json_path)
    
    # Excel output
    excel_dir = output_dir / "excel"
    excel_path = excel_dir / f"{safe_name}_{timestamp}.xlsx"
    
    # Select and order columns for Excel
    if columns is None:
        columns = [
            "name",
            "address",
            "street_address", 
            "postal_code",
            "city",
            "region",
            "country",
            "phone",
            "email",
            "latitude",
            "longitude",
            "opening_hours",
            "url",
        ]
    
    # Filter outlets to only include specified columns (if they exist)
    export_data = []
    for outlet in outlets:
        row = {col: outlet.get(col, "") for col in columns if col in outlet or col in columns}
        export_data.append(row)
    
    save_to_excel(export_data, excel_path, sheet_name=operator_name[:31])  # Excel sheet name max 31 chars
    
    logger.success(f"Exported {len(outlets)} outlets for {operator_name} ({country})")
    return json_path, excel_path


def merge_country_outputs(
    country: str,
    output_dir: Union[str, Path],
) -> tuple[Path, Path]:
    """
    Merge all operator outputs for a country into single files.
    
    Args:
        country: Country name
        output_dir: Base output directory for the country
        
    Returns:
        Tuple of (merged_json_path, merged_excel_path)
    """
    output_dir = Path(output_dir)
    json_dir = output_dir / "json"
    
    all_outlets = []
    
    # Read all JSON files
    for json_file in json_dir.glob("*.json"):
        if json_file.name.startswith("merged_"):
            continue
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                all_outlets.extend(data)
    
    if not all_outlets:
        logger.warning(f"No outlet data found for {country}")
        return None, None
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Save merged JSON
    merged_json = json_dir / f"merged_{country.lower()}_{timestamp}.json"
    save_to_json(all_outlets, merged_json)
    
    # Save merged Excel
    merged_excel = output_dir / "excel" / f"merged_{country.lower()}_{timestamp}.xlsx"
    save_to_excel(all_outlets, merged_excel, sheet_name=f"All {country} Outlets")
    
    logger.success(f"Merged {len(all_outlets)} outlets for {country}")
    return merged_json, merged_excel
