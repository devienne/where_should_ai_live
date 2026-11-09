"""
QuintoAndar rental scraper — loops over the configured neighbourhoods.

Usage (from the project root, with the venv):
    .venv\\Scripts\\python -m scraper.main                      # all bairros in config.BAIRROS
    .venv\\Scripts\\python -m scraper.main --bairros pinheiros   # only some bairros
    .venv\\Scripts\\python -m scraper.main --force               # ignore recent CSVs and re-scrape
    .venv\\Scripts\\python -m scraper.main --max-listings 0      # no limit (default: 100 per bairro)
    .venv\\Scripts\\python -m scraper.main --list                # show bairros and exit

Output:
    data/raw/quintoandar_<slug>_<YYYYMMDD>.csv   one CSV per bairro
    data/raw/urls/urls_<slug>_<YYYYMMDD>.txt     collected listing URLs (reused if the run restarts the same day)
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd

from .config import (BAIRROS, LOG_LEVEL, MAX_AGE_DAYS, MAX_LISTINGS_PER_BAIRRO, RAW_DATA_DIR,
                     URLS_DIR, listing_url)
from .http_client import fetch_properties
from .models import PropertyInfo
from .url_collector import collect_urls

logger = logging.getLogger("scraper")

FIRST_COLUMNS = [
    "url", "bairro", "zone", "scraped_at", "status", "title", "address_street", "lat", "lon",
    "area", "quartos", "suite", "banheiros", "vagas", "andar", "pet", "mobiliado", "metro_proximo",
]


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------
def latest_csv(slug: str) -> Optional[Path]:
    """Most recent CSV for a bairro in data/raw (old snapshots in subfolders are ignored)."""
    files = sorted(RAW_DATA_DIR.glob(f"quintoandar_{slug}_*.csv"))
    return files[-1] if files else None


def is_recent(path: Path, max_age_days: int) -> bool:
    date_str = path.stem.rsplit("_", 1)[-1]
    try:
        file_date = datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        return False
    return datetime.now() - file_date < timedelta(days=max_age_days)


def load_urls(path: Path) -> List[str]:
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def save_urls(urls: List[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(urls) + "\n")


def save_results(properties: List[PropertyInfo], path: Path) -> pd.DataFrame:
    df = pd.DataFrame([p.to_dict() for p in properties])
    if not df.empty:
        ordered = [c for c in FIRST_COLUMNS if c in df.columns]
        df = df[ordered + [c for c in df.columns if c not in ordered]]
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return df


def print_summary(df: pd.DataFrame, path: Path) -> None:
    total = len(df)
    if total == 0:
        print(f"  Saved empty file: {path}")
        return
    ok = (df["status"] == "success").sum()
    with_address = df["address_street"].notna().sum()
    with_coords = df["lat"].notna().sum()
    print(f"  Saved: {path.relative_to(RAW_DATA_DIR.parent.parent)}")
    print(f"  Listings: {total} | parsed: {ok} ({ok / total:.0%}) | "
          f"address: {with_address / total:.0%} | lat/lon: {with_coords / total:.0%}")


# --------------------------------------------------------------------------
# Scraping
# --------------------------------------------------------------------------
async def scrape_bairro(slug: str, zone: str, max_listings: Optional[int]) -> None:
    today = datetime.now().strftime("%Y%m%d")
    urls_file = URLS_DIR / f"urls_{slug}_{today}.txt"
    csv_file = RAW_DATA_DIR / f"quintoandar_{slug}_{today}.csv"

    # Step 1: listing URLs (reuse today's file if the run was interrupted)
    if urls_file.exists():
        urls = load_urls(urls_file)
        print(f"  1/3 Reusing {len(urls)} URLs from {urls_file.name}")
    else:
        print(f"  1/3 Collecting URLs from {listing_url(slug)} ...")
        urls = collect_urls(listing_url(slug), max_listings)
        if not urls:
            print("  No URLs collected (check the slug in the browser). Skipping.")
            return
        save_urls(urls, urls_file)
        print(f"      {len(urls)} URLs saved to {urls_file.name}")

    if max_listings:
        urls = urls[:max_listings]

    # Step 2: fetch and parse each listing
    print(f"  2/3 Fetching {len(urls)} listings ...")
    properties = await fetch_properties(urls)
    scraped_at = datetime.now().isoformat(timespec="seconds")
    for p in properties:
        p.bairro, p.zone, p.scraped_at = slug, zone, scraped_at

    # Step 3: save
    print("  3/3 Saving ...")
    df = save_results(properties, csv_file)
    print_summary(df, csv_file)


async def run(bairros: List[str], force: bool, max_age_days: int,
              max_listings: Optional[int]) -> None:
    print("=== QuintoAndar scraper ===")
    print(f"Bairros: {len(bairros)} | max listings per bairro: {max_listings or 'no limit'} | "
          f"started {datetime.now():%Y-%m-%d %H:%M}")

    for i, slug in enumerate(bairros, start=1):
        zone = BAIRROS[slug]
        print(f"\n[{i}/{len(bairros)}] {slug} ({zone})")

        existing = latest_csv(slug)
        if existing and not force and is_recent(existing, max_age_days):
            print(f"  Skipping: recent file {existing.name} (use --force to re-scrape)")
            continue

        try:
            await scrape_bairro(slug, zone, max_listings)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # keep going with the next bairro
            logger.error(f"Failed on {slug}: {e}", exc_info=True)

    print(f"\nDone at {datetime.now():%Y-%m-%d %H:%M}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape QuintoAndar rentals per bairro.")
    parser.add_argument("--bairros", nargs="+", metavar="SLUG",
                        help="bairro slugs to scrape (default: all in config.BAIRROS)")
    parser.add_argument("--force", action="store_true", help="re-scrape even if a recent CSV exists")
    parser.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS,
                        help=f"a CSV younger than this counts as recent (default {MAX_AGE_DAYS})")
    parser.add_argument("--max-listings", type=int, default=MAX_LISTINGS_PER_BAIRRO,
                        help=f"max listings per bairro, 0 = no limit (default {MAX_LISTINGS_PER_BAIRRO})")
    parser.add_argument("--list", action="store_true", help="list configured bairros and exit")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging()

    if args.list:
        for slug, zone in BAIRROS.items():
            existing = latest_csv(slug)
            print(f"{zone:7s} {slug:22s} {existing.name if existing else '-'}")
        return

    bairros = args.bairros or list(BAIRROS)
    unknown = [b for b in bairros if b not in BAIRROS]
    if unknown:
        sys.exit(f"Unknown bairro slug(s): {unknown}. Add them to BAIRROS in scraper/config.py.")

    try:
        asyncio.run(run(bairros, args.force, args.max_age_days, args.max_listings or None))
    except KeyboardInterrupt:
        print("\nInterrupted by user. Re-run the same command to continue "
              "(finished bairros are skipped, today's URL files are reused).")


if __name__ == "__main__":
    main()
