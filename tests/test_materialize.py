import duckdb

import materialization.materialize as mat
from feature_store import offline_store as off
from feature_store.connections import _DuckClient
from feature_store.schema import apply_schema


def _seed(client):
    apply_schema(client)
    client.execute(
        "INSERT INTO raw_users VALUES (1, DATE '2024-01-01', 'US', 'pro', '25-34', now())"
    )
    client.execute(
        "INSERT INTO feature_history "
        "(entity_id, entity_type, feature_version, event_time, "
        "txn_count_7d, txn_count_30d, txn_count_90d, total_spend_7d, "
        "total_spend_30d, total_spend_90d, avg_txn_amount_30d, "
        "failed_txn_rate_30d, days_since_last_txn, open_tickets, "
        "ticket_rate_30d, account_age_days, plan_encoded) "
        "VALUES (1, 'user', 'v1', DATE '2024-06-01', "
        "3, 3, 3, 175, 175, 175, 58.3, 0.0, 1, 0, 0, 100, 2)"
    )


def test_all_validation_failures_report_failed_status(monkeypatch):
    client = _DuckClient(duckdb.connect(":memory:"))
    _seed(client)
    monkeypatch.setattr(mat, "get_duckdb_client", lambda: client)
    monkeypatch.setattr(off, "get_duckdb_client", lambda: client)

    def _boom(df):
        raise ValueError("validation exploded")

    monkeypatch.setattr(mat, "validate_feature_batch", _boom)

    summary = mat.run_materialization(feature_version="v1")

    assert summary["processed"] == 0
    assert summary["validation_failures"] == 1
    assert summary["status"] == "failed"
