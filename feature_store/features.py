"""
feature_store/features.py
=========================
THE single source of feature computation. Every path — offline backfill,
on-demand serving, and PIT training joins — computes features from THIS SQL.
Duplicating this logic anywhere else reintroduces training-serving skew, the
exact bug this project exists to prevent.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import structlog

from feature_store.validator import validate_single_entity

log = structlog.get_logger()

FEATURE_COLS = [
    "txn_count_7d", "txn_count_30d", "txn_count_90d",
    "total_spend_7d", "total_spend_30d", "total_spend_90d",
    "avg_txn_amount_30d", "failed_txn_rate_30d", "days_since_last_txn",
    "open_tickets", "ticket_rate_30d",
    "account_age_days", "plan_encoded",
]


def feature_select_sql(entity_filter: str = "") -> str:
    """Return the DuckDB SELECT computing all features as of $snapshot.

    Params expected by the caller: $version, $snapshot, $t7, $t30, $t90,
    and $uid when entity_filter references it.

    Transactions and support tickets are pre-aggregated per user in their own
    subqueries before joining to raw_users. Joining both raw tables directly
    off the same base row would form a cartesian product per user (every
    matching transaction row paired with every matching ticket row), silently
    inflating every count/sum whenever a user has more than one row in both
    tables — a classic join fan-out bug. Aggregating each source table on its
    own keeps every FILTER count/sum correct regardless of how many rows the
    other table contributes.
    """
    return f"""
    SELECT
        u.user_id                                                    AS entity_id,
        'user'                                                       AS entity_type,
        $version                                                     AS feature_version,
        $snapshot                                                    AS event_time,

        coalesce(t.txn_count_7d, 0)::DOUBLE                          AS txn_count_7d,
        coalesce(t.txn_count_30d, 0)::DOUBLE                         AS txn_count_30d,
        coalesce(t.txn_count_90d, 0)::DOUBLE                         AS txn_count_90d,

        coalesce(t.total_spend_7d, 0)::DOUBLE                        AS total_spend_7d,
        coalesce(t.total_spend_30d, 0)::DOUBLE                       AS total_spend_30d,
        coalesce(t.total_spend_90d, 0)::DOUBLE                       AS total_spend_90d,

        coalesce(t.avg_txn_amount_30d, 0)::DOUBLE                    AS avg_txn_amount_30d,
        coalesce(t.failed_txn_rate_30d, 0)::DOUBLE                   AS failed_txn_rate_30d,

        coalesce(date_diff('day', t.last_success_txn, $snapshot), 0)::DOUBLE
                                                                      AS days_since_last_txn,

        coalesce(sk.open_tickets, 0)::DOUBLE                         AS open_tickets,
        coalesce(sk.ticket_rate_30d, 0)::DOUBLE                      AS ticket_rate_30d,

        date_diff('day', u.signup_date, CAST($snapshot AS DATE))::DOUBLE AS account_age_days,
        CASE u.plan_type WHEN 'free' THEN 0 WHEN 'basic' THEN 1
             WHEN 'pro' THEN 2 ELSE 3 END::DOUBLE                    AS plan_encoded
    FROM raw_users u
    LEFT JOIN (
        SELECT
            user_id,
            count(*) FILTER (WHERE status='success'
                AND event_time >= $t7  AND event_time < $snapshot)   AS txn_count_7d,
            count(*) FILTER (WHERE status='success'
                AND event_time >= $t30 AND event_time < $snapshot)   AS txn_count_30d,
            count(*) FILTER (WHERE status='success'
                AND event_time >= $t90 AND event_time < $snapshot)   AS txn_count_90d,
            sum(amount) FILTER (WHERE status='success'
                AND event_time >= $t7  AND event_time < $snapshot)   AS total_spend_7d,
            sum(amount) FILTER (WHERE status='success'
                AND event_time >= $t30 AND event_time < $snapshot)   AS total_spend_30d,
            sum(amount) FILTER (WHERE status='success'
                AND event_time >= $t90 AND event_time < $snapshot)   AS total_spend_90d,
            avg(amount) FILTER (WHERE status='success'
                AND event_time >= $t30 AND event_time < $snapshot)   AS avg_txn_amount_30d,
            count(*) FILTER (WHERE status='failed'
                AND event_time >= $t30 AND event_time < $snapshot)
            / greatest(count(*) FILTER (WHERE
                event_time >= $t30 AND event_time < $snapshot), 1)   AS failed_txn_rate_30d,
            max(event_time) FILTER (WHERE status='success'
                AND event_time < $snapshot)                         AS last_success_txn
        FROM raw_transactions
        GROUP BY user_id
    ) t ON t.user_id = u.user_id
    LEFT JOIN (
        SELECT
            user_id,
            count(*) FILTER (WHERE resolved = 0
                AND event_time < $snapshot)                         AS open_tickets,
            count(*) FILTER (WHERE event_time >= $t30
                AND event_time < $snapshot)                         AS ticket_rate_30d
        FROM raw_support_tickets
        GROUP BY user_id
    ) sk ON sk.user_id = u.user_id
    WHERE 1=1 {entity_filter}
    """


def _windows(snapshot_time: datetime, version: str) -> dict:
    return {
        "version": version,
        "snapshot": snapshot_time,
        "t7": snapshot_time - timedelta(days=7),
        "t30": snapshot_time - timedelta(days=30),
        "t90": snapshot_time - timedelta(days=90),
    }


def compute_and_store(client, snapshot_time: datetime, feature_version: str = "v1") -> int:
    params = _windows(snapshot_time, feature_version)
    insert_cols = "(entity_id, entity_type, feature_version, event_time, " + \
        ", ".join(FEATURE_COLS) + ")"
    client.execute(
        f"INSERT INTO feature_history {insert_cols} " + feature_select_sql(),
        params,
    )
    rows = client.execute(
        "SELECT count(*) FROM feature_history "
        "WHERE event_time = $snapshot AND feature_version = $version",
        {"snapshot": snapshot_time, "version": feature_version},
    )
    count = int(rows[0][0]) if rows else 0
    log.info("features_computed", rows=count, snapshot=snapshot_time.isoformat())
    return count


def compute_on_demand(client, entity_id: int, feature_version: str = "v1") -> dict | None:
    params = _windows(datetime.utcnow(), feature_version)
    params["uid"] = entity_id
    rows = client.execute(
        feature_select_sql(entity_filter="AND u.user_id = $uid"), params
    )
    if not rows:
        return None
    # rows[0] = (entity_id, entity_type, version, event_time, *FEATURE_COLS)
    values = rows[0][4:]
    raw = dict(zip(FEATURE_COLS, (float(v) for v in values)))
    # validate_single_entity() internally seeds its validation frame with a
    # placeholder entity_id of 0, which fails FEATURE_SCHEMA's Check.ge(1).
    # Passing the real entity_id here overrides that placeholder (dict-literal
    # unpacking lets a later key win) without needing to touch validator.py.
    raw["entity_id"] = entity_id
    return validate_single_entity(raw)
