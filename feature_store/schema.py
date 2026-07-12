"""Apply the DuckDB schema (idempotent CREATE TABLE IF NOT EXISTS statements)."""

from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent.parent / "configs" / "schema.sql"


def apply_schema(client) -> None:
    sql = _SCHEMA_PATH.read_text()
    for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
        client.execute(stmt)
