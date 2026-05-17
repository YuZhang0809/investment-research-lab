from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence


def require_duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb is required for local Parquet queries. Install requirements.txt first.") from exc
    return duckdb


def query(sql: str, parameters: Sequence[Any] | None = None):
    duckdb = require_duckdb()
    with duckdb.connect(database=":memory:") as connection:
        return connection.execute(sql, parameters or []).df()


def query_rows(sql: str, parameters: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    return query(sql, parameters).to_dict(orient="records")


def parquet_scan(path: Path) -> str:
    escaped = path.as_posix().replace("'", "''")
    return f"read_parquet('{escaped}')"
