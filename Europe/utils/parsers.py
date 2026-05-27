"""
LD+JSON and structured data parsers for extracting outlet information.
"""
import json
import re
from typing import Any, Optional
from bs4 import BeautifulSoup
from loguru import logger


class LDJsonParser:
    """Parser for extracting and processing LD+JSON structured data."""
    
    # Schema types commonly used for store/restaurant locations
    LOCATION_SCHEMA_TYPES = [
        "LocalBusiness",
        "Restaurant", 
        "FoodEstablishment",
        "Bakery",
        "CafeOrCoffeeShop",
        "Store",
        "Place",
        "Organization",
    ]
    
    @staticmethod
    def extract_all_ld_json(html: str) -> list[dict]:
        """
        Extract all LD+JSON scripts from HTML content.
        
        Args:
            html: Raw HTML content
            
        Returns:
            List of parsed JSON objects
        """
        soup = BeautifulSoup(html, "lxml")
        ld_json_scripts = soup.find_all("script", type="application/ld+json")
        
        results = []
        for script in ld_json_scripts:
            try:
                content = script.string
                if content:
                    # Clean up potential issues
                    content = content.strip()
                    data = json.loads(content)
                    
                    # Handle @graph structures
                    if isinstance(data, dict) and "@graph" in data:
                        results.extend(data["@graph"])
                    elif isinstance(data, list):
                        results.extend(data)
                    else:
                        results.append(data)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse LD+JSON: {e}")
                continue
        
        return results
    
    @classmethod
    def extract_locations(cls, html: str) -> list[dict]:
        """
        Extract location/store data from LD+JSON.
        
        Args:
            html: Raw HTML content
            
        Returns:
            List of location dictionaries with normalized fields
        """
        all_ld = cls.extract_all_ld_json(html)
        locations = []
        
        for item in all_ld:
            if not isinstance(item, dict):
                continue
                
            schema_type = item.get("@type", "")
            
            # Handle array of types
            if isinstance(schema_type, list):
                schema_types = schema_type
            else:
                schema_types = [schema_type]
            
            # Check if this is a location-related schema
            if any(t in cls.LOCATION_SCHEMA_TYPES for t in schema_types):
                location = cls._normalize_location(item)
                if location:
                    locations.append(location)
        
        return locations
    
    @staticmethod
    def _normalize_location(item: dict) -> Optional[dict]:
        """
        Normalize a location item to a standard format.
        
        Args:
            item: Raw LD+JSON location item
            
        Returns:
            Normalized location dictionary or None
        """
        try:
            # Extract name
            name = item.get("name", "")
            
            # Extract address
            address_data = item.get("address", {})
            if isinstance(address_data, str):
                address = address_data
            elif isinstance(address_data, dict):
                address_parts = [
                    address_data.get("streetAddress", ""),
                    address_data.get("postalCode", ""),
                    address_data.get("addressLocality", ""),
                    address_data.get("addressRegion", ""),
                    address_data.get("addressCountry", ""),
                ]
                address = ", ".join(part for part in address_parts if part)
            else:
                address = ""
            
            # Extract phone
            phone = item.get("telephone", "") or item.get("phone", "")
            
            # Extract geo coordinates if available
            geo = item.get("geo", {})
            latitude = geo.get("latitude") if isinstance(geo, dict) else None
            longitude = geo.get("longitude") if isinstance(geo, dict) else None
            
            # Extract opening hours
            opening_hours = item.get("openingHours", [])
            if isinstance(opening_hours, str):
                opening_hours = [opening_hours]
            
            # Extract URL
            url = item.get("url", "") or item.get("@id", "")
            
            # Extract additional info
            description = item.get("description", "")
            email = item.get("email", "")
            
            return {
                "name": name,
                "address": address,
                "street_address": address_data.get("streetAddress", "") if isinstance(address_data, dict) else "",
                "postal_code": address_data.get("postalCode", "") if isinstance(address_data, dict) else "",
                "city": address_data.get("addressLocality", "") if isinstance(address_data, dict) else "",
                "region": address_data.get("addressRegion", "") if isinstance(address_data, dict) else "",
                "country": address_data.get("addressCountry", "") if isinstance(address_data, dict) else "",
                "phone": phone,
                "email": email,
                "latitude": latitude,
                "longitude": longitude,
                "opening_hours": opening_hours,
                "url": url,
                "description": description,
                "raw_data": item,
            }
        except Exception as e:
            logger.error(f"Error normalizing location: {e}")
            return None


def extract_ld_json(html: str) -> list[dict]:
    """
    Convenience function to extract locations from HTML.
    
    Args:
        html: Raw HTML content
        
    Returns:
        List of location dictionaries
    """
    return LDJsonParser.extract_locations(html)


def extract_store_locator_data(html: str) -> list[dict]:
    """
    Try to extract store data from common JavaScript patterns.
    
    Args:
        html: Raw HTML content
        
    Returns:
        List of store dictionaries
    """
    stores = []
    
    # Common patterns for embedded store data
    patterns = [
        r'var\s+stores?\s*=\s*(\[[\s\S]*?\]);',
        r'window\.stores?\s*=\s*(\[[\s\S]*?\]);',
        r'"locations?"\s*:\s*(\[[\s\S]*?\])',
        r'storeData\s*=\s*(\[[\s\S]*?\]);',
        r'markers?\s*=\s*(\[[\s\S]*?\]);',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        for match in matches:
            try:
                data = json.loads(match)
                if isinstance(data, list):
                    stores.extend(data)
            except json.JSONDecodeError:
                continue
    
    return stores
