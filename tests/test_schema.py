import duckdb

from feature_store.connections import _DuckClient
from feature_store.schema import apply_schema


def test_apply_schema_creates_tables():
    client = _DuckClient(duckdb.connect(":memory:"))
    apply_schema(client)
    tables = {r[0] for r in client.execute("SHOW TABLES")}
    assert {
        "raw_users",
        "feature_history",
        "feature_registry",
        "materialization_log",
        "skew_snapshots",
        "lineage_edges",
    } <= tables
