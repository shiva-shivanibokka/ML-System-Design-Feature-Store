"""
feature_store/connections.py
============================
Connection factories for the offline store (DuckDB / MotherDuck) and the
online store (Redis / Upstash). Both are env-configured with local fallbacks
so the project runs with zero cloud accounts for local development.

Offline store:
  - If MOTHERDUCK_TOKEN is set, connect to MotherDuck (`md:<db>`).
  - Otherwise open a local DuckDB file (DUCKDB_PATH).
DuckDB connections are not safe to share across threads; every _DuckClient.execute
runs on a fresh cursor of the singleton connection, which IS thread-safe.

Online store:
  - redis.from_url(REDIS_URL). Upstash provides an rediss:// URL; local dev
    defaults to redis://localhost:6379.
"""
from __future__ import annotations

import os
from functools import lru_cache

import duckdb
import redis as redis_lib
import structlog

log = structlog.get_logger()


class _DuckClient:
    """Thin wrapper giving DuckDB a clickhouse-driver-like .execute() surface."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: dict | list | None = None) -> list[tuple]:
        # Run directly on the singleton connection (not a fresh .cursor() per
        # call): DuckDB's register() binds a DataFrame to the specific
        # connection/cursor object it was called on, so a new cursor per
        # execute() can never see anything registered via .register() below.
        # DuckDB serializes concurrent access to a single connection itself,
        # so this is still safe under FastAPI's single-process usage.
        self._conn.execute(sql, params if params is not None else {})
        try:
            return self._conn.fetchall()
        except duckdb.InvalidInputException:
            return []  # statements without a result set (INSERT/DDL)

    def register(self, name: str, df) -> None:
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
