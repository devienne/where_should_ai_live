"""Run the named queries stored in db/queries.sql.

    from db.queries import run_query
    df = run_query("cost_by_zone")
"""

import re
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .connection import get_engine

QUERIES_FILE = Path(__file__).with_name("queries.sql")
NAME_LINE = re.compile(r"^-- name: (\w+)\s*$", re.MULTILINE)


def load_queries(path: Path = QUERIES_FILE) -> Dict[str, str]:
    """{name: sql} for every '-- name: <name>' block in the file (re-read on each call)."""
    parts = NAME_LINE.split(path.read_text(encoding="utf-8"))   # [header, name1, sql1, name2, sql2, ...]
    return {name: sql.strip() for name, sql in zip(parts[1::2], parts[2::2])}


def run_query(name: str, engine: Optional[Engine] = None) -> pd.DataFrame:
    """Run one named query and return the result as a DataFrame."""
    queries = load_queries()
    if name not in queries:
        raise KeyError(f"Unknown query {name!r}. Available: {', '.join(queries)}")
    return pd.read_sql(text(queries[name]), engine or get_engine())
