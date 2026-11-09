"""
Data models for QuintoAndar scraper
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import re


@dataclass
class PropertyInfo:
    """Data class to hold property information"""
    
    # Basic info
    url: str
    bairro: Optional[str] = None
    zone: Optional[str] = None
    scraped_at: Optional[str] = None
    title: Optional[str] = None
    address_street: Optional[str] = None
    area: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None

    # Property details
    quartos: Optional[int] = None
    suite: int = 0
    banheiros: Optional[int] = None
    vagas: Optional[int] = None
    andar: Optional[str] = None
    
    # Features
    pet: str = "não"
    mobiliado: str = "sim"
    metro_proximo: str = "não"
    
    # Prices
    prices: Dict[str, str] = field(default_factory=dict)
    
    # Status
    status: str = "pending"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for DataFrame"""
        result = {
            "url": self.url,
            "bairro": self.bairro,
            "zone": self.zone,
            "scraped_at": self.scraped_at,
            "status": self.status,
            "title": self.title,
            "address_street": self.address_street,
            "lat": self.lat,
            "lon": self.lon,
            "area": self.area,
            "quartos": self.quartos,
            "suite": self.suite,
            "banheiros": self.banheiros,
            "vagas": self.vagas,
            "andar": self.andar,
            "pet": self.pet,
            "mobiliado": self.mobiliado,
            "metro_proximo": self.metro_proximo,
        }
        
        # Add price information
        result.update(self.prices)
        
        return result
    
    @staticmethod
    def parse_number(text: str) -> Optional[int]:
        """Extract number from text"""
        if not text:
            return None
        m = re.search(r"(\d+)", text)
        return int(m.group(1)) if m else None
    
    @staticmethod
    def clean_area(text: str) -> Optional[str]:
        """Clean area text to standard format"""
        if not text:
            return None
        # Handle both regular space and non-breaking space
        text = text.replace("\xa0", " ")
        m = re.search(r"(\d+)\s*m²", text)
        return f"{m.group(1)}" if m else None
    
    def update_from_text(self, text: str) -> None:
        """Update property info from parsed text"""
        if not text:
            return
            
        text_lower = text.lower()
        
        #  Area - handle both "m²" and "mÂ²" encoding issues
        if ("m²" in text or "mâ²" in text_lower) and not self.area:
            # Only set area if it's not already set and this looks like area info
            if "quarto" not in text_lower:  # Don't confuse with room descriptions
                self.area = self.clean_area(text)
        
        # Rooms - be more specific about room parsing
        elif "quarto" in text_lower:
            # Extract number, but be careful not to get area numbers
            room_match = re.search(r"(\d+)\s*quarto", text_lower)
            if room_match:
                q = int(room_match.group(1))
                # Sanity check: rooms should be reasonable (1-10)
                if 1 <= q <= 10:
                    self.quartos = q
            
            # Check for suite
            if "suíte" in text_lower or "suite" in text_lower:
                s_match = re.search(r"(\d+)\s*su[ií]te", text, flags=re.IGNORECASE)
                if s_match:
                    suite_count = int(s_match.group(1))
                    if 1 <= suite_count <= 5:  # Sanity check
                        self.suite = suite_count
                else:
                    # If "suite" is mentioned but no number, assume 1
                    self.suite = 1
        
        # Bathrooms
        elif "banheiro" in text_lower:
            bath_match = re.search(r"(\d+)\s*banheiro", text_lower)
            if bath_match:
                b = int(bath_match.group(1))
                if 1 <= b <= 10:  # Sanity check
                    self.banheiros = b
        
        # Parking spots
        elif "vaga" in text_lower:
            vaga_match = re.search(r"(\d+)\s*vaga", text_lower)
            if vaga_match:
                v = int(vaga_match.group(1))
                if 0 <= v <= 10:  # Sanity check
                    self.vagas = v
            elif "sem vaga" in text_lower:
                self.vagas = 0
            
        
        # Floor
        elif "andar" in text_lower and self.andar is None:
            self.andar = text
        
        # Pet policy (check the negative wording first: "Não aceita pet" contains "aceita pet")
        elif "aceita pet" in text_lower:
            negative = any(neg in text_lower for neg in ("não aceita", "nao aceita"))
            self.pet = "não" if negative else "sim"

        # Furniture
        elif "sem mobília" in text_lower or "sem mobiliá" in text_lower:
            self.mobiliado = "não"

        # Metro proximity ("Sem metrô próx." / "Não ... metrô" must not count as near)
        elif "metrô" in text_lower or "metro próx" in text_lower:
            negative = any(neg in text_lower for neg in ("sem metr", "não", "nao", "longe"))
            self.metro_proximo = "não" if negative else "sim"

            