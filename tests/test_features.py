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
    """Every feature must agree across the two serving paths, not just one.

    The README's central claim is that offline and on-demand serving are
    "impossible to drift apart by construction". An earlier version of this test
    selected txn_count_30d and then asserted only on plan_encoded -- the one
    feature that is time-invariant and therefore cannot diverge -- so the test
    named for the guarantee could not fail. Against the committed store the two
    paths differed on 8 of 13 features. Assert all of them.
    """
    client = _DuckClient(duckdb.connect(":memory:"))
    now = _seed(client)
    features.compute_and_store(client, snapshot_time=now, feature_version="v1")
    cols = ", ".join(features.FEATURE_COLS)
    row = client.execute(f"SELECT {cols} FROM feature_history WHERE entity_id = 1")[0]
    stored = dict(zip(features.FEATURE_COLS, (float(v) for v in row)))

    on_demand = features.compute_on_demand(client, entity_id=1, feature_version="v1")
    assert on_demand is not None
    mismatched = {
        col: (stored[col], on_demand[col])
        for col in features.FEATURE_COLS
        if stored[col] != on_demand[col]
    }
    assert (
        not mismatched
    ), f"serving paths disagree on {len(mismatched)} features: {mismatched}"


def test_compute_on_demand_batch_matches_single_entity_calls_and_handles_unknown():
    client = _DuckClient(duckdb.connect(":memory:"))
    _seed(client)
    single = features.compute_on_demand(client, entity_id=1, feature_version="v1")
    batch = features.compute_on_demand_batch(client, [1, 999], feature_version="v1")
    assert batch[1] is not None
    assert batch[1]["plan_encoded"] == single["plan_encoded"]
    assert batch[999] is None  # unknown entity resolves to None, not dropped


def test_open_tickets_does_not_read_resolutions_from_the_future():
    """A ticket resolved AFTER the snapshot was open AT the snapshot.

    open_tickets used to filter on raw_support_tickets.resolved, a current-state
    flag with no time attached, so a ticket still open at T but resolved later
    was counted as closed at T -- future information inside a historical feature,
    and open_tickets is a declared model_input. Reproduced before the fix: this
    asserted 1.0 and got 0.0.
    """
    client = _DuckClient(duckdb.connect(":memory:"))
    apply_schema(client)
    snapshot = datetime(2026, 1, 1)
    client.execute(
        "INSERT INTO raw_users VALUES (1, DATE '2025-01-01', 'US', 'pro', '25-34', now())"
    )
    client.execute(
        "INSERT INTO raw_support_tickets "
        "(ticket_id, user_id, severity, resolved, resolved_at, event_time) VALUES "
        # raised 10 days before the snapshot, resolved 20 days AFTER it
        "(1, 1, 'high', 1, TIMESTAMP '2026-01-21', TIMESTAMP '2025-12-22'), "
        # raised and resolved well before the snapshot: genuinely closed at T
        "(2, 1, 'low', 1, TIMESTAMP '2025-12-05', TIMESTAMP '2025-12-01'), "
        # still unresolved
        "(3, 1, 'low', 0, NULL, TIMESTAMP '2025-12-28')"
    )
    row = features.compute_on_demand_at(client, entity_id=1, snapshot_time=snapshot)
    assert row is not None
    # tickets 1 and 3 were open at the snapshot; ticket 2 was not.
    assert row["open_tickets"] == 2.0, (
        f"expected 2 open tickets as of {snapshot}, got {row['open_tickets']} -- "
        "a resolution dated after the snapshot leaked backwards"
    )
