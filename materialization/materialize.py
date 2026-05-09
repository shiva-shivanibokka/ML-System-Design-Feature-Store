"""
materialization/materialize.py
===============================
Online store materialization — syncs the latest feature snapshot from
ClickHouse (offline store) to Redis (online store).

This is the bridge between the offline and online stores. After backfill
or each new compute_features() call, this script pushes the most recent
feature values to Redis so the serving path can return them in <2ms.

Run modes:
  1. One-shot:   python materialization/materialize.py --once
  2. Scheduled:  python materialization/materialize.py --schedule
     (runs every 6 hours using APScheduler — no Airflow needed)

Usage:
    python materialization/materialize.py --once
    python materialization/materialize.py --schedule --interval-hours 6
"""

import argparse
import os
import sys
import uuid
from datetime import datetime

import structlog

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feature_store.connections import get_clickhouse_client, get_redis_client
from feature_store.offline_store import FEATURE_COLS, get_latest_features_for_entities
from feature_store.online_store import write_entities_pipeline
from feature_store.validator import validate_feature_batch

log = structlog.get_logger()

BATCH_SIZE = 500


def _get_all_entity_ids(client) -> list[int]:
    rows = client.execute("SELECT DISTINCT user_id FROM raw_users ORDER BY user_id")
    return [r[0] for r in rows]


def run_materialization(feature_version: str = "v1") -> dict:
    """
    Pull latest features for all entities from ClickHouse offline store,
    validate with Pandera, and write to Redis online store.

    Returns a summary dict for logging and audit trail.
    """
    client = get_clickhouse_client()
    run_id = str(uuid.uuid4())[:12]
    started_at = datetime.utcnow()

    log.info("materialization_starting", run_id=run_id, version=feature_version)

    # ── 1. Get all entity IDs ─────────────────────────────────────────────────
    entity_ids = _get_all_entity_ids(client)
    total = len(entity_ids)
    log.info("entities_found", count=total)

    processed = 0
    failed = 0
    validation_failures = 0

    # ── 2. Process in batches ─────────────────────────────────────────────────
    for start in range(0, total, BATCH_SIZE):
        batch_ids = entity_ids[start : start + BATCH_SIZE]

        try:
            df = get_latest_features_for_entities(
                batch_ids, feature_version=feature_version
            )

            if df.empty:
                log.warning("batch_empty", start=start, batch_size=len(batch_ids))
                continue

            # Validate features before writing to online store
            try:
                df = validate_feature_batch(df)
            except Exception as val_exc:
                validation_failures += len(batch_ids)
                log.error("batch_validation_failed", start=start, error=str(val_exc))
                continue

            # Prepare (entity_id, features_dict) pairs for Redis pipeline write
            entity_feature_pairs = []
            for _, row in df.iterrows():
                eid = int(row["entity_id"])
                features = {col: float(row[col]) for col in FEATURE_COLS if col in row}
                entity_feature_pairs.append((eid, features))

            written = write_entities_pipeline(entity_feature_pairs)
            processed += written

            log.info(
                "batch_materialized",
                start=start,
                written=written,
                progress=f"{min(start + BATCH_SIZE, total)}/{total}",
            )

        except Exception as exc:
            failed += len(batch_ids)
            log.error("batch_failed", start=start, error=str(exc))

    duration_ms = int((datetime.utcnow() - started_at).total_seconds() * 1000)
    status = "success" if failed == 0 else ("partial" if processed > 0 else "failed")

    # ── 3. Write audit log to ClickHouse ─────────────────────────────────────
    client.execute(
        """
        INSERT INTO materialization_log
        (run_id, feature_version, entity_type, entities_processed,
         entities_failed, duration_ms, status, error_message,
         started_at, completed_at)
        VALUES
        """,
        [
            (
                run_id,
                feature_version,
                "user",
                processed,
                failed + validation_failures,
                duration_ms,
                status,
                None,
                started_at,
                datetime.utcnow(),
            )
        ],
    )

    summary = {
        "run_id": run_id,
        "status": status,
        "total": total,
        "processed": processed,
        "failed": failed,
        "validation_failures": validation_failures,
        "duration_ms": duration_ms,
    }
    log.info("materialization_complete", **summary)
    return summary


def run_scheduled(interval_hours: int = 6, feature_version: str = "v1") -> None:
    """
    Run materialization on a fixed schedule using APScheduler.
    No external scheduler (Airflow, cron) required — embedded in the process.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BlockingScheduler()
    scheduler.add_job(
        func=lambda: run_materialization(feature_version=feature_version),
        trigger=IntervalTrigger(hours=interval_hours),
        id="materialize",
        name="Online store materialization",
        replace_existing=True,
    )

    log.info("scheduler_starting", interval_hours=interval_hours)
    # Run immediately on startup, then on schedule
    run_materialization(feature_version=feature_version)
    scheduler.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize features to online store")
    parser.add_argument("--once", action="store_true", default=False)
    parser.add_argument("--schedule", action="store_true", default=False)
    parser.add_argument("--interval-hours", type=int, default=6)
    parser.add_argument("--feature-version", default="v1")
    args = parser.parse_args()

    if args.schedule:
        run_scheduled(
            interval_hours=args.interval_hours, feature_version=args.feature_version
        )
    else:
        run_materialization(feature_version=args.feature_version)


if __name__ == "__main__":
    import structlog

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    main()
