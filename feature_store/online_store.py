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

The on-demand fallback (DuckDB query) lives in feature_store/features.py::compute_on_demand.

Reads degrade, writes do not
----------------------------
The read functions treat an unreachable Redis as a total cache miss and return
the same "nothing here" value they return for a cold entity. Serving already
knows how to handle a miss — it falls through to the DuckDB on-demand path — so
an online-store outage costs latency instead of taking the feature server down.

That distinction was not free. When the Aiven Valkey instance behind this demo
was deleted, `hgetall` raised ConnectionError rather than returning empty, the
documented Redis-then-DuckDB fallback never ran because a connection failure is
not a miss, and every serving request returned 500 while a complete offline
store sat there able to answer all of them.

The write path deliberately does NOT swallow anything. A materialization run
that reports success while storing nothing is worse than one that fails: the
online store silently empties and the first symptom is a cache hit rate quietly
falling to zero.
"""

from __future__ import annotations

import os
import time

import structlog
from redis.exceptions import RedisError

from feature_store.connections import get_redis_client

log = structlog.get_logger()

# This instance is shared with another project, so the namespace matters.
#
# The Upstash free plan allows a single database, and the recommendation engine
# already owns one. Sharing is safe because the two keyspaces are disjoint —
# that project writes `rec:{user_id}:{top_n}` and only ever scans `rec:*`, and
# neither project issues FLUSHDB/FLUSHALL or an unscoped KEYS. Overridable so
# that stays a deliberate property rather than a coincidence: point a second
# deployment at the same instance by giving it a different prefix.
ENTITY_KEY_PREFIX = os.getenv("ONLINE_STORE_PREFIX", "entity:user")
TTL_SECONDS = 48 * 3600  # 48 hours

# Maintained index of entity ids currently written, so /metrics can SCARD
# instead of SCAN-ing the whole keyspace on every unauthenticated call.
# Derived from the prefix so the index cannot outlive a namespace change.
ENTITY_INDEX_KEY = f"{ENTITY_KEY_PREFIX}:index"


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
    r.sadd(ENTITY_INDEX_KEY, entity_id)


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
        pipe.sadd(ENTITY_INDEX_KEY, entity_id)

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
    t0 = time.perf_counter()
    try:
        raw = get_redis_client().hgetall(_entity_key(entity_id))
    except RedisError as exc:
        # Same return value as a miss, so the caller falls through to the
        # on-demand DuckDB path instead of surfacing a 500.
        log.warning("online_store_unavailable", entity_id=entity_id, error=str(exc))
        return None
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
    t0 = time.perf_counter()

    try:
        r = get_redis_client()
        pipe = r.pipeline(transaction=False)
        for eid in entity_ids:
            pipe.hgetall(_entity_key(eid))
        results = pipe.execute()
    except RedisError as exc:
        # Report every entity as a miss so the batch endpoint falls through to
        # on-demand computation rather than failing the whole request.
        log.warning("online_store_unavailable", requested=len(entity_ids), error=str(exc))
        return {eid: None for eid in entity_ids}

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
    """Return number of entity keys in the online store (for monitoring).

    SCARD on the maintained index instead of a full keyspace SCAN — O(1)
    against the hosted Redis instance instead of O(N) per unauthenticated
    /metrics call.
    """
    # ponytail: index entries aren't pruned when an entity hash's own TTL
    # expires (only delete_entity() SREMs). Size can drift high vs actual
    # live keys over a long-running instance; add periodic reconciliation
    # (SCAN diff) if that drift ever matters for this demo.
    try:
        return int(get_redis_client().scard(ENTITY_INDEX_KEY))
    except RedisError as exc:
        # /metrics is a dashboard panel, not a health check — /health is what
        # reports the online store as down. Returning 0 keeps the rest of the
        # metrics readable instead of failing the whole endpoint.
        log.warning("online_store_unavailable", op="scard", error=str(exc))
        return 0


def delete_entity(entity_id: int) -> bool:
    """Remove an entity from the online store."""
    r = get_redis_client()
    deleted = bool(r.delete(_entity_key(entity_id)))
    r.srem(ENTITY_INDEX_KEY, entity_id)
    return deleted
