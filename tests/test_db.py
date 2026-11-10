"""
Tests for the PostGIS database (db/).

They need the Docker database to be running and loaded:
    docker compose up -d
    python -m db.init_db
    python -m db.load_listings
If the database can't be reached, every test here is skipped (not failed).
Nothing is written permanently: the "bad data" tests run inside transactions that are rolled back.
"""
from pathlib import Path

import h3
import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLEAN_CSV = PROJECT_ROOT / "data" / "processed" / "listings_clean.csv"


@pytest.fixture(scope="module")
def engine():
    """Connect once for the whole file; skip everything if the database isn't available."""
    try:
        from db.connection import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:                       # no .env, Docker not running, wrong password, ...
        pytest.skip(f"database not available: {type(exc).__name__}")
    return eng


def scalar(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
def test_postgis_enabled(engine):
    assert scalar(engine, "SELECT postgis_lib_version()").startswith("3.")


@pytest.mark.parametrize("table", ["listings", "areas", "travel_times", "geocode_cache"])
def test_table_exists(engine, table):
    assert scalar(engine, "SELECT to_regclass(:t) IS NOT NULL", t=f"public.{table}")


# --------------------------------------------------------------------------
# Loaded data
# --------------------------------------------------------------------------
def test_row_count_matches_csv(engine):
    if not CLEAN_CSV.exists():
        pytest.skip("no listings_clean.csv")
    assert scalar(engine, "SELECT COUNT(*) FROM listings") == len(pd.read_csv(CLEAN_CSV))


def test_h3_cells_are_valid_and_match_coordinates(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT lat, lon, h3_r8 FROM listings")).all()
    assert rows, "listings table is empty"
    for lat, lon, cell in rows:
        assert h3.is_valid_cell(cell)
        assert h3.get_resolution(cell) == 8
        assert cell == h3.latlng_to_cell(lat, lon, 8)       # cell really contains the point


def test_point_matches_lat_lon(engine):
    # the generated geog column must agree with lat/lon (catches swapped lat/lon)
    mismatches = scalar(engine, """
        SELECT COUNT(*) FROM listings
        WHERE abs(ST_Y(geog::geometry) - lat) > 1e-9 OR abs(ST_X(geog::geometry) - lon) > 1e-9
    """)
    assert mismatches == 0


def test_all_zones_present(engine):
    with engine.connect() as conn:
        zones = set(conn.execute(text("SELECT DISTINCT zone FROM listings")).scalars())
    assert zones == {"Centro", "Oeste", "Sul", "Norte", "Leste"}


def test_geography_distance_in_metres(engine):
    # Praça da Sé -> MASP is about 2.9 km (same pair as test_clean.test_haversine_known_distance)
    metres = scalar(engine, """
        SELECT ST_Distance(ST_SetSRID(ST_MakePoint(-46.6339, -23.5503), 4326)::geography,
                           ST_SetSRID(ST_MakePoint(-46.6559, -23.5614), 4326)::geography)
    """)
    assert 2500 < metres < 3200


def test_spatial_index_is_used(engine):
    with engine.connect() as conn:
        # Tell the planner to avoid a full scan if it can; on a tiny table it might otherwise
        # pick one. The test then proves the GiST index exists and fits ST_DWithin queries.
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(conn.execute(text("""
            EXPLAIN SELECT listing_id FROM listings
            WHERE ST_DWithin(geog, ST_SetSRID(ST_MakePoint(-46.6339, -23.5505), 4326)::geography, 1000)
        """)).scalars())
    assert "listings_geog_gix" in plan


# --------------------------------------------------------------------------
# Constraints reject bad data (each insert is rolled back)
# --------------------------------------------------------------------------
GOOD_LISTING = dict(
    listing_id="test-0", url="https://example.com/test-0", bairro="liberdade", zone="Centro",
    scraped_at="2026-11-10T12:00:00", lat=-23.56, lon=-46.63,
    h3_r8=h3.latlng_to_cell(-23.56, -46.63, 8), area_m2=40, total_cost=2000,
)
INSERT_LISTING = text("""
    INSERT INTO listings (listing_id, url, bairro, zone, scraped_at, lat, lon, h3_r8, area_m2, total_cost)
    VALUES (:listing_id, :url, :bairro, :zone, :scraped_at, :lat, :lon, :h3_r8, :area_m2, :total_cost)
""")


def test_good_listing_is_accepted(engine):
    # control case: proves the rejections below are caused by the bad value, not by the test row
    with engine.connect() as conn:
        conn.execute(INSERT_LISTING, GOOD_LISTING)
        conn.rollback()


@pytest.mark.parametrize("bad", [
    {"zone": "Nordeste"},           # not one of the 5 zones
    {"area_m2": 0},                 # area must be > 0
    {"total_cost": None},           # required
    {"listing_id": None},           # primary key can't be empty
])
def test_bad_listing_is_rejected(engine, bad):
    with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(INSERT_LISTING, {**GOOD_LISTING, **bad})
        conn.rollback()


def test_duplicate_listing_id_is_rejected(engine):
    existing = scalar(engine, "SELECT listing_id FROM listings LIMIT 1")
    if existing is None:
        pytest.skip("listings table is empty")
    with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(INSERT_LISTING, {**GOOD_LISTING, "listing_id": existing})
        conn.rollback()


@pytest.mark.parametrize("mode, minutes", [("bike", 10), ("car", -5)])
def test_bad_travel_time_is_rejected(engine, mode, minutes):
    with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(text("""
                INSERT INTO travel_times (work_h3, home_h3, mode, minutes)
                VALUES ('a', 'b', :mode, :minutes)
            """), {"mode": mode, "minutes": minutes})
        conn.rollback()
