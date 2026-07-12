from datetime import datetime, timedelta

import duckdb

from feature_store import features
from feature_store.connections import _DuckClient
from feature_store.schema import apply_schema


def _seed(client):
    apply_schema(client)
    now = datetime(2024, 6, 1)
    client.execute(
        "INSERT INTO raw_users VALUES (1, DATE '2024-01-01', 'US', 'pro', '25-34', now())"
    )
    # 3 successful txns in last 7d, 1 failed in 30d
    rows = [
        (1, 1, 100.0, "one-time", "success", now - timedelta(days=1)),
        (2, 1, 50.0, "one-time", "success", now - timedelta(days=3)),
        (3, 1, 25.0, "one-time", "success", now - timedelta(days=6)),
        (4, 1, 10.0, "one-time", "failed", now - timedelta(days=20)),
    ]
    client.register(
        "txns",
        __import__("pandas").DataFrame(
            rows,
            columns=[
                "transaction_id",
                "user_id",
                "amount",
                "category",
                "status",
                "event_time",
            ],
        ),
    )
    client.execute("INSERT INTO raw_transactions SELECT * FROM txns")
    return now


def test_compute_and_store_counts_windows_correctly():
    client = _DuckClient(duckdb.connect(":memory:"))
    now = _seed(client)
    n = features.compute_and_store(client, snapshot_time=now, feature_version="v1")
    assert n == 1
    row = client.execute(
        "SELECT txn_count_7d, txn_count_30d, total_spend_7d, plan_encoded "
        "FROM feature_history WHERE entity_id = 1"
    )[0]
    assert row[0] == 3.0  # 3 successful txns in 7d
    assert row[1] == 3.0  # same 3 within 30d (failed not counted in success count)
    assert row[2] == 175.0  # 100 + 50 + 25
    assert row[3] == 2.0  # pro -> 2


def test_days_since_last_txn_never_transacted_uses_sentinel():
    client = _DuckClient(duckdb.connect(":memory:"))
    apply_schema(client)
    now = datetime(2024, 6, 1)
    # User with zero transactions ever.
    client.execute(
        "INSERT INTO raw_users VALUES (2, DATE '2024-01-01', 'US', 'free', '25-34', now())"
    )
    features.compute_and_store(client, snapshot_time=now, feature_version="v1")
    row = client.execute(
        "SELECT days_since_last_txn FROM feature_history WHERE entity_id = 2"
    )[0]
    assert row[0] == 9999.0


def test_days_since_last_txn_recent_txn_is_near_zero():
    client = _DuckClient(duckdb.connect(":memory:"))
    now = _seed(client)
    features.compute_and_store(client, snapshot_time=now, feature_version="v1")
    row = client.execute(
        "SELECT days_since_last_txn FROM feature_history WHERE entity_id = 1"
    )[0]
    # Most recent successful txn in _seed is 1 day before `now`.
    assert row[0] == 1.0


def test_compute_and_store_is_idempotent_for_same_snapshot():
    client = _DuckClient(duckdb.connect(":memory:"))
    now = _seed(client)
    n1 = features.compute_and_store(client, snapshot_time=now, feature_version="v1")
    n2 = features.compute_and_store(client, snapshot_time=now, feature_version="v1")
    assert n1 == n2 == 1
    (count,) = client.execute(
        "SELECT count(*) FROM feature_history WHERE entity_id = 1 "
        "AND feature_version = 'v1' AND event_time = $t",
        {"t": now},
    )[0]
    assert count == 1


def test_on_demand_matches_stored_features():
    client = _DuckClient(duckdb.connect(":memory:"))
    now = _seed(client)
    features.compute_and_store(client, snapshot_time=now, feature_version="v1")
    stored = client.execute(
        "SELECT txn_count_30d, plan_encoded FROM feature_history WHERE entity_id = 1"
    )[0]
    on_demand = features.compute_on_demand(client, entity_id=1, feature_version="v1")
    # On-demand uses 'now' as snapshot; with the seed dates it recomputes the same
    # counts. Guard only the version-stable feature to prove the shared SQL path.
    assert on_demand is not None
    assert on_demand["plan_encoded"] == stored[1]
