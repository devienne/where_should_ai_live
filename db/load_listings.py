"""Load data/processed/listings_clean.csv into the `listings` table.

- adds the H3 cell (resolution 8) of each listing
- upsert: new listings are inserted, existing ones (same listing_id) are updated,
  so it's safe to re-run after a new scrape
    python -m db.load_listings
"""

from pathlib import Path

import h3
import pandas as pd
from sqlalchemy import text

from .connection import get_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_FILE = PROJECT_ROOT / "data" / "processed" / "listings_clean.csv"
H3_RESOLUTION = 8
STAGING_TABLE = "listings_staging"

# Columns copied from the CSV, with the SQL type each is cast to.
# (geog and loaded_at are filled by the database itself.)
COLUMNS = {
    "listing_id": "TEXT", "url": "TEXT", "bairro": "TEXT", "zone": "TEXT",
    "scraped_at": "TIMESTAMP", "property_type": "TEXT", "address": "TEXT",
    "lat": "DOUBLE PRECISION", "lon": "DOUBLE PRECISION", "h3_r8": "TEXT",
    "area_m2": "REAL", "bedrooms": "SMALLINT", "suites": "SMALLINT",
    "bathrooms": "SMALLINT", "parking": "SMALLINT", "floor_min": "SMALLINT",
    "pet_friendly": "BOOLEAN", "furnished": "BOOLEAN", "metro_near": "BOOLEAN",
    "rent": "NUMERIC", "condo_fee": "NUMERIC", "condo_included": "BOOLEAN",
    "iptu": "NUMERIC", "fire_insurance": "NUMERIC", "service_fee": "NUMERIC",
    "total_cost": "NUMERIC", "cost_per_m2": "NUMERIC",
}


def read_listings() -> pd.DataFrame:
    df = pd.read_csv(INPUT_FILE, dtype={"listing_id": str})

    # listing_id is the primary key: it must be present and unique
    missing = df["listing_id"].isna().sum()
    dupes = df["listing_id"].duplicated().sum()
    if missing or dupes:
        raise ValueError(f"listing_id problems: {missing} missing, {dupes} duplicated")

    df["h3_r8"] = [h3.latlng_to_cell(lat, lon, H3_RESOLUTION) for lat, lon in zip(df["lat"], df["lon"])]
    return df[list(COLUMNS)]


def build_upsert_sql() -> str:
    cols = list(COLUMNS)
    select_list = ",\n        ".join(f"{c}::{t}" for c, t in COLUMNS.items())   # cast staging text/floats to final types
    update_list = ",\n        ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "listing_id")
    return f"""
    INSERT INTO listings ({", ".join(cols)})
    SELECT
        {select_list}
    FROM {STAGING_TABLE}
    ON CONFLICT (listing_id) DO UPDATE SET
        {update_list},
        loaded_at = now()
    """


def load_listings() -> None:
    df = read_listings()
    print(f"Read {len(df)} listings from {INPUT_FILE.relative_to(PROJECT_ROOT)} "
          f"({df['h3_r8'].nunique()} distinct H3 cells)")

    engine = get_engine()
    with engine.begin() as conn:                     # one transaction for the whole load
        before = conn.execute(text("SELECT COUNT(*) FROM listings")).scalar()
        # 1. dump the dataframe into a throw-away staging table
        df.to_sql(STAGING_TABLE, conn, if_exists="replace", index=False)
        # 2. move rows into the real table, inserting new and updating existing ones
        conn.execute(text(build_upsert_sql()))
        # 3. clean up
        conn.execute(text(f"DROP TABLE {STAGING_TABLE}"))
        after = conn.execute(text("SELECT COUNT(*) FROM listings")).scalar()

    print(f"listings table: {before} rows before -> {after} rows after "
          f"({after - before} new, {len(df) - (after - before)} updated)")


if __name__ == "__main__":
    load_listings()
