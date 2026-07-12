# tests/test_serving.py
import duckdb
import fakeredis
from fastapi.testclient import TestClient

import serving.main as main
from feature_store import online_store
from feature_store.connections import _DuckClient
from feature_store.schema import apply_schema


def _client(monkeypatch):
    duck = _DuckClient(duckdb.connect(":memory:"))
    apply_schema(duck)
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(main, "get_duckdb_client", lambda: duck)
    monkeypatch.setattr(main, "get_redis_client", lambda: fake)
    # online_store.py imported get_redis_client into its own module namespace,
    # so patching main's reference alone doesn't reach get_entity/
    # get_entities_batch/get_online_store_size, which call it directly.
    monkeypatch.setattr(online_store, "get_redis_client", lambda: fake)
    return TestClient(main.app), duck, fake


def test_health_ok(monkeypatch):
    client, _, _ = _client(monkeypatch)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["components"] == {"redis": "ok", "duckdb": "ok"}


def test_health_degraded_when_one_store_down(monkeypatch):
    """Redis down, DuckDB up -> still serves Redis-hit-independent traffic,
    so this must be 200 "degraded", not a hard 503."""
    client, _, _ = _client(monkeypatch)
    monkeypatch.setattr(
        main, "get_redis_client", lambda: (_ for _ in ()).throw(RuntimeError("down"))
    )
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["components"]["duckdb"] == "ok"
    assert body["components"]["redis"].startswith("down")


def test_health_degraded_when_schema_missing(monkeypatch):
    """A SELECT 1 used to report healthy even when apply_schema/sync_registry
    never ran. The health check must read a real table (feature_registry)
    so a missing schema surfaces as a down component, not silent 'ok'."""
    duck = _DuckClient(duckdb.connect(":memory:"))  # schema NOT applied
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(main, "get_duckdb_client", lambda: duck)
    monkeypatch.setattr(main, "get_redis_client", lambda: fake)
    client = TestClient(main.app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["components"]["duckdb"].startswith("down")
    assert body["components"]["redis"] == "ok"


def test_health_503_when_all_stores_down(monkeypatch):
    client, _, _ = _client(monkeypatch)
    monkeypatch.setattr(
        main, "get_redis_client", lambda: (_ for _ in ()).throw(RuntimeError("down"))
    )
    monkeypatch.setattr(
        main, "get_duckdb_client", lambda: (_ for _ in ()).throw(RuntimeError("down"))
    )
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["status"] == "down"


def test_features_single_entity_redis_hit(monkeypatch):
    client, _, fake = _client(monkeypatch)
    fake.hset("entity:user:1", mapping={"txn_count_7d": "3.0", "plan_encoded": "2.0"})
    r = client.get("/features/1")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "online_store"
    assert body["features"]["txn_count_7d"] == 3.0


def test_features_single_entity_on_demand_miss(monkeypatch):
    client, duck, _ = _client(monkeypatch)
    duck.execute(
        "INSERT INTO raw_users VALUES (42, DATE '2024-01-01', 'US', 'pro', '25-34', now())"
    )
    r = client.get("/features/42")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "on_demand"
    assert body["features"]["plan_encoded"] == 2.0


def test_features_single_entity_404_unknown(monkeypatch):
    client, _, _ = _client(monkeypatch)
    r = client.get("/features/9999")
    assert r.status_code == 404


def test_features_batch_mixed_hit_miss(monkeypatch):
    client, duck, fake = _client(monkeypatch)
    fake.hset("entity:user:1", mapping={"txn_count_7d": "5.0", "plan_encoded": "1.0"})
    duck.execute(
        "INSERT INTO raw_users VALUES (2, DATE '2024-01-01', 'US', 'basic', '25-34', now())"
    )
    r = client.post(
        "/features/batch", json={"entity_ids": [1, 2], "feature_version": "v1"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["hits"] == 1
    assert body["on_demand_computed"] == 1
    assert body["misses"] == 1
    assert body["results"]["1"]["txn_count_7d"] == 5.0
    assert body["results"]["2"]["plan_encoded"] == 1.0


def test_skew_report_cached_on_second_call(monkeypatch):
    client, _, _ = _client(monkeypatch)
    main._skew_cache.clear()
    calls = []

    def fake_compute(feature_version="v1"):
        calls.append(feature_version)
        return [{"feature_name": "x"}]

    monkeypatch.setattr(main, "compute_skew_report", fake_compute)

    r1 = client.get("/skew-report")
    r2 = client.get("/skew-report")

    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()
    assert len(calls) == 1  # 2nd call served from cache, not recomputed
    main._skew_cache.clear()


def test_materialization_log_limit_out_of_range_is_422(monkeypatch):
    client, _, _ = _client(monkeypatch)
    r = client.get("/materialization-log?limit=100000")
    assert r.status_code == 422


def test_materialization_log_limit_within_range_ok(monkeypatch):
    client, _, _ = _client(monkeypatch)
    r = client.get("/materialization-log?limit=10")
    assert r.status_code == 200
    assert r.json() == []
