"""
materialization/backfill.py
===========================
Backfill script — computes and stores historical feature snapshots.

Without backfill, the feature_history table only contains snapshots from when
the feature store went live. Point-in-time joins for training data require
feature history to exist for every label timestamp in the training set.

This script walks backwards in time (from today to N days ago) and computes
feature snapshots at each interval. This is what makes PIT joins work.

Usage:
    python materialization/backfill.py --days 90 --interval-hours 24
    python materialization/backfill.py --days 30 --interval-hours 6
"""

import argparse
import os
import sys
import uuid
from datetime import datetime, timedelta

import structlog

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feature_store.connections import get_duckdb_client
from feature_store.offline_store import compute_features
from feature_store.schema import apply_schema

log = structlog.get_logger()


def run_backfill(
    days: int = 90,
    interval_hours: int = 24,
    feature_version: str = "v1",
    dry_run: bool = False,
) -> None:
    """
    Compute features at every `interval_hours` interval going back `days` days.

    For a 90-day backfill at 24-hour intervals:
        - 90 snapshots total
        - Each snapshot runs one DuckDB INSERT ... SELECT (bulk operation)
        - Total runtime: ~3-5 minutes on 10K users
    """
    client = get_duckdb_client()
    apply_schema(client)
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

    # Build list of snapshot timestamps (most recent first)
    snapshots: list[datetime] = []
    t = now
    while t >= now - timedelta(days=days):
        snapshots.append(t)
        t -= timedelta(hours=interval_hours)

    total = len(snapshots)
    log.info(
        "backfill_starting",
        snapshots=total,
        from_date=(now - timedelta(days=days)).isoformat(),
        to_date=now.isoformat(),
        interval_hours=interval_hours,
        dry_run=dry_run,
    )

    if dry_run:
        log.info("dry_run_complete", would_run=total)
        return

    # Check which snapshots already exist to support resumable backfill
    existing = set()
    rows = client.execute(
        """
        SELECT DISTINCT event_time
        FROM feature_history
        WHERE feature_version = $version
        """,
        {"version": feature_version},
    )
    for (ts,) in rows:
        existing.add(ts)
    log.info("existing_snapshots_found", count=len(existing))

    succeeded = 0
    failed = 0

    for i, snapshot_time in enumerate(snapshots, 1):
        # Skip if snapshot already computed (resumable)
        if snapshot_time in existing:
            log.debug(
                "snapshot_skipped_exists", snapshot_time=snapshot_time.isoformat()
            )
            continue

        run_id = str(uuid.uuid4())[:8]
        started_at = datetime.utcnow()

        try:
            n_rows = compute_features(
                client,
                snapshot_time=snapshot_time,
                feature_version=feature_version,
            )

            duration_ms = int((datetime.utcnow() - started_at).total_seconds() * 1000)

            # Log to materialization_log
            client.execute(
                """
                INSERT INTO materialization_log
                (run_id, feature_version, entity_type, entities_processed,
                 entities_failed, duration_ms, status, error_message,
                 started_at, completed_at)
                VALUES
                ($run_id, $v, 'user', $proc, $failed, $dur, $status, $err, $started, $completed)
                """,
                {
                    "run_id": run_id,
                    "v": feature_version,
                    "proc": n_rows,
                    "failed": 0,
                    "dur": duration_ms,
                    "status": "success",
                    "err": None,
                    "started": started_at,
                    "completed": datetime.utcnow(),
                },
            )

            succeeded += 1
            log.info(
                "snapshot_complete",
                progress=f"{i}/{total}",
                snapshot_time=snapshot_time.isoformat(),
                rows=n_rows,
                duration_ms=duration_ms,
            )

        except Exception as exc:
            failed += 1
            duration_ms = int((datetime.utcnow() - started_at).total_seconds() * 1000)

            client.execute(
                """
                INSERT INTO materialization_log
                (run_id, feature_version, entity_type, entities_processed,
                 entities_failed, duration_ms, status, error_message,
                 started_at, completed_at)
                VALUES
                ($run_id, $v, 'user', $proc, $failed, $dur, $status, $err, $started, $completed)
                """,
                {
                    "run_id": run_id,
                    "v": feature_version,
                    "proc": 0,
                    "failed": 1,
                    "dur": duration_ms,
                    "status": "failed",
                    "err": str(exc)[:500],
                    "started": started_at,
                    "completed": datetime.utcnow(),
                },
            )

            log.error(
                "snapshot_failed",
                snapshot_time=snapshot_time.isoformat(),
                error=str(exc),
            )

    log.info(
        "backfill_complete",
        total=total,
        succeeded=succeeded,
        failed=failed,
        skipped=total - succeeded - failed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill feature history")
    parser.add_argument(
        "--days", type=int, default=90, help="Days of history to backfill"
    )
    parser.add_argument(
        "--interval-hours", type=int, default=24, help="Snapshot interval in hours"
    )
    parser.add_argument("--feature-version", default="v1")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print plan without writing"
    )
    args = parser.parse_args()

    run_backfill(
        days=args.days,
        interval_hours=args.interval_hours,
        feature_version=args.feature_version,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    import structlog

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    main()
