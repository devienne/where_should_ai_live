"""Create the database tables from db/schema.sql.

Safe to re-run: the schema only creates what doesn't exist yet.
    python -m db.init_db
"""

from pathlib import Path

from sqlalchemy import text

from .connection import get_engine

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def init_db() -> None:
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    engine = get_engine()

    # One transaction: either the whole schema is applied, or nothing is
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)

    # Show what exists now
    with engine.connect() as conn:
        tables = conn.execute(text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
              AND table_name <> 'spatial_ref_sys'          -- PostGIS's own reference table
            ORDER BY table_name
        """)).scalars().all()
    print(f"Schema applied from {SCHEMA_FILE.name}. Tables: {', '.join(tables)}")


if __name__ == "__main__":
    init_db()
