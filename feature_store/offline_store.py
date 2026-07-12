"""
feature_store/offline_store.py
==============================
Offline feature store on DuckDB / MotherDuck.

Point-in-time correctness uses DuckDB's ASOF JOIN: for each (entity, label_time)
it matches the most recent feature row with event_time <= label_time. This is
the temporal join feature stores (Feast, Tecton, Hopsworks) are built around.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import structlog

from feature_store.connections import get_duckdb_client
from feature_store.features import FEATURE_COLS, compute_and_store

log = structlog.get_logger()

__all__ = [
    "FEATURE_COLS", "compute_features", "get_latest_features_for_entities",
    "get_training_dataset", "get_feature_stats",
]


def compute_features(client=None, snapshot_time: datetime | None = None,
                      feature_version: str = "v1") -> int:
    client = client or get_duckdb_client()
    return compute_and_store(client, snapshot_time or datetime.utcnow(), feature_version)


def get_latest_features_for_entities(entity_ids: list[int], feature_version: str = "v1",
                                      as_of: datetime | None = None) -> pd.DataFrame:
    client = get_duckdb_client()
    cutoff = as_of or datetime.utcnow()
    rows = client.execute(
        f"""
        SELECT entity_id, {", ".join(FEATURE_COLS)}
        FROM feature_history
        WHERE entity_id IN (SELECT UNNEST($ids))
          AND feature_version = $version
          AND event_time <= $cutoff
        QUALIFY row_number() OVER (PARTITION BY entity_id ORDER BY event_time DESC) = 1
        """,
        {"ids": entity_ids, "version": feature_version, "cutoff": cutoff},
    )
    return pd.DataFrame(rows, columns=["entity_id"] + FEATURE_COLS)


def get_training_dataset(label_timestamps: list[tuple[int, datetime]],
                          feature_version: str = "v1") -> pd.DataFrame:
    if not label_timestamps:
        return pd.DataFrame()
    client = get_duckdb_client()
    labels = pd.DataFrame(label_timestamps, columns=["entity_id", "label_time"])
    client.register("labels", labels)
    # feature_version is pre-filtered in a CTE, NOT in a trailing WHERE on the
    # joined result: the ASOF JOIN below is a LEFT JOIN, so a WHERE on fh.* would
    # silently drop the unmatched (no-prior-snapshot) label rows it's meant to keep.
    rows = client.execute(
        f"""
        WITH fh AS (
            SELECT * FROM feature_history WHERE feature_version = $version
        )
        SELECT l.entity_id, l.label_time, {", ".join(f"fh.{c}" for c in FEATURE_COLS)}
        FROM labels l
        ASOF LEFT JOIN fh
          ON l.entity_id = fh.entity_id
         AND l.label_time >= fh.event_time
        """,
        {"version": feature_version},
    )
    return pd.DataFrame(rows, columns=["entity_id", "label_timestamp"] + FEATURE_COLS)


def get_feature_stats(feature_version: str = "v1", since_days: int = 7) -> dict[str, dict]:
    client = get_duckdb_client()
    since = datetime.utcnow() - timedelta(days=since_days)
    stats: dict[str, dict] = {}
    for col in FEATURE_COLS:
        rows = client.execute(
            f"""
            SELECT avg({col}), stddev_pop({col}),
                   quantile_cont({col}, 0.25), quantile_cont({col}, 0.50),
                   quantile_cont({col}, 0.75), quantile_cont({col}, 0.95),
                   (count(*) FILTER (WHERE {col} IS NULL OR isnan({col}))) / greatest(count(*), 1),
                   count(*)
            FROM feature_history
            WHERE feature_version = $version AND event_time >= $since
            """,
            {"version": feature_version, "since": since},
        )
        if rows and rows[0][0] is not None:
            m, s, p25, p50, p75, p95, nr, n = rows[0]
            stats[col] = {"mean": float(m or 0), "std": float(s or 0),
                          "p25": float(p25 or 0), "p50": float(p50 or 0),
                          "p75": float(p75 or 0), "p95": float(p95 or 0),
                          "null_rate": float(nr or 0), "sample_count": int(n or 0)}
    return stats
