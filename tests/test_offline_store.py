# tests/test_offline_store.py
"""Tests for feature_store/offline_store.py: point-in-time reads and stats."""

from datetime import datetime, timedelta

import duckdb
import pandas as pd

from feature_store import offline_store as off
from feature_store.connections import _DuckClient
from feature_store.schema import apply_schema

_COLS = [
    "entity_id",
    "entity_type",
    "feature_version",
    "event_time",
    "txn_count_7d",
    "txn_count_30d",
    "txn_count_90d",
    "total_spend_7d",
    "total_spend_30d",
    "total_spend_90d",
    "avg_txn_amount_30d",
    "failed_txn_rate_30d",
    "days_since_last_txn",
    "open_tickets",
    "ticket_rate_30d",
    "account_age_days",
    "plan_encoded",
    "computed_at",
]


def _row(entity_id, event_time, txn7, computed_at=None):
    return (
        entity_id,
        "user",
        "v1",
        event_time,
        txn7,
        txn7,
        txn7,
        txn7,
        txn7,
        txn7,
        txn7,
        0.0,
        1,
        0,
        0,
        10,
        2,
        computed_at or event_time,
    )


def _insert(client, rows):
    client.register("h", pd.DataFrame(rows, columns=_COLS))
    client.execute("INSERT INTO feature_history SELECT * FROM h")
    client.unregister("h")


def test_get_latest_features_for_entities_returns_only_latest_row_as_of_cutoff(
    monkeypatch,
):
    client = _DuckClient(duckdb.connect(":memory:"))
    apply_schema(client)
    _insert(
        client,
        [
            _row(1, datetime(2024, 1, 1), 5),
            _row(1, datetime(2024, 3, 1), 999),
        ],
    )
    monkeypatch.setattr(off, "get_duckdb_client", lambda: client)

    # As-of a cutoff between the two snapshots -> only the Jan 1 row qualifies.
    df = off.get_latest_features_for_entities(
        [1], feature_version="v1", as_of=datetime(2024, 2, 1)
    )
    assert len(df) == 1
    assert df.loc[0, "txn_count_7d"] == 5

    # As-of after both snapshots -> the latest (March) row wins, not the
    # earlier one and not both.
    df2 = off.get_latest_features_for_entities(
        [1], feature_version="v1", as_of=datetime(2024, 4, 1)
    )
    assert len(df2) == 1
    assert df2.loc[0, "txn_count_7d"] == 999


def test_get_feature_stats_math_on_known_values(monkeypatch):
    client = _DuckClient(duckdb.connect(":memory:"))
    apply_schema(client)
    now = datetime.utcnow()
    _insert(
        client,
        [
            _row(1, now - timedelta(hours=1), 10, computed_at=now),
            _row(2, now - timedelta(hours=1), 20, computed_at=now),
        ],
    )
    monkeypatch.setattr(off, "get_duckdb_client", lambda: client)

    stats = off.get_feature_stats(feature_version="v1", since_days=7)
    txn = stats["txn_count_7d"]
    assert txn["mean"] == 15.0
    assert txn["p50"] == 15.0
    assert txn["sample_count"] == 2
    assert txn["null_rate"] == 0.0
