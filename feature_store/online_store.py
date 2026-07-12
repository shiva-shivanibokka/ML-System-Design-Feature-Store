"""
feature_store/online_store.py
=============================
Online feature store backed by Redis.

Storage pattern — hash-per-entity:
    Key:   entity:user:{user_id}
    Value: Redis hash of {feature_name: value, ...}

This pattern is identical to what Feast uses internally with Redis.
HGETALL retrieves all features for an entity in a single round-trip (<2ms).

Two serving paths:
  batch_get()       — HGETALL from Redis (pre-materialized, <2ms)
  batch_get_multi() — pipeline of HGETALL for multiple entities

The on-demand fallback (ClickHouse query) lives in serving/feature_server.py.
"""

from __future__ import annotations

import time
from typing import Optional

import structlog

from feature_store.connections import get_redis_client
from feature_store.features import FEATURE_COLS

log = structlog.get_logger()

ENTITY_KEY_PREFIX = "entity:user"
TTL_SECONDS = 48 * 3600  # 48 hours


def _entity_key(entity_id: int) -> str:
    return f"{ENTITY_KEY_PREFIX}:{entity_id}"


# ---------------------------------------------------------------------------
# Write path — called by materialization
# ---------------------------------------------------------------------------


def write_entity(entity_id: int, features: dict[str, float]) -> None:
    """Write a single entity's features to Redis as a hash."""
    r = get_redis_client()
    key = _entity_key(entity_id)
    # Store all values as strings (Redis hashes are string-typed)
    r.hset(key, mapping={k: str(v) for k, v in features.items()})
    r.expire(key, TTL_SECONDS)


def write_entities_pipeline(entity_features: list[tuple[int, dict[str, float]]]) -> int:
    """
    Batch write using Redis pipeline — dramatically faster than individual writes.
    Called by materialization with chunks of 500 entities.
    Returns count of entities written.
    """
    r = get_redis_client()
    pipe = r.pipeline(transaction=False)

    for entity_id, features in entity_features:
        key = _entity_key(entity_id)
        pipe.hset(key, mapping={k: str(v) for k, v in features.items()})
        pipe.expire(key, TTL_SECONDS)

    pipe.execute()
    return len(entity_features)


# ---------------------------------------------------------------------------
# Read path — called by the FastAPI feature server
# ---------------------------------------------------------------------------


def get_entity(entity_id: int) -> dict[str, float] | None:
    """
    Batch path: retrieve features for a single entity from Redis.
    Returns None if the entity is not in the online store (cache miss).
    Latency target: <2ms.
    """
    r = get_redis_client()
    t0 = time.perf_counter()
    raw = r.hgetall(_entity_key(entity_id))
    latency_ms = (time.perf_counter() - t0) * 1000

    if not raw:
        log.debug("online_store_miss", entity_id=entity_id)
        return None

    features = {k: float(v) for k, v in raw.items()}
    log.debug("online_store_hit", entity_id=entity_id, latency_ms=round(latency_ms, 2))
    return features


def get_entities_batch(entity_ids: list[int]) -> dict[int, dict[str, float] | None]:
    """
    Retrieve features for multiple entities using a Redis pipeline.
    Returns a dict mapping entity_id → features (or None for misses).
    """
    r = get_redis_client()
    t0 = time.perf_counter()

    pipe = r.pipeline(transaction=False)
    for eid in entity_ids:
        pipe.hgetall(_entity_key(eid))
    results = pipe.execute()

    latency_ms = (time.perf_counter() - t0) * 1000
    hits = sum(1 for r in results if r)
    log.info(
        "online_store_batch",
        requested=len(entity_ids),
        hits=hits,
        misses=len(entity_ids) - hits,
        latency_ms=round(latency_ms, 2),
    )

    output = {}
    for eid, raw in zip(entity_ids, results):
        output[eid] = {k: float(v) for k, v in raw.items()} if raw else None
    return output


def get_online_store_size() -> int:
    """Return number of entity keys in the online store (for monitoring)."""
    r = get_redis_client()
    # SCAN is O(N) but non-blocking; acceptable for monitoring
    count = 0
    for _ in r.scan_iter(match=f"{ENTITY_KEY_PREFIX}:*", count=1000):
        count += 1
    return count


def delete_entity(entity_id: int) -> bool:
    """Remove an entity from the online store."""
    r = get_redis_client()
    return bool(r.delete(_entity_key(entity_id)))
