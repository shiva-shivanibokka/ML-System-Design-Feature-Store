# tests/test_serving.py
from fastapi.testclient import TestClient
import fakeredis, duckdb
import serving.main as main
from feature_store.connections import _DuckClient
from feature_store.schema import apply_schema

def _client(monkeypatch):
    duck = _DuckClient(duckdb.connect(":memory:")); apply_schema(duck)
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(main, "get_duckdb_client", lambda: duck)
    monkeypatch.setattr(main, "get_redis_client", lambda: fake)
    return TestClient(main.app), duck, fake

def test_health_ok(monkeypatch):
    client, _, _ = _client(monkeypatch)
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"

def test_health_503_when_store_down(monkeypatch):
    client, _, _ = _client(monkeypatch)
    monkeypatch.setattr(main, "get_redis_client", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    r = client.get("/health")
    assert r.status_code == 503
