"""
serving/main.py
===============
FastAPI feature server — dual-path architecture.

Two serving paths:
  BATCH PATH (pre-materialized):
    Redis HGETALL → returns features in <2ms
    Used for: known entities with history in the online store

  ON-DEMAND PATH (cold-start fallback):
    ClickHouse query → computes features on the fly in ~20ms
    Used for: new entities not yet materialized, or after cache expiry

Every request logs which path was taken + latency, enabling the
training vs serving skew comparison in the Gradio dashboard.

Endpoints:
  GET  /health
  GET  /features/{entity_id}                — single entity, dual-path
  POST /features/batch                      — bulk lookup (up to 500)
  POST /features/training-dataset           — PIT-correct training export
  GET  /skew-report                         — KS test per feature
  GET  /lineage/{feature_name}              — feature provenance DAG
  GET  /registry                            — all registered features
  GET  /materialization-log                 — materialization audit trail
  GET  /metrics                             — serving latency summary
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from feature_store.connections import get_clickhouse_client, get_redis_client
from feature_store.offline_store import FEATURE_COLS, get_feature_stats
from feature_store.online_store import (
    get_entities_batch,
    get_entity,
    get_online_store_size,
)
from feature_store.registry import get_all_features, sync_registry
from feature_store.validator import validate_single_entity
from skew.detector import compute_skew_report
from lineage.graph import get_lineage_for_feature

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Structlog configuration
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)

# ---------------------------------------------------------------------------
# In-memory latency tracker (simple ring buffer for /metrics)
# ---------------------------------------------------------------------------
_latency_log: list[dict] = []
MAX_LATENCY_LOG = 1000


def _record_latency(path: str, latency_ms: float, hit: bool) -> None:
    _latency_log.append(
        {"path": path, "latency_ms": latency_ms, "hit": hit, "ts": time.time()}
    )
    if len(_latency_log) > MAX_LATENCY_LOG:
        _latency_log.pop(0)


# ---------------------------------------------------------------------------
# Lifespan — startup tasks
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("feature_server_starting")
    try:
        sync_registry()
        log.info("registry_synced")
    except Exception as exc:
        log.error("registry_sync_failed", error=str(exc))
    yield
    log.info("feature_server_shutting_down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ML Feature Store — Feature Server",
    description=(
        "Dual-path feature server: Redis online store (<2ms) with "
        "ClickHouse on-demand fallback (~20ms). "
        "Prevents training-serving skew via centralized feature computation."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class FeatureResponse(BaseModel):
    entity_id: int
    features: dict[str, float]
    source: str = Field(
        description="'online_store' (Redis) or 'on_demand' (ClickHouse)"
    )
    latency_ms: float
    feature_version: str


class BatchFeatureRequest(BaseModel):
    entity_ids: list[int] = Field(..., max_length=500)
    feature_version: str = "v1"


class BatchFeatureResponse(BaseModel):
    results: dict[int, dict[str, float] | None]
    hits: int
    misses: int
    on_demand_computed: int
    latency_ms: float


class TrainingDatasetRequest(BaseModel):
    entity_ids: list[int]
    label_timestamp: datetime
    feature_version: str = "v1"


class SkewReport(BaseModel):
    feature_name: str
    training_mean: float
    serving_mean: float
    mean_shift: float
    ks_statistic: float
    ks_pvalue: float
    flagged: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FEATURE_VERSION = os.getenv("FEATURE_VERSION", "v1")


def _on_demand_features(
    entity_id: int, version: str = FEATURE_VERSION
) -> dict[str, float] | None:
    """
    On-demand path: compute features directly from ClickHouse raw tables.
    This is the cold-start fallback for entities not in the online store.
    """
    client = get_clickhouse_client()
    now = datetime.utcnow()

    rows = client.execute(
        f"""
        SELECT
            {", ".join(FEATURE_COLS)}
        FROM
        (
            SELECT
                countIf(t.status = 'success' AND t.event_time >= %(t7)s)   AS txn_count_7d,
                countIf(t.status = 'success' AND t.event_time >= %(t30)s)  AS txn_count_30d,
                countIf(t.status = 'success' AND t.event_time >= %(t90)s)  AS txn_count_90d,
                sumIf(t.amount, t.status='success' AND t.event_time>=%(t7)s)  AS total_spend_7d,
                sumIf(t.amount, t.status='success' AND t.event_time>=%(t30)s) AS total_spend_30d,
                sumIf(t.amount, t.status='success' AND t.event_time>=%(t90)s) AS total_spend_90d,
                avgIf(t.amount, t.status='success' AND t.event_time>=%(t30)s) AS avg_txn_amount_30d,
                countIf(t.status='failed' AND t.event_time>=%(t30)s)
                    / greatest(countIf(t.event_time>=%(t30)s), 1)           AS failed_txn_rate_30d,
                toFloat32(dateDiff('day', maxIf(t.event_time, t.status='success'), now())) AS days_since_last_txn,
                countIf(sk.resolved=0)                                       AS open_tickets,
                countIf(sk.event_time>=%(t30)s)                             AS ticket_rate_30d,
                toFloat32(dateDiff('day', u.signup_date, today()))          AS account_age_days,
                multiIf(u.plan_type='free',0,u.plan_type='basic',1,
                        u.plan_type='pro',2,3)                              AS plan_encoded
            FROM raw_users u
            LEFT JOIN raw_transactions t ON t.user_id = u.user_id
            LEFT JOIN raw_support_tickets sk ON sk.user_id = u.user_id
            WHERE u.user_id = %(uid)s
            GROUP BY u.user_id, u.signup_date, u.plan_type
        )
        """,
        {
            "uid": entity_id,
            "t7": now - timedelta(days=7),
            "t30": now - timedelta(days=30),
            "t90": now - timedelta(days=90),
        },
    )

    if not rows:
        return None

    raw = dict(zip(FEATURE_COLS, rows[0]))
    return validate_single_entity(raw)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/features/{entity_id}", response_model=FeatureResponse)
async def get_features(
    entity_id: int,
    feature_version: str = Query(default="v1"),
):
    """
    Dual-path feature retrieval for a single entity.

    1. Try Redis (batch path) — returns in <2ms if entity is materialized
    2. Fall back to ClickHouse (on-demand path) — ~20ms, for cold entities
    3. Validate features before returning
    """
    t0 = time.perf_counter()
    structlog.contextvars.bind_contextvars(request_id=str(uuid.uuid4())[:8])

    # ── Batch path ─────────────────────────────────────────────────────────
    features = get_entity(entity_id)
    source = "online_store"

    if features is None:
        # ── On-demand path ──────────────────────────────────────────────────
        log.info("on_demand_fallback", entity_id=entity_id)
        features = _on_demand_features(entity_id, version=feature_version)
        source = "on_demand"

        if features is None:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    hit = source == "online_store"
    _record_latency(source, latency_ms, hit)

    log.info(
        "feature_request",
        entity_id=entity_id,
        source=source,
        latency_ms=latency_ms,
    )

    return FeatureResponse(
        entity_id=entity_id,
        features=features,
        source=source,
        latency_ms=latency_ms,
        feature_version=feature_version,
    )


@app.post("/features/batch", response_model=BatchFeatureResponse)
async def get_features_batch(request: BatchFeatureRequest):
    """
    Bulk feature retrieval for up to 500 entities.
    Uses Redis pipeline for batch hits, falls back to ClickHouse for misses.
    """
    t0 = time.perf_counter()

    # ── Batch path: all entities via Redis pipeline ────────────────────────
    online_results = get_entities_batch(request.entity_ids)
    hits = sum(1 for v in online_results.values() if v is not None)
    misses = [eid for eid, v in online_results.items() if v is None]

    # ── On-demand fallback for misses ──────────────────────────────────────
    on_demand_count = 0
    for eid in misses:
        features = _on_demand_features(eid, version=request.feature_version)
        if features is not None:
            online_results[eid] = features
            on_demand_count += 1

    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    _record_latency("batch", latency_ms, hit=hits > 0)

    log.info(
        "batch_feature_request",
        requested=len(request.entity_ids),
        hits=hits,
        misses=len(misses),
        on_demand=on_demand_count,
        latency_ms=latency_ms,
    )

    return BatchFeatureResponse(
        results=online_results,
        hits=hits,
        misses=len(misses),
        on_demand_computed=on_demand_count,
        latency_ms=latency_ms,
    )


@app.get("/skew-report")
async def skew_report(feature_version: str = Query(default="v1")):
    """
    Compute training vs serving feature distribution skew.
    Uses KS test per feature to detect statistical drift.
    """
    try:
        report = compute_skew_report(feature_version=feature_version)
        return {"feature_version": feature_version, "report": report}
    except Exception as exc:
        log.error("skew_report_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/lineage/{feature_name}")
async def feature_lineage(
    feature_name: str, feature_version: str = Query(default="v1")
):
    """
    Return the lineage DAG for a given feature — upstream sources and
    downstream model consumers.
    """
    try:
        graph = get_lineage_for_feature(feature_name, feature_version=feature_version)
        return graph
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/registry")
async def registry(feature_version: str = Query(default="v1")):
    """List all active features from the registry."""
    return get_all_features(version=feature_version)


@app.get("/materialization-log")
async def materialization_log(limit: int = Query(default=50)):
    """Return the most recent materialization run records."""
    client = get_clickhouse_client()
    rows = client.execute(
        """
        SELECT run_id, feature_version, entity_type, entities_processed,
               entities_failed, duration_ms, status, error_message,
               started_at, completed_at
        FROM materialization_log
        ORDER BY completed_at DESC
        LIMIT %(limit)s
        """,
        {"limit": limit},
    )
    columns = [
        "run_id",
        "feature_version",
        "entity_type",
        "entities_processed",
        "entities_failed",
        "duration_ms",
        "status",
        "error_message",
        "started_at",
        "completed_at",
    ]
    return [dict(zip(columns, row)) for row in rows]


@app.get("/metrics")
async def serving_metrics():
    """Return p50/p95/p99 latency breakdown by serving path."""
    import statistics

    def percentiles(values: list[float]) -> dict:
        if not values:
            return {"p50": 0, "p95": 0, "p99": 0, "count": 0}
        s = sorted(values)
        n = len(s)
        return {
            "p50": round(s[int(n * 0.50)], 2),
            "p95": round(s[int(n * 0.95)], 2),
            "p99": round(s[min(int(n * 0.99), n - 1)], 2),
            "count": n,
        }

    online = [r["latency_ms"] for r in _latency_log if r["path"] == "online_store"]
    on_demand = [r["latency_ms"] for r in _latency_log if r["path"] == "on_demand"]
    batch = [r["latency_ms"] for r in _latency_log if r["path"] == "batch"]

    hit_rate = sum(1 for r in _latency_log if r["hit"]) / max(len(_latency_log), 1)

    return {
        "online_store": percentiles(online),
        "on_demand": percentiles(on_demand),
        "batch": percentiles(batch),
        "cache_hit_rate": round(hit_rate, 3),
        "online_store_entities": get_online_store_size(),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "serving.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
