"""
Data parser for extracting property information from HTML
"""

import json
import re
import logging
from typing import Dict, Any, Optional
from bs4 import BeautifulSoup

from .models import PropertyInfo
from .config import SELECTORS


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _valid_sp_coords(lat: Optional[float], lon: Optional[float]) -> bool:
    """Rough bounding box of the São Paulo metro area (filters out zeros/garbage)."""
    return lat is not None and lon is not None and -24.1 < lat < -23.3 and -47.0 < lon < -46.3


def _find_coords(obj: Any, depth: int = 0) -> Optional[tuple]:
    """Recursively look for a dict holding latitude/longitude (or lat/lng/lon) keys."""
    if depth > 25:
        return None
    if isinstance(obj, dict):
        lat = obj.get("latitude", obj.get("lat"))
        lon = obj.get("longitude", obj.get("lng", obj.get("lon")))
        lat, lon = _to_float(lat), _to_float(lon)
        if _valid_sp_coords(lat, lon):
            return lat, lon
        for value in obj.values():
            found = _find_coords(value, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_coords(item, depth + 1)
            if found:
                return found
    return None


class PropertyParser:
    """Parses HTML content to extract property information"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def parse_property(self, html: str, url: str) -> PropertyInfo:
        """Main method to parse property from HTML"""
        if not html:
            return PropertyInfo(url=url, status="no_content")
        
        soup = BeautifulSoup(html, "lxml")
        property_info = PropertyInfo(url=url)
        
        try:
            # Extract from JSON-LD first (most reliable)
            self._extract_from_json_ld(soup, property_info)
            
            # Extract from DOM elements
            self._extract_title(soup, property_info)
            self._extract_address(soup, property_info)
            self._extract_property_details(soup, property_info)
            self._extract_prices(soup, property_info)
            self._extract_coordinates(soup, property_info)

            property_info.status = "success"
            
        except Exception as e:
            self.logger.error(f"Error parsing property {url}: {e}")
            property_info.status = "parse_error"
        
        return property_info
    
    def _extract_from_json_ld(self, soup: BeautifulSoup, property_info: PropertyInfo) -> None:
        """Extract data from JSON-LD structured data"""
        for script_tag in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script_tag.get_text(strip=True))
                
                # Handle list of JSON-LD objects
                if isinstance(data, list):
                    for item in data:
                        if self._is_property_data(item):
                            data = item
                            break
                    else:
                        data = data[0] if data else {}
                
                if isinstance(data, dict):
                    self._parse_json_ld_data(data, property_info)
                    
            except (json.JSONDecodeError, KeyError) as e:
                self.logger.debug(f"Could not parse JSON-LD: {e}")
                continue
    
    def _is_property_data(self, item: Dict) -> bool:
        """Check if JSON-LD item contains property data"""
        if not isinstance(item, dict):
            return False
        
        type_val = item.get("@type", "")
        return any(keyword in str(type_val).lower() for keyword in 
                  ["place", "residence", "apartment", "house", "realestate"])
    
    def _parse_json_ld_data(self, data: Dict, property_info: PropertyInfo) -> None:
        """Parse JSON-LD data into property info"""
        # Address from JSON-LD
        address = data.get("address")
        if isinstance(address, dict):
            address_parts = [
                address.get("streetAddress"),
                address.get("addressLocality"), 
                address.get("addressRegion")
            ]
            address_parts = [part for part in address_parts if part]
            if address_parts:
                property_info.address_street = ", ".join(address_parts)
        
        # Price from offers
        offers = data.get("offers")
        if offers:
            if isinstance(offers, list) and offers:
                offer = offers[0]
            elif isinstance(offers, dict):
                offer = offers
            else:
                offer = {}
            
            if isinstance(offer, dict):
                price = offer.get("price")
                currency = offer.get("priceCurrency", "")
                if price:
                    property_info.prices["Preço (LD)"] = f"{currency} {price}".strip()
    
    def _extract_title(self, soup: BeautifulSoup, property_info: PropertyInfo) -> None:
        """Extract property title"""
        title_element = soup.select_one(SELECTORS["title"])
        if title_element:
            property_info.title = title_element.get_text(strip=True)
    
    def _extract_address(self, soup: BeautifulSoup, property_info: PropertyInfo) -> None:
        """Extract address with multiple strategies"""
        if property_info.address_street:  # Already got from JSON-LD
            return
        
        # Strategy 1: Address container with paragraphs
        address_paragraphs = soup.select(SELECTORS["address_paragraphs"])
        if address_paragraphs:
            address_parts = []
            for p in address_paragraphs:
                text = p.get_text(strip=True)
                if text and text not in address_parts:
                    address_parts.append(text)
            if address_parts:
                property_info.address_street = ", ".join(address_parts)
                return
        
        # Strategy 2: Single address container
        address_container = soup.select_one(SELECTORS["address_container"])
        if address_container:
            # Get all text but clean it up
            address_text = address_container.get_text(strip=True)
            # Remove multiple spaces and normalize
            address_text = re.sub(r'\s+', ' ', address_text)
            if address_text:
                property_info.address_street = address_text
                return
        
        # Strategy 3: Look for common address patterns
        for selector in [
            '[data-testid*="address"]',
            '[class*="address" i]',
            '[class*="location" i]',
            '.address',
            '.location'
        ]:
            element = soup.select_one(selector)
            if element:
                text = element.get_text(strip=True)
                if text and len(text) > 10:  # Reasonable address length
                    property_info.address_street = text
                    return
        
        # Strategy 4: Extract from breadcrumb or nearby elements
        breadcrumbs = soup.select('[class*="breadcrumb" i] a, nav a')
        for breadcrumb in breadcrumbs[-2:]:  # Last 2 breadcrumbs often contain location
            text = breadcrumb.get_text(strip=True)
            if any(keyword in text.lower() for keyword in ["sp", "paulo", "são", "rua", "av"]):
                property_info.address_street = text
                return
        
        self.logger.debug(f"Could not extract address for {property_info.url}")
    
    def _extract_property_details(self, soup: BeautifulSoup, property_info: PropertyInfo) -> None:
        """Extract property details from main info blocks"""
        info_blocks = soup.select(SELECTORS["main_info"])
        
        for block in info_blocks:
            text_parts = [t.strip() for t in block.stripped_strings]
            if not text_parts:
                continue
            
            text = " ".join(text_parts)
            property_info.update_from_text(text)
        
        # Also try to extract from title if we missed anything
        if property_info.title:
            property_info.update_from_text(property_info.title)
    
    def _extract_prices(self, soup: BeautifulSoup, property_info: PropertyInfo) -> None:
        """Extract price information from price table"""
        price_items = soup.select(SELECTORS["price_table"])
        
        for item in price_items:
            # Find label
            label_element = item.select_one(SELECTORS["price_labels"])
            if not label_element:
                continue
            
            label = label_element.get_text(strip=True)
            if not label:
                continue
            
            # Find value
            value_elements = item.select(SELECTORS["price_values"])
            if not value_elements:
                continue
            
            # Take the last value element (often the most specific)
            value = value_elements[-1].get_text(strip=True)
            
            if label and value:
                property_info.prices[label] = value


    def _extract_coordinates(self, soup: BeautifulSoup, property_info: PropertyInfo) -> None:
        """Look for lat/lon in JSON-LD ("geo") and in the Next.js page data (__NEXT_DATA__).

        If nothing is found, lat/lon stay empty and the listing is geocoded later (Day 4).
        """
        candidates = []
        for script_tag in soup.find_all("script", {"type": "application/ld+json"}):
            candidates.append(script_tag.get_text(strip=True))
        next_data = soup.find("script", {"id": "__NEXT_DATA__"})
        if next_data:
            candidates.append(next_data.get_text(strip=True))

        for raw in candidates:
            try:
                coords = _find_coords(json.loads(raw))
            except json.JSONDecodeError:
                continue
            if coords:
                property_info.lat, property_info.lon = coords
                return

        self.logger.debug(f"No coordinates found for {property_info.url}")


def parse_property_html(html: str, url: str) -> PropertyInfo:
    """Convenience function to parse property HTML"""
    parser = PropertyParser()
    return parser.parse_property(html, url)