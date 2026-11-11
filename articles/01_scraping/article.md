# Where should AI live? — Part 1: Scraping a JavaScript website with Selenium and requests

*This is the first article in a series about building a São Paulo home finder. Each article covers one stage: data collection, database, mobility data, machine learning, the app, and an LLM chatbot.*

---

## The question: where can I live, given how long I'm willing to commute?

Anyone who has rented a flat in a big city knows the trade-off: cheaper homes tend to be farther away, and the commute eats the savings, in time if not in money. So the question people actually ask is not *"what's available in Pinheiros?"* but:

> **"I work here, and I'm willing to spend up to 40 minutes getting there. Where can I live?"**

Answering it needs two things: travel times across the city, which later articles cover, and **a list of homes for rent, with their prices and locations**.

Rental platforms such as **QuintoAndar**, one of Brazil's largest, have exactly that list. But they're built for browsing one neighbourhood at a time, and none of them can filter by commute time. Opening hundreds of listings by hand and copying their details would take days.

So in this article I build a **web scraper**: a program that visits the listing pages and collects their details into a table. It is deliberately small and polite, and the [Ethics](#ethics) section at the end explains the rules it follows. The full code is a single file, [`scrape_listings.py`](scrape_listings.py), and this article walks through it function by function.

---

## First, understand the website

Before writing any scraping code, it pays to understand how the pages are built, because that decides which tools you need. The scraper deals with two kinds of page:

- **The search page** of a neighbourhood shows listing cards, with a **"Load more"** button at the bottom.
- **A listing page** shows one property: price table, area, bedrooms, parking and a map.

Web pages come in two broad kinds:

| | **Static HTML** | **JavaScript-rendered (dynamic)** |
|---|---|---|
| What the server sends | the finished page, data included | code that fetches the data and builds the page inside your browser |
| What a plain HTTP request gets | everything you see | only what was there *before* the JavaScript ran |
| Tool | `requests` + BeautifulSoup: fast and light | a real browser driven by code, such as **Selenium**: slow and heavy |

Many modern sites are a **hybrid**: the server sends a first version of the page, and JavaScript takes over for anything interactive, such as filters, infinite scroll or *"Load more"*. Three quick checks tell you which parts are which:

1. **"View page source" versus "Inspect".** *View page source* shows the HTML the server sent. *Inspect* (DevTools) shows the page as it is now, after JavaScript has changed it. Data that appears only in *Inspect* was added by JavaScript.
2. **Turn JavaScript off.** In Chrome DevTools, press `Ctrl+Shift+P`, type *"Disable JavaScript"* and reload. Whatever still works came from the server.
3. **Ask Python.** Download the page without a browser and count what you get:

```python
html = requests.get(search_url, headers=HEADERS, timeout=20).text
soup = BeautifulSoup(html, "lxml")
cards = soup.select("div[data-testid='house-card-container-rent'] a")
print(len({a["href"] for a in cards}))          # listing links in the raw HTML
```

For QuintoAndar, this showed:

- **The search page is a hybrid.** The raw HTML already contains the **first 24 listings**, together with the "Load more" button. Every batch after that is fetched by JavaScript when the button is clicked, and `requests` can download the button but not click it.
- **Listing pages are static.** The raw HTML contains every fact, every price, and the page's embedded data.

That gives the design: **use a browser only where JavaScript is unavoidable.**

```
 Search page (per neighbourhood)             Listing pages (one per property)
 ┌────────────────────────────────┐          ┌──────────────────────────────────┐
 │ first 24 cards in the HTML,    │  URLs    │ everything in the HTML           │
 │ more only via "Load more" (JS) │ ───────► │ (facts, prices, embedded JSON)   │
 │ → Selenium (headless Chrome)   │          │ → requests + BeautifulSoup       │
 └────────────────────────────────┘          └──────────────────────────────────┘
                                                            │
                                                            ▼
                                                  one CSV row per listing
```

---

## The script, function by function

### Settings and selectors

```python
BASE_URL = "https://www.quintoandar.com.br"
REQUEST_DELAY = 1.0      # seconds to pause after each listing page
TIMEOUT = 20             # seconds to wait for a server response

CARD_LINKS = "div[data-testid='house-card-container-rent'] a"
LOAD_MORE  = "button#see-more, button[data-testid='load-more-button']"
MAIN_INFO  = "div[data-testid='house-main-info'] .MainInfo_iconDescriptionWrapper__St8RA"
PRICE_ROWS = "ul[data-testid='listing-price-table'] li"
```

The **CSS selectors** say where things are on the page. I found them in DevTools by right-clicking an element, choosing *Inspect*, and looking for an attribute that identifies it.

Not all selectors are equally robust. **`data-testid` attributes** are added by the site's developers for their own automated tests, so they tend to survive redesigns. **Generated class names** like `MainInfo_iconDescriptionWrapper__St8RA` end in a hash that can change with every release of the site. Keeping all selectors at the top of the file means there's one place to fix when that happens.

### `clean_url(href)`: respecting robots.txt

```python
def clean_url(href: str) -> str:
    p = urlsplit(href)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))
```

Links on the search page carry tracking parameters, such as `.../imovel/893860514/alugar?search_id=...&rank=3`. The site's `robots.txt`, where a site states what bots may and may not fetch, **disallows URLs with those parameters**. `clean_url` rebuilds the link without the query string (`?…`) and fragment (`#…`), which gives the plain listing URL that robots.txt allows. It also means two links to the same listing become one URL, so nothing is downloaded twice.

### `collect_urls(bairro, n)`: driving a browser with Selenium

This is the JavaScript part, and it works in four steps.

**1. Start a headless Chrome.**

```python
options = Options()
options.add_argument("--headless=new")
driver = webdriver.Chrome(options=options)
wait = WebDriverWait(driver, 12)
```

*Headless* means Chrome runs normally, just without a window. Since version 4.6, Selenium downloads the matching ChromeDriver by itself, so an installed Chrome is the only requirement. `WebDriverWait` waits up to 12 seconds for a condition to become true.

**2. Wait for the page to be ready, instead of sleeping.**

```python
driver.get(search_url)
wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, CARD_LINKS)))
```

`driver.get` returns as soon as the page *starts* loading, while JavaScript may still be working. The classic mistake is to add `time.sleep(5)`: too long on a fast connection, too short on a slow one. An **explicit wait** returns the moment the first listing card exists.

**3. Collect the links, then click "Load more" and wait for new cards.**

```python
for card in driver.find_elements(By.CSS_SELECTOR, CARD_LINKS):
    href = card.get_attribute("href")
    if href and clean_url(href) not in urls:
        urls.append(clean_url(href))

n_cards = len(driver.find_elements(By.CSS_SELECTOR, CARD_LINKS))
button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, LOAD_MORE)))
driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
driver.execute_script("arguments[0].click();", button)
wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, CARD_LINKS)) > n_cards)
```

Each round reads *all* cards on the page, and the `not in urls` check skips the ones already collected. A list rather than a set keeps the site's order. Then:

- **The click goes through JavaScript.** A normal `button.click()` imitates a mouse and fails if anything covers the button, such as a cookie banner or a floating header. Calling the button's own `click()` avoids that.
- **The script waits for the effect, not for time.** It counts the cards before clicking and waits until there are more. There's no ready-made condition for "new content arrived", but `wait.until` accepts any function, and this `lambda` is exactly that check.
- **The end of the list** shows up the same way. When the button is gone, or no new cards appear within 12 seconds, the wait raises `TimeoutException`, and the loop stops.

**4. Always close the browser.**

```python
try:
    ...
finally:
    driver.quit()
```

Each Chrome uses hundreds of MB of memory. `try/finally` closes it even if something fails halfway through, instead of leaving an invisible browser running after every crash.

### `fetch_page(session, url)`: plain HTTP for the static pages

```python
def fetch_page(session, url):
    try:
        response = session.get(url, timeout=TIMEOUT)
    except requests.RequestException as error:
        print(f"      failed: {url} ({error.__class__.__name__})")
        return None
    if response.status_code != 200:
        print(f"      HTTP {response.status_code}: {url}")
        return None
    return response.text
```

Listing pages don't need a browser, so this stage is ordinary HTTP:
- **The session.** A `requests.Session` reuses one connection and sends the same headers each time: a normal browser `User-Agent` and `Accept-Language: pt-BR`, so the server returns the page a visitor would see.
- **The timeout** stops a silent server from making the script wait forever.
- **Error codes.** A `404` usually means the listing was rented and removed after its URL was collected. A `429 Too Many Requests` means slow down. Either way, the function reports it and returns `None`, and the scraper moves on.

### `parse_listing(html, url)`: from HTML to a row of data

BeautifulSoup turns about 300 KB of HTML into a dozen fields. Each field is read from wherever it's easiest to get.

**The facts block** is a row of short texts such as *"45 m²"*, *"2 quartos (1 suíte)"*, *"1 vaga"* and *"Aceita pet"*:

```python
facts = [" ".join(b.stripped_strings).lower() for b in soup.select(MAIN_INFO)]
text = " | ".join(facts)
row["area_m2"]  = first_int(r"(\d+)\s*m²", text)
row["bedrooms"] = first_int(r"(\d+)\s*quarto", text)
row["parking"]  = first_int(r"(\d+)\s*vaga", text) or 0          # "-" means no parking
row["pet_friendly"] = "aceita pet" in text and "não aceita pet" not in text
```

`soup.select` returns every element that matches a selector, and `stripped_strings` gives each element's text without extra whitespace. All the facts are joined into one lower-case string. A small helper then pulls out the numbers:

```python
def first_int(pattern, text):
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None
```

`r"(\d+)\s*quarto"` means *one or more digits, optional spaces, then "quarto"*. The parentheses capture the digits, and the same pattern matches "1 quarto" and "3 quartos". If there's no match, the result is `None` rather than an error.

**The price table** has rows like *"Aluguel — R$ 2.000"* or *"Condomínio — Incluso"*:

```python
for li in soup.select(PRICE_ROWS):
    parts = list(li.stripped_strings)               # ["Aluguel", "R$ 2.000"]
    label, value = parts[0], parts[-1]
    number = re.sub(r"[^\d]", "", value)            # "R$ 2.000" -> "2000"
    row[label] = 0 if value.lower().startswith("inclu") else (int(number) if number else None)
```

In Brazil the dot is the thousands separator (*R$ 2.000* is two thousand reais), so the safest approach is to keep only the digits. *"Incluso"*, meaning already included in the rent, becomes 0. Using each row's own label as the column name means a new fee added by the site later becomes a new column automatically.

**Data that isn't printed on the page.** The location appears only as a Google Map, not as text. The HTML does, however, carry two blocks of **structured data**: JSON inside `<script>` tags.

- **JSON-LD** (`<script type="application/ld+json">`) describes the page for search engines using the schema.org vocabulary: property type, address, price, and `geo` coordinates.
- **`__NEXT_DATA__`** is the data the Next.js framework used to render the page, about 35 KB of JSON per listing.

```python
scripts = soup.find_all("script", {"type": "application/ld+json"}) + \
          soup.find_all("script", {"id": "__NEXT_DATA__"})
for script in scripts:
    coords = find_coords(json.loads(script.get_text()))
    if coords:
        row["lat"], row["lon"] = coords
        break
```

Embedded JSON is often the most reliable part of a page to scrape. It's structured and typed, and it doesn't change when the visual design does.

### `find_coords(obj)`: searching JSON you don't control

```python
def find_coords(obj):
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
        found = find_coords(child)
        if found:
            return found
    return None
```

Hard-coding a path like `data["props"]["pageProps"]["house"]["address"]["lat"]` would break the next time the site's developers reorganise their data. Instead, `find_coords` is **recursive**: it checks the current object for latitude/longitude keys, and if they aren't there, it calls itself on everything inside.

`float(None)` and `float("abc")` raise errors, which here simply mean "not here, keep looking". The **bounding box** around São Paulo matters too. Without it, a placeholder such as `{"lat": 0, "lng": 0}` elsewhere in the page would put the apartment in the Atlantic Ocean.

### `main()`: putting it together

```python
urls = collect_urls(args.bairro, args.n)                  # stage 1: Selenium

with requests.Session() as session:
    session.headers.update(HEADERS)
    for url in urls:
        html = fetch_page(session, url)                   # stage 2: requests
        if html:
            rows.append(parse_listing(html, url))         # stage 3: BeautifulSoup
        time.sleep(REQUEST_DELAY)                         # be polite

pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
```

`argparse` reads three command-line options: `--bairro`, the neighbourhood as written in the site's URLs; `--n`, the number of listings; and `--out`, the file name. The one-second pause between pages keeps the load on the site low. Saving with `utf-8-sig` makes Excel display *"Condomínio"* and *"São Paulo"* correctly.

---

## Going further: async downloads and backoff

Downloading one page at a time is right for a few dozen listings. For the full project, about 2,500 listings across 25 neighbourhoods, the project's scraper keeps **three requests in flight at once** with `httpx` and `asyncio`:

```python
semaphore = asyncio.Semaphore(3)                 # at most 3 requests at the same time

async def worker(url):
    async with semaphore:                        # wait for a free slot
        html = await fetch(client, url)
        await asyncio.sleep(REQUEST_DELAY)       # still pause after every request
    return parse_listing(html, url) if html else None

rows = await asyncio.gather(*(worker(u) for u in urls))
```

It also **retries with exponential backoff**. After a `429` or a server error (`5xx`), it waits 2 s, then 4 s, then 8 s before trying again, giving the server room to recover. A `404` is never retried, because the listing is gone. With this setup, the full collection took **46 minutes**: concurrency makes a scraper faster, and the semaphore and the pauses keep it polite.

---

## Ethics

This scraper follows the site's `robots.txt`: it fetches only the search and listing pages that robots.txt allows, and removes the tracking parameters that it disallows. It keeps the load low, with a pause after every request, at most three requests at a time in the full run, and a one-off collection of 100 listings per neighbourhood. It collects no personal data: no names, phone numbers, photos or descriptions, only facts about the property. It doesn't hide that it's automated.

The coordinates aren't printed as text on the listing pages. The site shows them on its map and publishes them in the page's structured data for search engines. Many listings hide their street address, so I never publish individual listings or locations. The data stays on my machine, and later articles only show results aggregated over areas of about 0.7 km².

Finally, like many websites, QuintoAndar's Terms of Use don't permit automated data collection. This is a non-commercial learning project, and I chose to collect a small, private sample under the rules above. If you adapt this code, read the terms of the site you target, and prefer an official API or an open dataset when one exists.

---

## Try it yourself

```bash
pip install selenium requests beautifulsoup4 lxml pandas
python scrape_listings.py --bairro liberdade --n 30
```

```
[1/3] Collecting 30 listing URLs from https://www.quintoandar.com.br/alugar/imovel/liberdade-sao-paulo-sp-brasil
      24 URLs so far
      36 URLs so far
[2/3] Downloading 30 listing pages (1.0s pause between them)
[3/3] Saved 30 listings to listings_liberdade.csv in 81s
      with coordinates: 100%
   bedrooms  area_m2  parking  Aluguel  Total
0         3      170        1     7000   8952
1         3      171        1     3520   6444
2         3      130        1     4780   5865
3         3      163        1     9800  10237
4         3      120        1     4500   6274
```

The first 24 URLs come with the page. The jump to 36 is one click on "Load more", and the list is then cut to the 30 requested. Downloading the 30 pages takes most of the 81 seconds, mainly because of the one-second pauses. The coordinates are saved in the CSV but deliberately left out of the preview.

Change `--bairro` to any neighbourhood slug from the site's URLs, such as `pinheiros` or `vila-mariana`. Asking for more than 24 listings makes the "Load more" loop run. Keep the numbers small, and keep the delays.

---

## What's next

The scraper produces a table of homes with prices and locations. Next, I'll load it into **PostgreSQL with PostGIS**, index each listing on an **H3 hexagonal grid**, and write the spatial queries the rest of the project depends on, such as *"listings within 500 m of a metro station"*.

*Code: [github.com/devienne/where_should_ai_live](https://github.com/devienne/where_should_ai_live)*
