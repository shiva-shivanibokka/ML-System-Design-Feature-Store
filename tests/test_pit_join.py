from datetime import datetime

import duckdb
import pandas as pd

from feature_store import offline_store as off
from feature_store.connections import _DuckClient
from feature_store.schema import apply_schema


def _seed_history(client):
    apply_schema(client)
    # entity 1 has two snapshots: past value 5, future value 999
    rows = [
        (
            1,
            "user",
            "v1",
            datetime(2024, 1, 1),
            5,
            5,
            5,
            5,
            5,
            5,
            5,
            0.0,
            1,
            0,
            0,
            10,
            2,
            datetime(2024, 1, 1),
        ),
        (
            1,
            "user",
            "v1",
            datetime(2024, 3, 1),
            999,
            999,
            999,
            9,
            9,
            9,
            9,
            0.0,
            1,
            0,
            0,
            70,
            2,
            datetime(2024, 3, 1),
        ),
    ]
    cols = [
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
    client.register("h", pd.DataFrame(rows, columns=cols))
    client.execute("INSERT INTO feature_history SELECT * FROM h")


def test_pit_join_never_leaks_future(monkeypatch):
    client = _DuckClient(duckdb.connect(":memory:"))
    _seed_history(client)
    monkeypatch.setattr(off, "get_duckdb_client", lambda: client)
    df = off.get_training_dataset([(1, datetime(2024, 2, 1))], feature_version="v1")
    # As of 2024-02-01 the only valid snapshot is 2024-01-01 (value 5), NOT 999.
    assert df.loc[0, "txn_count_7d"] == 5
