"""
Where should AI live? — Part 1 companion script
================================================

A minimal, single-file version of the scraper described in the article:

  1. Selenium opens the neighbourhood search page and clicks "Load more" until it has N listing URLs
  2. httpx fetches the listing pages concurrently (async, HTTP/2), politely
  3. Each page is parsed: price table, area/rooms/parking..., and lat/lon from the embedded JSON
  4. Results are saved to a CSV

Requirements (Python 3.10+, Google Chrome installed):
    pip install selenium "httpx[http2]" beautifulsoup4 lxml pandas

Usage:
    python scrape_listings.py                          # 20 listings from Liberdade
    python scrape_listings.py --bairro pinheiros --n 50
    python scrape_listings.py --bairro vila-mariana --n 30 --out vila_mariana.csv

The bairro is the slug used in the site's URL:
    https://www.quintoandar.com.br/alugar/imovel/<bairro>-sao-paulo-sp-brasil

Please be considerate: keep N small, keep the delays, and read the target site's
robots.txt and terms of use before running any scraper. Educational use only.
"""
import argparse
import asyncio
import json
import re
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
BASE_URL = "https://www.quintoandar.com.br"
CONCURRENCY = 3        # requests in flight at the same time
REQUEST_DELAY = 1.0    # seconds each worker waits after a request
RETRIES = 3
TIMEOUT = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

CARD_LINKS = "div[data-testid='house-card-container-rent'] a"
LOAD_MORE = "button#see-more, button[data-testid='load-more-button']"
MAIN_INFO = "div[data-testid='house-main-info'] .MainInfo_iconDescriptionWrapper__St8RA"
PRICE_ROWS = "ul[data-testid='listing-price-table'] li"


# ----------------------------------------------------------------------------
# Stage 1 — collect listing URLs with Selenium
# ----------------------------------------------------------------------------
def clean_url(href: str) -> str:
    """Drop the query string: robots.txt disallows tracking parameters like ?search_id=."""
    p = urlsplit(href)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def collect_urls(bairro: str, n: int) -> list:
    search_url = f"{BASE_URL}/alugar/imovel/{bairro}-sao-paulo-sp-brasil"
    print(f"[1/3] Collecting {n} listing URLs from {search_url}")

    options = Options()
    options.add_argument("--headless=new")
    driver = webdriver.Chrome(options=options)   # Selenium Manager finds/downloads ChromeDriver
    wait = WebDriverWait(driver, 12)
    urls = []  # keeps the order in which listings appear

    try:
        driver.get(search_url)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, CARD_LINKS)))

        while len(urls) < n:
            for card in driver.find_elements(By.CSS_SELECTOR, CARD_LINKS):
                href = card.get_attribute("href")
                if href and href.startswith("https") and clean_url(href) not in urls:
                    urls.append(clean_url(href))
            print(f"      {len(urls)} URLs so far")
            if len(urls) >= n:
                break

            # Click "Load more" and wait for new cards to appear
            n_cards = len(driver.find_elements(By.CSS_SELECTOR, CARD_LINKS))
            try:
                button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, LOAD_MORE)))
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", button)
                wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, CARD_LINKS)) > n_cards)
            except TimeoutException:
                print("      No more listings to load.")
                break
    finally:
        driver.quit()

    return urls[:n]


# ----------------------------------------------------------------------------
# Stage 3 — parse one listing page
# ----------------------------------------------------------------------------
def find_coords(obj, depth=0):
    """Search nested JSON for latitude/longitude keys; accept only values inside São Paulo."""
    if depth > 25:
        return None
    if isinstance(obj, dict):
        try:
            lat = float(obj.get("latitude", obj.get("lat")))
            lon = float(obj.get("longitude", obj.get("lng", obj.get("lon"))))
            if -24.1 < lat < -23.3 and -47.0 < lon < -46.3:
                return lat, lon
        except (TypeError, ValueError):
            pass
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = find_coords(child, depth + 1)
        if found:
            return found
    return None


def first_int(pattern: str, text: str):
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def parse_listing(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    row = {"url": url}

    # Title, e.g. "Apartamento para alugar com 40m², 1 quarto e sem vaga"
    h1 = soup.select_one("h1")
    row["title"] = h1.get_text(strip=True) if h1 else None

    # Short facts block: "45 m²", "2 quartos (1 suíte)", "1 vaga", "Aceita pet", "Metrô próx.", ...
    facts = [" ".join(b.stripped_strings).lower() for b in soup.select(MAIN_INFO)]
    text = " | ".join(facts)
    row["area_m2"] = first_int(r"(\d+)\s*m²", text)
    row["bedrooms"] = first_int(r"(\d+)\s*quarto", text)
    row["bathrooms"] = first_int(r"(\d+)\s*banheiro", text)
    row["parking"] = first_int(r"(\d+)\s*vaga", text) or 0          # "-" means no parking
    row["floor"] = next((f for f in facts if "andar" in f), None)
    # Check negative wording first: "não aceita pet" also contains "aceita pet"!
    row["pet_friendly"] = "aceita pet" in text and "não aceita pet" not in text
    row["furnished"] = "mobiliado" in text and "sem mobília" not in text
    row["metro_near"] = "metrô próx" in text and "sem metrô" not in text

    # Price table: "Aluguel R$ 2.000", "Condomínio R$ 500", ..., "Total R$ 2.700"
    for li in soup.select(PRICE_ROWS):
        parts = list(li.stripped_strings)
        if len(parts) >= 2:
            label, value = parts[0], parts[-1].replace("\xa0", " ")
            number = re.sub(r"[^\d]", "", value)
            row[label] = 0 if value.lower().startswith("inclu") else (int(number) if number else None)

    # Coordinates: JSON-LD and the Next.js page data
    row["lat"] = row["lon"] = None
    scripts = soup.find_all("script", {"type": "application/ld+json"}) + \
        soup.find_all("script", {"id": "__NEXT_DATA__"})
    for script in scripts:
        try:
            coords = find_coords(json.loads(script.get_text()))
        except json.JSONDecodeError:
            continue
        if coords:
            row["lat"], row["lon"] = coords
            break
    return row


# ----------------------------------------------------------------------------
# Stage 2 — fetch listing pages concurrently (async + polite)
# ----------------------------------------------------------------------------
async def fetch(client: httpx.AsyncClient, url: str):
    for attempt in range(1, RETRIES + 1):
        try:
            r = await client.get(url)
            if r.status_code == 200:
                return r.text
            if r.status_code == 429 or r.status_code >= 500:   # busy: back off and retry
                await asyncio.sleep(2 ** attempt)
                continue
            return None                                        # other 4xx: give up
        except httpx.HTTPError:
            await asyncio.sleep(2 ** attempt)
    return None


async def fetch_and_parse_all(urls: list) -> list:
    print(f"[2/3] Fetching {len(urls)} listings ({CONCURRENCY} at a time, {REQUEST_DELAY}s pause)")
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(http2=True, headers=HEADERS, timeout=TIMEOUT,
                                 follow_redirects=True) as client:
        async def worker(url):
            async with semaphore:
                html = await fetch(client, url)
                await asyncio.sleep(REQUEST_DELAY)
            return parse_listing(html, url) if html else {"url": url, "error": "fetch failed"}

        return await asyncio.gather(*(worker(u) for u in urls))


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Scrape a few rental listings from one São Paulo bairro.")
    parser.add_argument("--bairro", default="liberdade", help="bairro slug, e.g. pinheiros, vila-mariana")
    parser.add_argument("--n", type=int, default=20, help="number of listings (default 20)")
    parser.add_argument("--out", help="output CSV (default listings_<bairro>.csv)")
    args = parser.parse_args()

    start = time.time()
    urls = collect_urls(args.bairro, args.n)
    if not urls:
        print("No listings found. Check the bairro slug in your browser.")
        return
    rows = asyncio.run(fetch_and_parse_all(urls))

    df = pd.DataFrame(rows)
    out = args.out or f"listings_{args.bairro}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")

    print(f"[3/3] Saved {len(df)} listings to {out} in {time.time() - start:.0f}s")
    print(f"      with coordinates: {df['lat'].notna().mean():.0%}")
    preview = [c for c in ["bedrooms", "area_m2", "parking", "Aluguel", "Total", "lat", "lon"] if c in df]
    print(df[preview].head().to_string())


if __name__ == "__main__":
    main()
