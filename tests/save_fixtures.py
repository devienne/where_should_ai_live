"""
Save a few real listing pages as HTML fixtures for the parser tests.

The fixtures contain real listing data, so tests/fixtures/ is git-ignored.

Usage (from the project root):
    .venv\\Scripts\\python -m tests.save_fixtures            # uses the most recent liberdade CSV
    .venv\\Scripts\\python -m tests.save_fixtures --csv data/raw/quintoandar_pinheiros_20261109.csv
"""
import argparse
import time
from pathlib import Path

import httpx
import pandas as pd

from scraper.config import HEADERS, RAW_DATA_DIR, TIMEOUT

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PLACEHOLDER_ADDRESS = "Condomínios em São Paulo"


def pick_examples(df: pd.DataFrame) -> dict:
    """Pick up to 3 different kinds of listing."""
    examples = {}
    with_street = df[df.address_street.notna() & (df.address_street != PLACEHOLDER_ADDRESS)]
    hidden = df[df.address_street == PLACEHOLDER_ADDRESS]
    no_parking = df[df.vagas.isna()]
    if len(with_street):
        examples["listing_with_street"] = with_street.iloc[0].url
    if len(hidden):
        examples["listing_hidden_address"] = hidden.iloc[0].url
    if len(no_parking):
        candidates = no_parking[~no_parking.url.isin(examples.values())]
        if len(candidates):
            examples["listing_no_parking"] = candidates.iloc[0].url
    return examples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, help="scraped CSV to pick URLs from")
    args = parser.parse_args()

    csv = args.csv or sorted(RAW_DATA_DIR.glob("quintoandar_liberdade_*.csv"))[-1]
    df = pd.read_csv(csv)
    examples = pick_examples(df)

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True, http2=True) as client:
        for name, url in examples.items():
            html = client.get(url).text
            (FIXTURES_DIR / f"{name}.html").write_text(html, encoding="utf-8")
            (FIXTURES_DIR / f"{name}.url").write_text(url, encoding="utf-8")
            print(f"saved {name}.html  <- {url}")
            time.sleep(2)


if __name__ == "__main__":
    main()
