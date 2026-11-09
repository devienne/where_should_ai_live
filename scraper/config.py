"""
Configuration settings for the QuintoAndar scraper.
"""
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
URLS_DIR = RAW_DATA_DIR / "urls"

# Selenium Manager downloads ChromeDriver here (inside the venv, not the user folder)
SELENIUM_CACHE_DIR = PROJECT_ROOT / ".venv" / "selenium"

# --------------------------------------------------------------------------
# Neighbourhoods: QuintoAndar slug -> zone (5 per zone)
# Slugs are used in https://www.quintoandar.com.br/alugar/imovel/<slug>-sao-paulo-sp-brasil
# --------------------------------------------------------------------------
BAIRROS = {
    # Centro (new; slugs to be confirmed in the browser)
    "republica": "Centro",
    "bela-vista": "Centro",
    "consolacao": "Centro",
    "liberdade": "Centro",
    "santa-cecilia": "Centro",
    # Oeste
    "butanta": "Oeste",
    "perdizes": "Oeste",
    "pinheiros": "Oeste",
    "vila-leopoldina": "Oeste",
    "vila-madalena": "Oeste",
    # Sul
    "brooklin": "Sul",
    "interlagos": "Sul",
    "moema": "Sul",
    "santo-amaro": "Sul",
    "vila-mariana": "Sul",
    # Norte
    "casa-verde": "Norte",
    "mandaqui": "Norte",
    "santana": "Norte",
    "tucuruvi": "Norte",
    "vila-maria": "Norte",
    # Leste
    "itaquera": "Leste",
    "mooca": "Leste",
    "penha": "Leste",
    "sao-miguel-paulista": "Leste",
    "tatuape": "Leste",
}

BASE_URL = "https://www.quintoandar.com.br"


def listing_url(slug: str) -> str:
    """Search page that lists all rentals of a neighbourhood."""
    return f"{BASE_URL}/alugar/imovel/{slug}-sao-paulo-sp-brasil"


# Maximum number of listings scraped per bairro (None = no limit)
MAX_LISTINGS_PER_BAIRRO = 100

# Skip a bairro if a CSV for it was produced less than this many days ago
MAX_AGE_DAYS = 7

# --------------------------------------------------------------------------
# Browser (URL collection)
# --------------------------------------------------------------------------
WAIT_TIME = 12
HEADLESS = True
PAGE_LOAD_TIMEOUT = 30

# --------------------------------------------------------------------------
# HTTP client (polite settings)
# --------------------------------------------------------------------------
CONCURRENCY = 3          # parallel requests (was 8)
REQUEST_DELAY = 1.0      # seconds each worker waits after every request
TIMEOUT = 20.0
RETRIES = 3
RATE_LIMIT_DELAY = 2.0   # base back-off when the server answers 429

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.7,en;q=0.6",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# --------------------------------------------------------------------------
# CSS selectors
# --------------------------------------------------------------------------
SELECTORS = {
    "listing_cards": "div[data-testid='house-card-container-rent'] a",
    "load_more_btn": "button#see-more, button[data-testid='load-more-button']",
    "title": "h1[data-testid='listing-title'], h1, h2",
    "address_container": "div[data-testid='address-container']",
    "address_paragraphs": "div[data-testid='address-container'] p",
    "main_info": "div[data-testid='house-main-info'] .MainInfo_iconDescriptionWrapper__St8RA",
    "price_table": "ul[data-testid='listing-price-table'] li",
    "price_labels": "span, h4",
    "price_values": "div > p, div > h4",
}

LOG_LEVEL = "INFO"
