"""
Spatial SQL in one file: PostGIS + H3 on a handful of rental listings.

Companion to "Where should AI live? — Part 2". Standalone: no project code needed.

1. Start a throw-away PostGIS database (Docker):
       docker run --name postgis-demo -e POSTGRES_PASSWORD=demo -p 5434:5432 -d postgis/postgis:16-3.4
2. Install the libraries:
       pip install "psycopg[binary]" pandas h3
3. Run it on synthetic listings...
       python spatial_queries.py
   ...or on the CSV made by Part 1's scrape_listings.py:
       python spatial_queries.py --csv ../01_scraping/listings_liberdade.csv

Everything goes into a TEMPORARY table, which disappears when the script ends,
so it is safe to point at any database.
"""

import argparse                                   # command-line options
import time                                       # timing

import h3                                         # Uber's hexagonal grid
import numpy as np                                # synthetic data
import pandas as pd                               # reading the CSV / printing tables
import psycopg                                    # PostgreSQL driver

DEFAULT_DB = "postgresql://postgres:demo@localhost:5434/postgres"   # the docker run above
PRACA_DA_SE = (-23.5503, -46.6339)                # (lat, lon) of São Paulo's "kilometre zero"
H3_RESOLUTION = 8                                 # ~0.7 km² hexagons

# A temporary copy of the project's `listings` table, with only the columns used here
CREATE_TABLE = """
CREATE TEMP TABLE listings (
    listing_id  SERIAL PRIMARY KEY,
    area_m2     REAL NOT NULL CHECK (area_m2 > 0),
    bedrooms    SMALLINT,
    total_cost  NUMERIC(10, 2) NOT NULL,
    lat         DOUBLE PRECISION NOT NULL,
    lon         DOUBLE PRECISION NOT NULL,
    h3_r8       TEXT NOT NULL,
    -- point computed by the database itself; geography = distances in metres
    geog        GEOGRAPHY(POINT, 4326)
                GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED
);
CREATE INDEX ON listings USING GIST (geog);       -- spatial index: "near X" without scanning every row
CREATE INDEX ON listings (h3_r8);
"""

# The four queries the article walks through. %(lat)s etc. are filled in safely by psycopg.
QUERIES = {
    "1. Listings within the radius (ST_DWithin)": """
        SELECT COUNT(*)                                                      AS listings,
               ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY total_cost)::numeric, 0) AS median_total_cost
        FROM listings
        WHERE ST_DWithin(geog, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(radius)s)
    """,
    "2. The 5 nearest listings (<-> nearest-neighbour operator)": """
        SELECT listing_id, area_m2, bedrooms, total_cost,
               ROUND(ST_Distance(geog, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography)) AS metres
        FROM listings
        ORDER BY geog <-> ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography
        LIMIT 5
    """,
    "3. Listings per H3 cell (GROUP BY a hexagon id)": """
        SELECT h3_r8,
               COUNT(*)                                                                   AS listings,
               ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY total_cost / area_m2)::numeric, 1) AS median_cost_m2
        FROM listings
        GROUP BY h3_r8
        ORDER BY listings DESC
        LIMIT 8
    """,
    "4. Cheapest per m² within its own H3 cell (window function)": """
        WITH ranked AS (
            SELECT listing_id, h3_r8, area_m2, total_cost,
                   ROUND((total_cost / area_m2)::numeric, 1)                         AS cost_m2,
                   RANK() OVER (PARTITION BY h3_r8 ORDER BY total_cost / area_m2)    AS rank_in_cell,
                   COUNT(*) OVER (PARTITION BY h3_r8)                                AS cell_listings
            FROM listings
        )
        SELECT * FROM ranked
        WHERE rank_in_cell = 1 AND cell_listings >= 3
        ORDER BY cost_m2
        LIMIT 8
    """,
}


def synthetic_listings(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """Fake listings scattered around central São Paulo (clearly not real data)."""
    rng = np.random.default_rng(seed)
    lat = PRACA_DA_SE[0] + rng.normal(0, 0.02, n)        # ~2 km spread
    lon = PRACA_DA_SE[1] + rng.normal(0, 0.02, n)
    area = rng.lognormal(np.log(55), 0.5, n).round()     # typical 55 m², long right tail
    km = np.hypot((lat - PRACA_DA_SE[0]) * 111, (lon - PRACA_DA_SE[1]) * 102)
    cost_m2 = rng.normal(95 - 8 * km, 15).clip(25)       # cheaper further from the centre
    return pd.DataFrame({"area_m2": area, "bedrooms": rng.integers(1, 4, n),
                         "total_cost": (area * cost_m2).round(), "lat": lat, "lon": lon})


def from_scraper_csv(path: str) -> pd.DataFrame:
    """Read the CSV written by Part 1's scrape_listings.py."""
    df = pd.read_csv(path)
    df = df.rename(columns={"Total": "total_cost"})
    df = df.dropna(subset=["lat", "lon", "area_m2", "total_cost"])
    return df[df["area_m2"] > 0][["area_m2", "bedrooms", "total_cost", "lat", "lon"]]


def main():
    parser = argparse.ArgumentParser(description="PostGIS + H3 spatial queries on rental listings.")
    parser.add_argument("--csv", help="CSV from Part 1's scrape_listings.py (default: synthetic listings)")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"PostgreSQL URL (default {DEFAULT_DB})")
    parser.add_argument("--lat", type=float, default=PRACA_DA_SE[0], help="centre latitude (default Praça da Sé)")
    parser.add_argument("--lon", type=float, default=PRACA_DA_SE[1], help="centre longitude")
    parser.add_argument("--radius", type=int, default=1000, help="radius in metres for query 1 (default 1000)")
    args = parser.parse_args()

    df = from_scraper_csv(args.csv) if args.csv else synthetic_listings()
    source = args.csv or "synthetic listings"
    # H3 cell of every listing, computed in Python (the h3 library) before loading
    df["h3_r8"] = [h3.latlng_to_cell(la, lo, H3_RESOLUTION) for la, lo in zip(df["lat"], df["lon"])]
    print(f"[1/3] {len(df)} listings from {source}, in {df['h3_r8'].nunique()} H3 cells")

    with psycopg.connect(args.db, connect_timeout=5) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        conn.execute(CREATE_TABLE)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO listings (area_m2, bedrooms, total_cost, lat, lon, h3_r8) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                df[["area_m2", "bedrooms", "total_cost", "lat", "lon", "h3_r8"]]
                .astype(object).where(df.notna(), None).itertuples(index=False, name=None),
            )
        print("[2/3] Loaded into a temporary PostGIS table (geography point + GiST index)")

        print(f"[3/3] Queries around ({args.lat}, {args.lon}), radius {args.radius} m\n")
        params = {"lat": args.lat, "lon": args.lon, "radius": args.radius}
        for title, sql in QUERIES.items():
            start = time.perf_counter()
            with conn.cursor() as cur:
                cur.execute(sql, params)
                result = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
            print(f"{title}  ({(time.perf_counter() - start) * 1000:.0f} ms)")
            print(result.to_string(index=False) if len(result) else "   (no rows)", end="\n\n")


if __name__ == "__main__":
    main()
