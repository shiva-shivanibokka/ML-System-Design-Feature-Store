from feature_store.connections import get_duckdb_client, get_redis_client


def test_duckdb_client_executes_scalar():
    client = get_duckdb_client()
    rows = client.execute("SELECT 1 + 1 AS two")
    assert rows == [(2,)]


def test_duckdb_named_params():
    client = get_duckdb_client()
    rows = client.execute("SELECT $x + $y AS s", {"x": 3, "y": 4})
    assert rows[0][0] == 7


def test_redis_client_from_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    get_redis_client.cache_clear()
    r = get_redis_client()
    assert r.connection_pool.connection_kwargs["host"] == "localhost"
