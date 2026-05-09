"""
feature_store/offline_store.py
==============================
Offline feature store backed by ClickHouse.

Core capabilities:
  1. compute_features()       — compute features for all entities at a given timestamp
  2. point_in_time_join()     — PIT-correct join for training dataset generation
  3. get_training_dataset()   — pull PIT-correct training data with labels
  4. get_feature_stats()      — distribution stats for skew detection

Point-in-time correctness:
  For each (entity_id, label_timestamp) pair, we retrieve the latest feature
  row where event_time <= label_timestamp. This prevents future data from
  leaking into training labels — the #1 production bug in feature stores built
  without this constraint.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import structlog

from feature_store.connections import get_clickhouse_client
from feature_store.registry import get_feature_names, load_feature_config

log = structlog.get_logger()

FEATURE_COLS = [
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
]


# ---------------------------------------------------------------------------
# Feature computation — writes to feature_history
# ---------------------------------------------------------------------------

_COMPUTE_SQL = """
INSERT INTO feature_history
(entity_id, entity_type, feature_version, event_time,
 txn_count_7d, txn_count_30d, txn_count_90d,
 total_spend_7d, total_spend_30d, total_spend_90d,
 avg_txn_amount_30d, failed_txn_rate_30d, days_since_last_txn,
 open_tickets, ticket_rate_30d,
 account_age_days, plan_encoded)
SELECT
    u.user_id                                               AS entity_id,
    'user'                                                  AS entity_type,
    %(version)s                                             AS feature_version,
    %(snapshot_time)s                                       AS event_time,

    -- Transaction counts
    countIf(t.status = 'success' AND t.event_time >= %(t7)s
            AND t.event_time < %(snapshot_time)s)           AS txn_count_7d,
    countIf(t.status = 'success' AND t.event_time >= %(t30)s
            AND t.event_time < %(snapshot_time)s)           AS txn_count_30d,
    countIf(t.status = 'success' AND t.event_time >= %(t90)s
            AND t.event_time < %(snapshot_time)s)           AS txn_count_90d,

    -- Spend
    sumIf(t.amount, t.status = 'success' AND t.event_time >= %(t7)s
          AND t.event_time < %(snapshot_time)s)             AS total_spend_7d,
    sumIf(t.amount, t.status = 'success' AND t.event_time >= %(t30)s
          AND t.event_time < %(snapshot_time)s)             AS total_spend_30d,
    sumIf(t.amount, t.status = 'success' AND t.event_time >= %(t90)s
          AND t.event_time < %(snapshot_time)s)             AS total_spend_90d,

    -- Average transaction amount
    avgIf(t.amount, t.status = 'success' AND t.event_time >= %(t30)s
          AND t.event_time < %(snapshot_time)s)             AS avg_txn_amount_30d,

    -- Failed transaction rate
    countIf(t.status = 'failed' AND t.event_time >= %(t30)s
            AND t.event_time < %(snapshot_time)s)
    / greatest(countIf(t.event_time >= %(t30)s
                       AND t.event_time < %(snapshot_time)s), 1) AS failed_txn_rate_30d,

    -- Recency
    toFloat32(dateDiff('day', maxIf(t.event_time, t.status = 'success'),
               %(snapshot_time)s))                          AS days_since_last_txn,

    -- Support tickets
    countIf(sk.resolved = 0 AND sk.event_time < %(snapshot_time)s) AS open_tickets,
    countIf(sk.event_time >= %(t30)s AND sk.event_time < %(snapshot_time)s) AS ticket_rate_30d,

    -- Profile
    toFloat32(dateDiff('day', u.signup_date, toDate(%(snapshot_time)s))) AS account_age_days,
    multiIf(u.plan_type='free',0, u.plan_type='basic',1,
            u.plan_type='pro',2, 3)                         AS plan_encoded

FROM raw_users u
LEFT JOIN raw_transactions t ON t.user_id = u.user_id
LEFT JOIN raw_support_tickets sk ON sk.user_id = u.user_id
GROUP BY u.user_id, u.signup_date, u.plan_type
"""


def compute_features(
    snapshot_time: datetime | None = None,
    feature_version: str = "v1",
) -> int:
    """
    Compute features for ALL users at `snapshot_time` and append to feature_history.
    Returns the number of entity rows written.
    """
    client = get_clickhouse_client()
    ts = snapshot_time or datetime.utcnow()

    params = {
        "version": feature_version,
        "snapshot_time": ts,
        "t7": ts - timedelta(days=7),
        "t30": ts - timedelta(days=30),
        "t90": ts - timedelta(days=90),
    }

    log.info(
        "computing_features", snapshot_time=ts.isoformat(), version=feature_version
    )
    client.execute(_COMPUTE_SQL, params)

    (count,) = client.execute(
        "SELECT count() FROM feature_history WHERE event_time = %(ts)s AND feature_version = %(v)s",
        {"ts": ts, "v": feature_version},
    )[0]
    log.info("features_computed", rows=count, snapshot_time=ts.isoformat())
    return count


# ---------------------------------------------------------------------------
# Point-in-time correct training dataset
# ---------------------------------------------------------------------------


def get_training_dataset(
    label_timestamps: list[tuple[int, datetime]],  # [(entity_id, label_time), ...]
    feature_version: str = "v1",
) -> pd.DataFrame:
    """
    For each (entity_id, label_timestamp) pair, retrieve the most recent
    feature row where event_time <= label_timestamp.

    This is the core PIT-correct join — prevents future feature values from
    leaking into training labels.

    Returns a DataFrame with columns: entity_id, label_timestamp, <features...>
    """
    if not label_timestamps:
        return pd.DataFrame()

    client = get_clickhouse_client()

    # Build a VALUES table for the label timestamps
    label_rows = [(eid, ts) for eid, ts in label_timestamps]

    log.info("pit_join_start", entities=len(label_rows), version=feature_version)

    # Upload label timestamps as a temporary in-memory table via VALUES
    rows = client.execute(
        f"""
        SELECT
            labels.entity_id,
            labels.label_time,
            fh.txn_count_7d, fh.txn_count_30d, fh.txn_count_90d,
            fh.total_spend_7d, fh.total_spend_30d, fh.total_spend_90d,
            fh.avg_txn_amount_30d, fh.failed_txn_rate_30d, fh.days_since_last_txn,
            fh.open_tickets, fh.ticket_rate_30d,
            fh.account_age_days, fh.plan_encoded
        FROM
        (
            SELECT
                arrayJoin(%(label_rows)s) AS label_pair,
                label_pair.1 AS entity_id,
                label_pair.2 AS label_time
        ) AS labels
        LEFT JOIN LATERAL
        (
            SELECT *
            FROM feature_history
            WHERE entity_id = labels.entity_id
              AND feature_version = %(version)s
              AND event_time <= labels.label_time
            ORDER BY event_time DESC
            LIMIT 1
        ) AS fh ON 1=1
        """,
        {"label_rows": label_rows, "version": feature_version},
    )

    columns = ["entity_id", "label_timestamp"] + FEATURE_COLS
    df = pd.DataFrame(rows, columns=columns)
    log.info("pit_join_complete", rows=len(df))
    return df


def get_latest_features_for_entities(
    entity_ids: list[int],
    feature_version: str = "v1",
    as_of: datetime | None = None,
) -> pd.DataFrame:
    """
    Retrieve the most recent feature snapshot for a list of entity IDs.
    Used by materialization to push to online store and by on-demand path.
    """
    client = get_clickhouse_client()
    cutoff = as_of or datetime.utcnow()

    rows = client.execute(
        f"""
        SELECT
            entity_id,
            {", ".join(FEATURE_COLS)}
        FROM feature_history
        WHERE entity_id IN %(ids)s
          AND feature_version = %(version)s
          AND event_time <= %(cutoff)s
        ORDER BY entity_id, event_time DESC
        LIMIT 1 BY entity_id
        """,
        {"ids": entity_ids, "version": feature_version, "cutoff": cutoff},
    )

    columns = ["entity_id"] + FEATURE_COLS
    return pd.DataFrame(rows, columns=columns)


def get_feature_stats(
    feature_version: str = "v1",
    since_days: int = 7,
) -> dict[str, dict]:
    """
    Compute distribution statistics for all features over the last `since_days`.
    Used by the skew detection module to capture training-time distributions.
    """
    client = get_clickhouse_client()
    since = datetime.utcnow() - timedelta(days=since_days)

    stats = {}
    for col in FEATURE_COLS:
        rows = client.execute(
            f"""
            SELECT
                avg({col})                      AS mean,
                stddevPop({col})                AS std,
                quantile(0.25)({col})           AS p25,
                quantile(0.50)({col})           AS p50,
                quantile(0.75)({col})           AS p75,
                quantile(0.95)({col})           AS p95,
                countIf(isNaN({col}) OR isNull({col})) / count() AS null_rate,
                count()                          AS sample_count
            FROM feature_history
            WHERE feature_version = %(version)s
              AND event_time >= %(since)s
            """,
            {"version": feature_version, "since": since},
        )
        if rows:
            mean, std, p25, p50, p75, p95, null_rate, n = rows[0]
            stats[col] = {
                "mean": float(mean or 0),
                "std": float(std or 0),
                "p25": float(p25 or 0),
                "p50": float(p50 or 0),
                "p75": float(p75 or 0),
                "p95": float(p95 or 0),
                "null_rate": float(null_rate or 0),
                "sample_count": int(n or 0),
            }
    return stats
