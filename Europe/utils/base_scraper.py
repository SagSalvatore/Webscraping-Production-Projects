"""
Base scraper class for bakery outlet data extraction.
Provides common functionality that can be extended for specific operators.
"""
import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from loguru import logger

import sys
sys.path.insert(0, str(__file__).rsplit("\\", 2)[0])
from utils.exporters import export_outlets
from utils.http_client import AsyncHttpClient
from utils.parsers import LDJsonParser, extract_store_locator_data


class BaseScraper(ABC):
    """
    Abstract base class for bakery outlet scrapers.
    
    Subclasses must implement:
        - scrape() method
        - OPERATOR_NAME class attribute
        - COUNTRY class attribute
    """
    
    OPERATOR_NAME: str = "Unknown"
    COUNTRY: str = "Unknown"
    BASE_URL: str = ""
    
    def __init__(
        self,
        output_dir: Optional[Path] = None,
        proxy: Optional[str] = None,
    ):
        """
        Initialize the scraper.
        
        Args:
            output_dir: Output directory for results
            proxy: Proxy URL (optional)
        """
        self.proxy = proxy
        
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            # Default to country's output directory
            project_root = Path(__file__).parent.parent
            self.output_dir = project_root / self.COUNTRY / "output"
        
        self.outlets: list[dict] = []
        self._client: Optional[AsyncHttpClient] = None
    
    @property
    def client(self) -> AsyncHttpClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = AsyncHttpClient(
                base_url=self.BASE_URL,
                proxy=self.proxy,
            )
        return self._client
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.client.__aenter__()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._client:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)
    
    @abstractmethod
    async def scrape(self) -> list[dict]:
        """
        Main scraping method - must be implemented by subclasses.
        
        Returns:
            List of outlet dictionaries
        """
        pass
    
    async def run(self) -> tuple[Path, Path]:
        """
        Run the scraper and export results.
        
        Returns:
            Tuple of (json_path, excel_path)
        """
        logger.info(f"Starting scraper: {self.OPERATOR_NAME} ({self.COUNTRY})")
        
        async with self:
            self.outlets = await self.scrape()
        
        if not self.outlets:
            logger.warning(f"No outlets found for {self.OPERATOR_NAME}")
            return None, None
        
        logger.success(f"Found {len(self.outlets)} outlets for {self.OPERATOR_NAME}")
        
        # Export results
        json_path, excel_path = export_outlets(
            outlets=self.outlets,
            operator_name=self.OPERATOR_NAME,
            country=self.COUNTRY,
            output_dir=self.output_dir,
        )
        
        return json_path, excel_path
    
    async def fetch_page(self, url: str, **kwargs) -> str:
        """
        Fetch a page and return HTML content.
        
        Args:
            url: URL to fetch
            **kwargs: Additional arguments for HTTP client
            
        Returns:
            HTML content
        """
        response = await self.client.get(url, **kwargs)
        return response.text
    
    async def fetch_json(self, url: str, **kwargs) -> Any:
        """
        Fetch JSON from a URL (for hidden APIs).
        
        Args:
            url: URL to fetch
            **kwargs: Additional arguments
            
        Returns:
            Parsed JSON
        """
        return await self.client.get_json(url, **kwargs)
    
    def parse_ld_json(self, html: str) -> list[dict]:
        """
        Parse LD+JSON from HTML content.
        
        Args:
            html: HTML content
            
        Returns:
            List of location dictionaries
        """
        return LDJsonParser.extract_locations(html)
    
    def parse_store_locator(self, html: str) -> list[dict]:
        """
        Try to extract store data from JavaScript in HTML.
        
        Args:
            html: HTML content
            
        Returns:
            List of store dictionaries
        """
        return extract_store_locator_data(html)
    
    def normalize_outlet(
        self,
        raw_data: dict,
        name_field: str = "name",
        address_field: str = "address",
        phone_field: str = "phone",
        **extra_mappings,
    ) -> dict:
        """
        Normalize raw outlet data to standard format.
        
        Args:
            raw_data: Raw outlet data
            name_field: Field name for outlet name
            address_field: Field name for address
            phone_field: Field name for phone
            **extra_mappings: Additional field mappings
            
        Returns:
            Normalized outlet dictionary
        """
        outlet = {
            "name": raw_data.get(name_field, ""),
            "address": "",
            "street_address": "",
            "postal_code": "",
            "city": "",
            "region": "",
            "country": self.COUNTRY,
            "phone": raw_data.get(phone_field, ""),
            "email": raw_data.get("email", ""),
            "latitude": None,
            "longitude": None,
            "opening_hours": [],
            "url": "",
            "operator": self.OPERATOR_NAME,
        }
        
        # Handle address - can be a string or a dict
        address = raw_data.get(address_field, "")
        if isinstance(address, str):
            outlet["address"] = address
        elif isinstance(address, dict):
            outlet["street_address"] = address.get("street", "") or address.get("streetAddress", "")
            outlet["postal_code"] = address.get("zip", "") or address.get("postalCode", "")
            outlet["city"] = address.get("city", "") or address.get("addressLocality", "")
            outlet["region"] = address.get("region", "") or address.get("addressRegion", "")
            
            # Build full address
            parts = [
                outlet["street_address"],
                outlet["postal_code"],
                outlet["city"],
            ]
            outlet["address"] = ", ".join(p for p in parts if p)
        
        # Handle coordinates
        if "lat" in raw_data and "lng" in raw_data:
            outlet["latitude"] = raw_data.get("lat")
            outlet["longitude"] = raw_data.get("lng")
        elif "latitude" in raw_data and "longitude" in raw_data:
            outlet["latitude"] = raw_data.get("latitude")
            outlet["longitude"] = raw_data.get("longitude")
        elif "geo" in raw_data:
            geo = raw_data["geo"]
            if isinstance(geo, dict):
                outlet["latitude"] = geo.get("latitude")
                outlet["longitude"] = geo.get("longitude")
        
        # Apply extra mappings
        for target_field, source_field in extra_mappings.items():
            if source_field in raw_data:
                outlet[target_field] = raw_data[source_field]
        
        return outlet


def run_scraper(scraper_class: type[BaseScraper], **kwargs) -> tuple[Path, Path]:
    """
    Run a scraper synchronously.
    
    Args:
        scraper_class: Scraper class to instantiate and run
        **kwargs: Arguments for scraper constructor
        
    Returns:
        Tuple of (json_path, excel_path)
    """
    scraper = scraper_class(**kwargs)
    return asyncio.run(scraper.run())
