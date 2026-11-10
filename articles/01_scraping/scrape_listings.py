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
import argparse                                   # read command-line options (--bairro, --n, --out)
import asyncio                                    # run many downloads concurrently
import json                                       # parse the JSON embedded in the pages
import re                                         # regular expressions to pull numbers out of text
import time                                       # sleep between clicks and measure run time
from urllib.parse import urlsplit, urlunsplit     # split a URL into parts and rebuild it

import httpx                                      # async HTTP client (supports HTTP/2)
import pandas as pd                               # tables and CSV output
from bs4 import BeautifulSoup                     # HTML parser
from selenium import webdriver                    # controls a real Chrome browser
from selenium.common.exceptions import TimeoutException          # raised when a wait runs out of time
from selenium.webdriver.chrome.options import Options            # Chrome start-up options
from selenium.webdriver.common.by import By                      # how to locate elements (CSS selector)
from selenium.webdriver.support import expected_conditions as EC  # ready-made "wait until..." conditions
from selenium.webdriver.support.ui import WebDriverWait          # waits for a condition, with a timeout

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
BASE_URL = "https://www.quintoandar.com.br"       # site root
CONCURRENCY = 3                                   # max requests in flight at the same time
REQUEST_DELAY = 1.0                               # seconds each worker pauses after a request
RETRIES = 3                                       # attempts per page before giving up
TIMEOUT = 20                                      # seconds to wait for a response

HEADERS = {                                       # HTTP headers sent with every request
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "  # identify as a
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",           # standard browser
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",  # ask for the Portuguese version of the pages
}

CARD_LINKS = "div[data-testid='house-card-container-rent'] a"    # links of the listing cards
LOAD_MORE = "button#see-more, button[data-testid='load-more-button']"  # the "Load more" button
MAIN_INFO = "div[data-testid='house-main-info'] .MainInfo_iconDescriptionWrapper__St8RA"  # facts block
PRICE_ROWS = "ul[data-testid='listing-price-table'] li"         # rows of the price table


# ----------------------------------------------------------------------------
# Stage 1 — collect listing URLs with Selenium
# ----------------------------------------------------------------------------
def clean_url(href: str) -> str:                  # remove "?..." and "#..." from a URL
    """Drop the query string: robots.txt disallows tracking parameters like ?search_id=."""
    p = urlsplit(href)                            # split into scheme, host, path, query, fragment
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))  # rebuild without query and fragment


def collect_urls(bairro: str, n: int) -> list:    # return up to n listing URLs for one bairro
    search_url = f"{BASE_URL}/alugar/imovel/{bairro}-sao-paulo-sp-brasil"  # bairro search page
    print(f"[1/3] Collecting {n} listing URLs from {search_url}")  # progress message

    options = Options()                           # Chrome settings
    options.add_argument("--headless=new")        # run Chrome without opening a window
    driver = webdriver.Chrome(options=options)    # start Chrome (Selenium Manager fetches ChromeDriver)
    wait = WebDriverWait(driver, 12)              # helper that waits up to 12 s for conditions
    urls = []                                     # list keeps the order in which listings appear

    try:                                          # make sure Chrome is closed even if something fails
        driver.get(search_url)                    # open the search page
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, CARD_LINKS)))  # wait for first cards

        while len(urls) < n:                      # keep going until we have n URLs
            for card in driver.find_elements(By.CSS_SELECTOR, CARD_LINKS):  # every card link on the page
                href = card.get_attribute("href")  # the link's target URL
                # keep absolute links we haven't seen yet, without their query string
                if href and href.startswith("https") and clean_url(href) not in urls:
                    urls.append(clean_url(href))  # store the cleaned URL
            print(f"      {len(urls)} URLs so far")  # progress message
            if len(urls) >= n:                    # enough URLs?
                break                             # stop clicking

            # Click "Load more" and wait for new cards to appear
            n_cards = len(driver.find_elements(By.CSS_SELECTOR, CARD_LINKS))  # cards before clicking
            try:                                  # the button may be missing on the last page
                button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, LOAD_MORE)))  # find button
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)  # scroll to it
                time.sleep(1)                     # short pause, like a person would
                driver.execute_script("arguments[0].click();", button)  # click it via JavaScript
                # wait until more cards are on the page than before the click
                wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, CARD_LINKS)) > n_cards)
            except TimeoutException:              # no button or no new cards within 12 s
                print("      No more listings to load.")  # tell the user
                break                             # stop collecting
    finally:                                      # always runs, error or not
        driver.quit()                             # close Chrome

    return urls[:n]                               # at most n URLs


# ----------------------------------------------------------------------------
# Stage 3 — parse one listing page
# ----------------------------------------------------------------------------
def find_coords(obj, depth=0):                    # search any JSON structure for lat/lon
    """Search nested JSON for latitude/longitude keys; accept only values inside São Paulo."""
    if depth > 25:                                # safety limit against very deep nesting
        return None                               # give up on this branch
    if isinstance(obj, dict):                     # a JSON object
        try:                                      # keys may be missing or not numbers
            lat = float(obj.get("latitude", obj.get("lat")))  # latitude under either key name
            lon = float(obj.get("longitude", obj.get("lng", obj.get("lon"))))  # longitude, 3 key names
            if -24.1 < lat < -23.3 and -47.0 < lon < -46.3:  # inside a box around São Paulo?
                return lat, lon                   # found valid coordinates
        except (TypeError, ValueError):           # missing key (None) or non-numeric value
            pass                                  # not here; keep searching
        children = obj.values()                   # search inside the object's values
    elif isinstance(obj, list):                   # a JSON array
        children = obj                            # search inside its items
    else:                                         # a plain value (text, number...)
        return None                               # nothing to search inside
    for child in children:                        # look inside each child
        found = find_coords(child, depth + 1)     # recursive call one level deeper
        if found:                                 # coordinates found somewhere below?
            return found                          # pass them up
    return None                                   # nothing found in this branch


def first_int(pattern: str, text: str):           # first number captured by a regex, or None
    m = re.search(pattern, text)                  # look for the pattern in the text
    return int(m.group(1)) if m else None         # convert the captured digits to int


def parse_listing(html: str, url: str) -> dict:   # turn one listing page into a dict of fields
    soup = BeautifulSoup(html, "lxml")            # parse the HTML
    row = {"url": url}                            # start the record with the URL

    # Title, e.g. "Apartamento para alugar com 40m², 1 quarto e sem vaga"
    h1 = soup.select_one("h1")                    # the page's main heading
    row["title"] = h1.get_text(strip=True) if h1 else None  # its text, if present

    # Short facts block: "45 m²", "2 quartos (1 suíte)", "1 vaga", "Aceita pet", "Metrô próx.", ...
    facts = [" ".join(b.stripped_strings).lower() for b in soup.select(MAIN_INFO)]  # each fact, lower case
    text = " | ".join(facts)                      # all facts in one string for regex searches
    row["area_m2"] = first_int(r"(\d+)\s*m²", text)          # number before "m²"
    row["bedrooms"] = first_int(r"(\d+)\s*quarto", text)     # number before "quarto(s)"
    row["bathrooms"] = first_int(r"(\d+)\s*banheiro", text)  # number before "banheiro(s)"
    row["parking"] = first_int(r"(\d+)\s*vaga", text) or 0   # number before "vaga(s)"; "-" means none
    row["floor"] = next((f for f in facts if "andar" in f), None)  # the fact mentioning the floor
    # Check negative wording first: "não aceita pet" also contains "aceita pet"!
    row["pet_friendly"] = "aceita pet" in text and "não aceita pet" not in text   # pets allowed?
    row["furnished"] = "mobiliado" in text and "sem mobília" not in text          # furnished?
    row["metro_near"] = "metrô próx" in text and "sem metrô" not in text          # near the metro?

    # Price table: "Aluguel R$ 2.000", "Condomínio R$ 500", ..., "Total R$ 2.700"
    for li in soup.select(PRICE_ROWS):            # each row of the price table
        parts = list(li.stripped_strings)         # the row's text pieces, e.g. ["Aluguel", "R$ 2.000"]
        if len(parts) >= 2:                       # need at least a label and a value
            label, value = parts[0], parts[-1].replace("\xa0", " ")  # first piece = label, last = value
            number = re.sub(r"[^\d]", "", value)  # keep only the digits ("R$ 2.000" -> "2000")
            # "Incluso" (included) means 0; otherwise the number, or None if there are no digits
            row[label] = 0 if value.lower().startswith("inclu") else (int(number) if number else None)

    # Coordinates: JSON-LD and the Next.js page data
    row["lat"] = row["lon"] = None                # default: no coordinates
    scripts = soup.find_all("script", {"type": "application/ld+json"}) + \
        soup.find_all("script", {"id": "__NEXT_DATA__"})     # the two kinds of embedded JSON
    for script in scripts:                        # try each embedded JSON block
        try:                                      # the block might not be valid JSON
            coords = find_coords(json.loads(script.get_text()))  # parse it and search for lat/lon
        except json.JSONDecodeError:              # not valid JSON
            continue                              # try the next block
        if coords:                                # found coordinates?
            row["lat"], row["lon"] = coords       # store them
            break                                 # no need to look further
    return row                                    # the finished record


# ----------------------------------------------------------------------------
# Stage 2 — fetch listing pages concurrently (async + polite)
# ----------------------------------------------------------------------------
async def fetch(client: httpx.AsyncClient, url: str):  # download one page, with retries
    for attempt in range(1, RETRIES + 1):         # attempts 1, 2, 3
        try:                                      # network errors raise exceptions
            r = await client.get(url)             # send the request and wait for the answer
            if r.status_code == 200:              # OK
                return r.text                     # return the HTML
            if r.status_code == 429 or r.status_code >= 500:  # "too many requests" or server error
                await asyncio.sleep(2 ** attempt)  # back off: wait 2, 4, 8 s
                continue                          # and try again
            return None                           # other 4xx (e.g. 404 removed listing): give up
        except httpx.HTTPError:                   # timeout or connection problem
            await asyncio.sleep(2 ** attempt)     # back off before retrying
    return None                                   # all attempts failed


async def fetch_and_parse_all(urls: list) -> list:  # download and parse all URLs
    print(f"[2/3] Fetching {len(urls)} listings ({CONCURRENCY} at a time, {REQUEST_DELAY}s pause)")  # progress
    semaphore = asyncio.Semaphore(CONCURRENCY)    # lets at most CONCURRENCY workers in at once

    # One shared HTTP client: HTTP/2, our headers, timeout, and follow redirects
    async with httpx.AsyncClient(http2=True, headers=HEADERS, timeout=TIMEOUT,
                                 follow_redirects=True) as client:
        async def worker(url):                    # the job for one URL
            async with semaphore:                 # wait for a free slot
                html = await fetch(client, url)   # download the page
                await asyncio.sleep(REQUEST_DELAY)  # polite pause before releasing the slot
            # parse the page, or record the failure
            return parse_listing(html, url) if html else {"url": url, "error": "fetch failed"}

        return await asyncio.gather(*(worker(u) for u in urls))  # run all jobs; results in URL order


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():                                       # entry point
    # Define the command-line options
    parser = argparse.ArgumentParser(description="Scrape a few rental listings from one São Paulo bairro.")
    parser.add_argument("--bairro", default="liberdade", help="bairro slug, e.g. pinheiros, vila-mariana")
    parser.add_argument("--n", type=int, default=20, help="number of listings (default 20)")
    parser.add_argument("--out", help="output CSV (default listings_<bairro>.csv)")
    args = parser.parse_args()                    # read the options the user typed

    start = time.time()                           # start the timer
    urls = collect_urls(args.bairro, args.n)      # stage 1: listing URLs
    if not urls:                                  # nothing found?
        print("No listings found. Check the bairro slug in your browser.")  # likely a wrong slug
        return                                    # stop here
    rows = asyncio.run(fetch_and_parse_all(urls))  # stages 2 + 3: download and parse

    df = pd.DataFrame(rows)                       # one row per listing
    out = args.out or f"listings_{args.bairro}.csv"  # output file name
    df.to_csv(out, index=False, encoding="utf-8-sig")  # save (utf-8-sig opens correctly in Excel)

    print(f"[3/3] Saved {len(df)} listings to {out} in {time.time() - start:.0f}s")  # summary
    print(f"      with coordinates: {df['lat'].notna().mean():.0%}")  # share of rows with lat/lon
    # Columns to preview (only those that exist)
    preview = [c for c in ["bedrooms", "area_m2", "parking", "Aluguel", "Total", "lat", "lon"] if c in df]
    print(df[preview].head().to_string())         # show the first 5 rows


if __name__ == "__main__":                        # only when run as a script (not imported)
    main()                                        # start the program
