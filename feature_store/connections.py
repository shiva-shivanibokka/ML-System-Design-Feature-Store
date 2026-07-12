"""
feature_store/connections.py
============================
Connection factories for the offline store (DuckDB / MotherDuck) and the
online store (Redis / Aiven Valkey). Both are env-configured with local
fallbacks so the project runs with zero cloud accounts for local development.

Offline store:
  - If MOTHERDUCK_TOKEN is set, connect to MotherDuck (`md:<db>`).
  - Otherwise open a local DuckDB file (DUCKDB_PATH).
DuckDB connections are not safe for concurrent access from multiple threads, so
_DuckClient serializes every execute()/register() call on the one singleton
connection behind a lock (register() must bind DataFrames to that exact
connection object, which rules out a fresh cursor per call).

Online store:
  - redis.from_url(REDIS_URL). Aiven Valkey (or any Redis-compatible service)
    provides an rediss:// URL; local dev defaults to redis://localhost:6379.
"""

from __future__ import annotations

import os
import threading
from functools import lru_cache

import duckdb
import redis as redis_lib
import structlog

log = structlog.get_logger()


class _DuckClient:
    """Thin wrapper giving DuckDB a clickhouse-driver-like .execute() surface."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        # ponytail: one global lock over the single connection. register() binds
        # a DataFrame to the exact connection object, so we cannot use a fresh
        # .cursor() per call (the registration would be invisible). A single
        # DuckDB connection is not safe for concurrent thread access, so serialize
        # every execute/register. Move to a connection pool only if serving
        # throughput ever outgrows one connection (not a concern for this demo).
        self._lock = threading.Lock()

    def execute(self, sql: str, params: dict | list | None = None) -> list[tuple]:
        with self._lock:
            self._conn.execute(sql, params if params is not None else {})
            try:
                return self._conn.fetchall()
            except duckdb.InvalidInputException:
                return []  # statements without a result set (INSERT/DDL)

    def register(self, name: str, df) -> None:
        with self._lock:
            self._conn.register(name, df)

    @property
    def raw(self) -> duckdb.DuckDBPyConnection:
        return self._conn


@lru_cache(maxsize=1)
def get_duckdb_client() -> _DuckClient:
    token = os.getenv("MOTHERDUCK_TOKEN", "").strip()
    db = os.getenv("DUCKDB_DATABASE", "feature_store")
    if token:
        log.info("connecting_motherduck", database=db)
        conn = duckdb.connect(f"md:{db}", config={"motherduck_token": token})
    else:
        path = os.getenv("DUCKDB_PATH", "feature_store.duckdb")
        log.info("connecting_duckdb_local", path=path)
        conn = duckdb.connect(path)
    return _DuckClient(conn)


@lru_cache(maxsize=1)
def get_redis_client() -> redis_lib.Redis:
    url = os.getenv("REDIS_URL", "redis://localhost:6379")
    log.info("connecting_redis", url=url.split("@")[-1])  # never log credentials
    return redis_lib.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
