"""Database connection helper shared by every db/ script.

Reads the credentials from .env and returns a SQLAlchemy engine.
Run it directly to test the connection:
    python -m db.connection
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

# Load .env from the project root, whatever folder the script is started from
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def get_engine() -> Engine:
    """Build a SQLAlchemy engine for the PostGIS database described in .env."""
    url = URL.create(
        drivername="postgresql+psycopg",            # psycopg 3 driver
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],   # URL.create escapes special characters
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.environ["POSTGRES_DB"],
    )
    return create_engine(url)


def check_connection() -> None:
    """Connect, then print the PostgreSQL and PostGIS versions."""
    engine = get_engine()
    with engine.connect() as conn:
        pg_version = conn.execute(text("SELECT version()")).scalar()
        print(f"Connected to: {engine.url.render_as_string(hide_password=True)}")
        print(f"PostgreSQL:   {pg_version.split(',')[0]}")
        try:
            postgis = conn.execute(text("SELECT postgis_lib_version()")).scalar()
            print(f"PostGIS:      {postgis}")
        except Exception:
            print("PostGIS:      not enabled yet (db/schema.sql will enable it)")


if __name__ == "__main__":
    check_connection()
