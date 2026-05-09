"""
feature_store/connections.py
============================
Shared connection factories for ClickHouse and Redis.
Both return module-level singletons — safe for use inside FastAPI lifespan.
"""

import os
from functools import lru_cache

import clickhouse_driver
import redis as redis_lib
import structlog

log = structlog.get_logger()


@lru_cache(maxsize=1)
def get_clickhouse_client() -> clickhouse_driver.Client:
    host = os.getenv("CLICKHOUSE_HOST", "localhost")
    port = int(os.getenv("CLICKHOUSE_PORT", "9000"))
    db = os.getenv("CLICKHOUSE_DB", "feature_store")
    user = os.getenv("CLICKHOUSE_USER", "fs_user")
    password = os.getenv("CLICKHOUSE_PASSWORD", "fs_pass")

    log.info("connecting_clickhouse", host=host, port=port, db=db)
    client = clickhouse_driver.Client(
        host=host,
        port=port,
        database=db,
        user=user,
        password=password,
        connect_timeout=10,
        send_receive_timeout=300,
    )
    return client


@lru_cache(maxsize=1)
def get_redis_client() -> redis_lib.Redis:
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    log.info("connecting_redis", host=host, port=port)
    return redis_lib.Redis(
        host=host,
        port=port,
        db=0,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=2,
    )
