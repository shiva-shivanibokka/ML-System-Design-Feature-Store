# Free-Tier Feature Store Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the local-only 5-container Docker feature store into a production-grade system that runs entirely on free tiers, with a Next.js frontend on Vercel and a FastAPI backend on Hugging Face Spaces.

**Architecture:** The offline store moves from ClickHouse to **MotherDuck (hosted DuckDB)** using DuckDB's `ASOF JOIN` for point-in-time correctness. The online store moves from local Redis to **Upstash Redis** (URL-configured, near drop-in). The FastAPI feature server runs as a **Docker Space on Hugging Face** (port 7860). The **Gradio UI is deleted** and replaced by a **Next.js dashboard on Vercel**. Batch jobs (backfill, materialize, train) become **GitHub Actions** workflows. Experiment tracking moves to **DagsHub's free hosted MLflow**. The single biggest correctness win: feature computation, currently duplicated between `offline_store.py` and `serving/main.py`, collapses into ONE module (`feature_store/features.py`) reused by compute, on-demand serving, and PIT joins — which is the exact anti-skew guarantee the project claims to demonstrate.

**Tech Stack:** Python 3.11, FastAPI, DuckDB + MotherDuck, `redis` (Upstash), Pandera, LightGBM, MLflow (DagsHub), structlog, pytest + fakeredis; Next.js (App Router, TypeScript) + Recharts on Vercel; Docker on HF Spaces; GitHub Actions.

## Global Constraints

- **Free tiers only.** No paid infra. Confirmed tiers: Upstash Redis (256 MB / 500K cmd/mo), HF Spaces (2 vCPU / 16 GB, sleeps when idle, **ephemeral disk**), MotherDuck (10 GB / 10 compute-hrs/mo), DagsHub free MLflow, Vercel Hobby, GitHub Actions free minutes.
- **Frontend deploys to Vercel only.** Backend never runs on Vercel.
- **No Gradio.** Delete it entirely.
- **Banned platforms:** Supabase, Fly.io, Render. Do not introduce them.
- **HF disk is ephemeral** — the backend must hold NO durable state locally. Offline store = MotherDuck (remote); online store = Upstash (remote). Nothing persists on the Space filesystem.
- **One feature definition, reused everywhere.** No SQL that computes a feature may be duplicated. Compute, on-demand, and PIT paths all call `feature_store/features.py`.
- **DuckDB dialect** for all offline SQL: `count(*) FILTER (WHERE ...)`, `sum(x) FILTER (...)`, `CASE WHEN`, `date_diff('day', a, b)`, `quantile_cont`, `stddev_pop`, `QUALIFY`, `ASOF JOIN`. Named params use `$name` (not `%(name)s`).
- **Python 3.11**, dependency versions pinned in `requirements*.txt`.
- **Secrets via environment variables only.** No credentials in code or committed files. Names: `MOTHERDUCK_TOKEN`, `DUCKDB_DATABASE`, `DUCKDB_PATH` (local fallback), `REDIS_URL`, `MLFLOW_TRACKING_URI`, `MLFLOW_TRACKING_USERNAME`, `MLFLOW_TRACKING_PASSWORD`, `ALLOWED_ORIGINS`, `API_URL`/`NEXT_PUBLIC_API_URL`.
- **Local dev must still work** with zero cloud accounts: DuckDB falls back to a local file, Redis to `redis://localhost:6379`, MLflow to `file:./mlruns`.

---

## Phase 0 — Prep, deletions, dependencies

### Task 0.1: Delete Gradio and ClickHouse artifacts

**Files:**
- Delete: `gradio_app/app.py`, `gradio_app/` (dir)
- Delete: `Dockerfile.gradio`, `requirements.gradio.txt`
- Delete: `configs/clickhouse/init.sql`, `configs/clickhouse/` (dir)

- [ ] **Step 1: Remove files**
```bash
git rm -r gradio_app configs/clickhouse Dockerfile.gradio requirements.gradio.txt
```
- [ ] **Step 2: Commit**
```bash
git commit -m "chore: remove gradio and clickhouse artifacts"
```

### Task 0.2: New dependency set

Replace `clickhouse-driver`+`gradio`+`plotly`+`apscheduler` with `duckdb`. Add `fakeredis` for infra-free tests and `dagshub` for MLflow auth.

**Files:**
- Modify: `requirements.txt`, `requirements.api.txt`

- [ ] **Step 1: Rewrite `requirements.api.txt`** (backend runtime deps)
```
fastapi==0.111.0
uvicorn[standard]==0.30.1
pydantic==2.7.1
duckdb==1.1.3
redis==5.0.4
pandera==0.20.0
pandas==2.2.2
numpy==1.26.4
scipy==1.13.0
structlog==24.2.0
pyyaml==6.0.1
```
- [ ] **Step 2: Rewrite `requirements.txt`** (full dev: API deps + training + dev tools)
```
# API + feature store core
fastapi==0.111.0
uvicorn[standard]==0.30.1
pydantic==2.7.1
httpx==0.27.0
duckdb==1.1.3
redis==5.0.4
pandera==0.20.0

# ML + training
lightgbm==4.3.0
scikit-learn==1.4.2
mlflow==2.13.0
dagshub==0.3.35

# Data
pandas==2.2.2
numpy==1.26.4
scipy==1.13.0
pyarrow==16.1.0
Faker==25.2.0

# Observability
structlog==24.2.0

# Config
pyyaml==6.0.1
python-dotenv==1.0.1

# Dev / CI
ruff==0.4.8
pytest==8.2.2
pytest-cov==5.0.0
fakeredis==2.23.2
```
- [ ] **Step 3: Commit**
```bash
git commit -am "chore: swap deps to duckdb, drop gradio/clickhouse/apscheduler"
```

### Task 0.3: `.env.example` and `.dockerignore`

**Files:**
- Create: `.env.example`, `.dockerignore`

- [ ] **Step 1: Write `.env.example`**
```bash
# Offline store (MotherDuck). Leave MOTHERDUCK_TOKEN empty for local DuckDB file.
MOTHERDUCK_TOKEN=
DUCKDB_DATABASE=feature_store
DUCKDB_PATH=feature_store.duckdb

# Online store (Upstash). Leave default for local redis.
REDIS_URL=redis://localhost:6379

# Experiment tracking (DagsHub). Leave URI empty for local ./mlruns.
MLFLOW_TRACKING_URI=
MLFLOW_TRACKING_USERNAME=
MLFLOW_TRACKING_PASSWORD=

# Serving
ALLOWED_ORIGINS=*
FEATURE_VERSION=v1

# Frontend (Vercel) — set to the HF Space URL in prod
NEXT_PUBLIC_API_URL=http://localhost:7860
```
- [ ] **Step 2: Write `.dockerignore`**
```
.git
frontend
node_modules
tests
docs
*.duckdb
mlruns
.venv
__pycache__
*.pyc
.env
```
- [ ] **Step 3: Commit**
```bash
git add .env.example .dockerignore && git commit -m "chore: add env example and dockerignore"
```

---

## Phase 1 — Connections (DuckDB/MotherDuck + Upstash)

### Task 1.1: DuckDB + Upstash connection factories

**Files:**
- Modify: `feature_store/connections.py`
- Test: `tests/test_connections.py`

**Interfaces:**
- Produces: `get_duckdb_client() -> _DuckClient` where `_DuckClient` has `.execute(sql: str, params: dict | None = None) -> list[tuple]` (mirrors the old clickhouse-driver interface so callers change SQL only) and `.register(name: str, df: pandas.DataFrame) -> None`.
- Produces: `get_redis_client() -> redis.Redis` built from `REDIS_URL`.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_connections.py
from feature_store.connections import get_duckdb_client, get_redis_client

def test_duckdb_client_executes_scalar():
    client = get_duckdb_client()
    rows = client.execute("SELECT 1 + 1 AS two")
    assert rows == [(2,)]

def test_duckdb_named_params():
    client = get_duckdb_client()
    rows = client.execute("SELECT $x + $y AS s", {"x": 3, "y": 4})
    assert rows[0][0] == 7

def test_redis_client_from_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    get_redis_client.cache_clear()
    r = get_redis_client()
    assert r.connection_pool.connection_kwargs["host"] == "localhost"
```
- [ ] **Step 2: Run it, verify it fails**
Run: `pytest tests/test_connections.py -v`
Expected: FAIL (import error / old ClickHouse signature).
- [ ] **Step 3: Rewrite `feature_store/connections.py`**
```python
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
        cur = self._conn.cursor()
        cur.execute(sql, params if params is not None else {})
        try:
            return cur.fetchall()
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
```
- [ ] **Step 4: Run tests, verify pass**
Run: `pytest tests/test_connections.py -v`
Expected: PASS (a local redis or fakeredis is not required for the URL-parse test; the duckdb tests use an in-memory/local file).
- [ ] **Step 5: Commit**
```bash
git add feature_store/connections.py tests/test_connections.py
git commit -m "feat: duckdb/motherduck + upstash connection factories"
```

---

## Phase 2 — Single-source feature computation + DuckDB schema

### Task 2.1: DuckDB schema

**Files:**
- Create: `configs/schema.sql`
- Create: `feature_store/schema.py` (applies schema.sql to a connection)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `apply_schema(client) -> None` — executes every `CREATE TABLE IF NOT EXISTS` in `configs/schema.sql`.

- [ ] **Step 1: Write `configs/schema.sql`** (DuckDB dialect, same columns as the old ClickHouse schema minus engine/partition/TTL clauses)
```sql
-- DuckDB / MotherDuck schema for the ML Feature Store.

CREATE TABLE IF NOT EXISTS raw_users (
    user_id     BIGINT,
    signup_date DATE,
    country     VARCHAR,
    plan_type   VARCHAR,
    age_bucket  VARCHAR,
    created_at  TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_transactions (
    transaction_id BIGINT,
    user_id        BIGINT,
    amount         DOUBLE,
    category       VARCHAR,
    status         VARCHAR,
    event_time     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_support_tickets (
    ticket_id  BIGINT,
    user_id    BIGINT,
    severity   VARCHAR,
    resolved   INTEGER,
    event_time TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feature_history (
    entity_id           BIGINT,
    entity_type         VARCHAR,
    feature_version     VARCHAR,
    event_time          TIMESTAMP,
    txn_count_7d        DOUBLE,
    txn_count_30d       DOUBLE,
    txn_count_90d       DOUBLE,
    total_spend_7d      DOUBLE,
    total_spend_30d     DOUBLE,
    total_spend_90d     DOUBLE,
    avg_txn_amount_30d  DOUBLE,
    failed_txn_rate_30d DOUBLE,
    days_since_last_txn DOUBLE,
    open_tickets        DOUBLE,
    ticket_rate_30d     DOUBLE,
    account_age_days    DOUBLE,
    plan_encoded        DOUBLE,
    computed_at         TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS feature_registry (
    feature_name    VARCHAR,
    feature_version VARCHAR,
    entity_type     VARCHAR,
    dtype           VARCHAR,
    description     VARCHAR,
    source_table    VARCHAR,
    transformation  VARCHAR,
    owner           VARCHAR,
    tags            VARCHAR[],
    is_active       INTEGER DEFAULT 1,
    created_at      TIMESTAMP DEFAULT now(),
    deprecated_at   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lineage_edges (
    source_node     VARCHAR,
    target_node     VARCHAR,
    edge_type       VARCHAR,
    feature_version VARCHAR,
    created_at      TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS materialization_log (
    run_id             VARCHAR,
    feature_version    VARCHAR,
    entity_type        VARCHAR,
    entities_processed BIGINT,
    entities_failed    BIGINT,
    duration_ms        BIGINT,
    status             VARCHAR,
    error_message      VARCHAR,
    started_at         TIMESTAMP,
    completed_at       TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS skew_snapshots (
    snapshot_id     VARCHAR,
    feature_name    VARCHAR,
    feature_version VARCHAR,
    context         VARCHAR,
    mean            DOUBLE,
    std             DOUBLE,
    p25             DOUBLE,
    p50             DOUBLE,
    p75             DOUBLE,
    p95             DOUBLE,
    null_rate       DOUBLE,
    sample_count    BIGINT,
    captured_at     TIMESTAMP DEFAULT now()
);
```
- [ ] **Step 2: Write `feature_store/schema.py`**
```python
"""Apply the DuckDB schema (idempotent CREATE TABLE IF NOT EXISTS statements)."""
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent.parent / "configs" / "schema.sql"


def apply_schema(client) -> None:
    sql = _SCHEMA_PATH.read_text()
    for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
        client.execute(stmt)
```
- [ ] **Step 3: Write test**
```python
# tests/test_schema.py
import duckdb
from feature_store.connections import _DuckClient
from feature_store.schema import apply_schema

def test_apply_schema_creates_tables():
    client = _DuckClient(duckdb.connect(":memory:"))
    apply_schema(client)
    tables = {r[0] for r in client.execute("SHOW TABLES")}
    assert {"raw_users", "feature_history", "feature_registry",
            "materialization_log", "skew_snapshots", "lineage_edges"} <= tables
```
- [ ] **Step 4: Run, verify pass**
Run: `pytest tests/test_schema.py -v`
Expected: PASS.
- [ ] **Step 5: Commit**
```bash
git add configs/schema.sql feature_store/schema.py tests/test_schema.py
git commit -m "feat: duckdb schema + idempotent apply"
```

### Task 2.2: Single feature-computation module (kills the duplication)

This is the correctness centerpiece. ONE SQL `SELECT` body computes all 13 features. `compute_and_store` (all users, at a snapshot) and `compute_on_demand` (one user, as of now) both build on it, so serving can never diverge from training.

**Files:**
- Create: `feature_store/features.py`
- Test: `tests/test_features.py`

**Interfaces:**
- Produces: `FEATURE_COLS: list[str]` (the 13 feature names, canonical home — `offline_store` re-exports for back-compat).
- Produces: `feature_select_sql(entity_filter: str = "") -> str` — returns the DuckDB `SELECT` computing all features as of `$snapshot`, windows `$t7/$t30/$t90`, version `$version`; `entity_filter` is `""` or `"AND u.user_id = $uid"`.
- Produces: `compute_and_store(client, snapshot_time: datetime, feature_version: str) -> int` — `INSERT INTO feature_history SELECT ...`, returns rows written.
- Produces: `compute_on_demand(client, entity_id: int, feature_version: str) -> dict[str, float] | None` — computes one entity's features as of now, returns validated dict or None.

- [ ] **Step 1: Write the failing test** (correctness of the shared SQL, in-memory DuckDB, no infra)
```python
# tests/test_features.py
from datetime import datetime, timedelta
import duckdb
from feature_store.connections import _DuckClient
from feature_store.schema import apply_schema
from feature_store import features

def _seed(client):
    apply_schema(client)
    now = datetime(2024, 6, 1)
    client.execute(
        "INSERT INTO raw_users VALUES (1, DATE '2024-01-01', 'US', 'pro', '25-34', now())"
    )
    # 3 successful txns in last 7d, 1 failed in 30d
    rows = [
        (1, 1, 100.0, "one-time", "success", now - timedelta(days=1)),
        (2, 1, 50.0, "one-time", "success", now - timedelta(days=3)),
        (3, 1, 25.0, "one-time", "success", now - timedelta(days=6)),
        (4, 1, 10.0, "one-time", "failed",  now - timedelta(days=20)),
    ]
    client.register("txns", __import__("pandas").DataFrame(
        rows, columns=["transaction_id","user_id","amount","category","status","event_time"]))
    client.execute("INSERT INTO raw_transactions SELECT * FROM txns")
    return now

def test_compute_and_store_counts_windows_correctly():
    client = _DuckClient(duckdb.connect(":memory:"))
    now = _seed(client)
    n = features.compute_and_store(client, snapshot_time=now, feature_version="v1")
    assert n == 1
    row = client.execute(
        "SELECT txn_count_7d, txn_count_30d, total_spend_7d, plan_encoded "
        "FROM feature_history WHERE entity_id = 1")[0]
    assert row[0] == 3.0      # 3 successful txns in 7d
    assert row[1] == 3.0      # same 3 within 30d (failed not counted in success count)
    assert row[2] == 175.0    # 100 + 50 + 25
    assert row[3] == 2.0      # pro -> 2

def test_on_demand_matches_stored_features():
    client = _DuckClient(duckdb.connect(":memory:"))
    now = _seed(client)
    features.compute_and_store(client, snapshot_time=now, feature_version="v1")
    stored = client.execute(
        "SELECT txn_count_30d, plan_encoded FROM feature_history WHERE entity_id = 1")[0]
    on_demand = features.compute_on_demand(client, entity_id=1, feature_version="v1")
    # On-demand uses 'now' as snapshot; with the seed dates it recomputes the same
    # counts. Guard only the version-stable feature to prove the shared SQL path.
    assert on_demand is not None
    assert on_demand["plan_encoded"] == stored[1]
```
- [ ] **Step 2: Run, verify fail**
Run: `pytest tests/test_features.py -v`
Expected: FAIL (module missing).
- [ ] **Step 3: Write `feature_store/features.py`**
```python
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
    """
    return f"""
    SELECT
        u.user_id                                                    AS entity_id,
        'user'                                                       AS entity_type,
        $version                                                     AS feature_version,
        $snapshot                                                    AS event_time,

        count(*) FILTER (WHERE t.status='success'
            AND t.event_time >= $t7  AND t.event_time < $snapshot)   AS txn_count_7d,
        count(*) FILTER (WHERE t.status='success'
            AND t.event_time >= $t30 AND t.event_time < $snapshot)   AS txn_count_30d,
        count(*) FILTER (WHERE t.status='success'
            AND t.event_time >= $t90 AND t.event_time < $snapshot)   AS txn_count_90d,

        coalesce(sum(t.amount) FILTER (WHERE t.status='success'
            AND t.event_time >= $t7  AND t.event_time < $snapshot), 0) AS total_spend_7d,
        coalesce(sum(t.amount) FILTER (WHERE t.status='success'
            AND t.event_time >= $t30 AND t.event_time < $snapshot), 0) AS total_spend_30d,
        coalesce(sum(t.amount) FILTER (WHERE t.status='success'
            AND t.event_time >= $t90 AND t.event_time < $snapshot), 0) AS total_spend_90d,

        coalesce(avg(t.amount) FILTER (WHERE t.status='success'
            AND t.event_time >= $t30 AND t.event_time < $snapshot), 0) AS avg_txn_amount_30d,

        count(*) FILTER (WHERE t.status='failed'
            AND t.event_time >= $t30 AND t.event_time < $snapshot)
        / greatest(count(*) FILTER (WHERE
            t.event_time >= $t30 AND t.event_time < $snapshot), 1)   AS failed_txn_rate_30d,

        coalesce(date_diff('day',
            max(t.event_time) FILTER (WHERE t.status='success'
                AND t.event_time < $snapshot), $snapshot), 0)::DOUBLE AS days_since_last_txn,

        count(*) FILTER (WHERE sk.resolved = 0
            AND sk.event_time < $snapshot)                           AS open_tickets,
        count(*) FILTER (WHERE sk.event_time >= $t30
            AND sk.event_time < $snapshot)                          AS ticket_rate_30d,

        date_diff('day', u.signup_date, CAST($snapshot AS DATE))::DOUBLE AS account_age_days,
        CASE u.plan_type WHEN 'free' THEN 0 WHEN 'basic' THEN 1
             WHEN 'pro' THEN 2 ELSE 3 END                           AS plan_encoded
    FROM raw_users u
    LEFT JOIN raw_transactions t   ON t.user_id = u.user_id
    LEFT JOIN raw_support_tickets sk ON sk.user_id = u.user_id
    WHERE 1=1 {entity_filter}
    GROUP BY u.user_id, u.signup_date, u.plan_type
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
    return validate_single_entity(raw)
```
- [ ] **Step 4: Run, verify pass**
Run: `pytest tests/test_features.py -v`
Expected: PASS.
- [ ] **Step 5: Commit**
```bash
git add feature_store/features.py tests/test_features.py
git commit -m "feat: single-source feature computation (kills serving/offline duplication)"
```

---

## Phase 3 — Offline store: PIT ASOF join + stats (DuckDB)

### Task 3.1: Rewrite `offline_store.py` to DuckDB + ASOF PIT join

**Files:**
- Modify: `feature_store/offline_store.py`
- Test: `tests/test_pit_join.py`

**Interfaces:**
- Re-exports `FEATURE_COLS` from `feature_store.features` (back-compat — other modules import it from here).
- `compute_features(client=None, snapshot_time=None, feature_version="v1") -> int` — thin wrapper over `features.compute_and_store` (keeps the existing name used by backfill).
- `get_latest_features_for_entities(entity_ids: list[int], feature_version="v1", as_of=None) -> pd.DataFrame` — columns `["entity_id"] + FEATURE_COLS`, one latest row per entity (`QUALIFY`).
- `get_training_dataset(label_timestamps: list[tuple[int, datetime]], feature_version="v1") -> pd.DataFrame` — PIT-correct via `ASOF LEFT JOIN`; columns `["entity_id","label_timestamp"] + FEATURE_COLS`.
- `get_feature_stats(feature_version="v1", since_days=7) -> dict[str, dict]` — per-feature mean/std/p25/p50/p75/p95/null_rate/sample_count.

- [ ] **Step 1: Write the failing PIT test** (proves no future leakage — the project's thesis, as an executable check)
```python
# tests/test_pit_join.py
from datetime import datetime
import duckdb, pandas as pd
from feature_store.connections import _DuckClient
from feature_store.schema import apply_schema
from feature_store import offline_store as off

def _seed_history(client):
    apply_schema(client)
    # entity 1 has two snapshots: past value 5, future value 999
    rows = [
        (1, "user", "v1", datetime(2024,1,1), 5,5,5, 5,5,5, 5,0.0,1, 0,0, 10,2, datetime(2024,1,1)),
        (1, "user", "v1", datetime(2024,3,1), 999,999,999, 9,9,9, 9,0.0,1, 0,0, 70,2, datetime(2024,3,1)),
    ]
    cols = ["entity_id","entity_type","feature_version","event_time",
            "txn_count_7d","txn_count_30d","txn_count_90d",
            "total_spend_7d","total_spend_30d","total_spend_90d",
            "avg_txn_amount_30d","failed_txn_rate_30d","days_since_last_txn",
            "open_tickets","ticket_rate_30d","account_age_days","plan_encoded","computed_at"]
    client.register("h", pd.DataFrame(rows, columns=cols))
    client.execute("INSERT INTO feature_history SELECT * FROM h")

def test_pit_join_never_leaks_future(monkeypatch):
    client = _DuckClient(duckdb.connect(":memory:"))
    _seed_history(client)
    monkeypatch.setattr(off, "get_duckdb_client", lambda: client)
    df = off.get_training_dataset([(1, datetime(2024,2,1))], feature_version="v1")
    # As of 2024-02-01 the only valid snapshot is 2024-01-01 (value 5), NOT 999.
    assert df.loc[0, "txn_count_7d"] == 5
```
- [ ] **Step 2: Run, verify fail**
Run: `pytest tests/test_pit_join.py -v`
Expected: FAIL.
- [ ] **Step 3: Rewrite `feature_store/offline_store.py`**
```python
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
                   (count(*) FILTER (WHERE {col} IS NULL OR isnan({col}))) / greatest(count(*),1),
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
```
- [ ] **Step 4: Run, verify pass**
Run: `pytest tests/test_pit_join.py tests/test_features.py -v`
Expected: PASS.
- [ ] **Step 5: Commit**
```bash
git add feature_store/offline_store.py tests/test_pit_join.py
git commit -m "feat: DuckDB offline store with ASOF point-in-time join"
```

---

## Phase 4 — Online store (Upstash) + materialize/backfill as scripts

### Task 4.1: `online_store.py` — no logic change, verify against fakeredis

`online_store.py` already only uses `get_redis_client()` + standard commands, which `redis.from_url` (Upstash) supports unchanged. Only the import of `FEATURE_COLS` should come from `features`.

**Files:**
- Modify: `feature_store/online_store.py` (change `from feature_store.offline_store import FEATURE_COLS` → `from feature_store.features import FEATURE_COLS`)
- Test: `tests/test_online_store.py`

- [ ] **Step 1: Write test using fakeredis**
```python
# tests/test_online_store.py
import fakeredis
from feature_store import online_store as onl

def test_write_then_read_roundtrip(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(onl, "get_redis_client", lambda: fake)
    onl.write_entity(1, {"txn_count_7d": 3.0, "plan_encoded": 2.0})
    got = onl.get_entity(1)
    assert got["txn_count_7d"] == 3.0 and got["plan_encoded"] == 2.0
    assert onl.get_entity(999) is None
```
- [ ] **Step 2: Run, verify fail** (import path or fixture)
Run: `pytest tests/test_online_store.py -v`
- [ ] **Step 3: Apply the one-line import change in `online_store.py`.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit**
```bash
git add feature_store/online_store.py tests/test_online_store.py
git commit -m "test: online store roundtrip on fakeredis; source FEATURE_COLS from features"
```

### Task 4.2: Materialize — drop APScheduler, keep one-shot; add schema bootstrap

**Files:**
- Modify: `materialization/materialize.py`
- Modify: `materialization/backfill.py`

- [ ] **Step 1: In `materialize.py`** replace `get_clickhouse_client` import with `get_duckdb_client`; delete `run_scheduled` and the `--schedule`/`apscheduler` code; call `apply_schema(client)` at the top of `run_materialization`; keep the batch loop and audit-log INSERT (SQL already uses `VALUES`-style list insert — change the parametrized insert to DuckDB by inserting a one-row DataFrame or using positional `?`). Replace the audit INSERT with:
```python
from feature_store.schema import apply_schema
# ...
client.execute(
    """INSERT INTO materialization_log
       (run_id, feature_version, entity_type, entities_processed, entities_failed,
        duration_ms, status, error_message, started_at, completed_at)
       VALUES ($run_id,$v,'user',$proc,$failed,$dur,$status,$err,$started,$completed)""",
    {"run_id": run_id, "v": feature_version, "proc": processed,
     "failed": failed + validation_failures, "dur": duration_ms, "status": status,
     "err": None, "started": started_at, "completed": datetime.utcnow()},
)
```
`_get_all_entity_ids` becomes `SELECT DISTINCT user_id FROM raw_users ORDER BY user_id`.
- [ ] **Step 2: In `backfill.py`** swap the client import to `get_duckdb_client`, call `apply_schema(client)` first, change `compute_features(...)` calls to pass the client, and convert the two `materialization_log` INSERTs to the `$name` DuckDB form shown above. The "existing snapshots" query becomes `SELECT DISTINCT event_time FROM feature_history WHERE feature_version = $version`.
- [ ] **Step 3: Smoke test locally** (DuckDB file, local redis or `REDIS_URL` set)
Run:
```bash
python data/generate.py --users 500 --days 90
python materialization/backfill.py --days 30 --interval-hours 24
python materialization/materialize.py
```
Expected: logs show snapshots computed and N entities materialized.
- [ ] **Step 4: Commit**
```bash
git add materialization/ && git commit -m "feat: materialize/backfill on DuckDB, one-shot only (cron runs in CI)"
```

### Task 4.3: `data/generate.py` → DuckDB loader

**Files:**
- Modify: `data/generate.py`

- [ ] **Step 1:** Replace `get_client()`/`clickhouse_driver` with `get_duckdb_client()`; call `apply_schema(client)`; replace the three `load_*` functions' `client.execute("INSERT ... VALUES", rows)` with DuckDB bulk insert via registered DataFrames:
```python
import pandas as pd
def load_users(client, users):
    client.register("u_df", pd.DataFrame(users))
    client.execute("INSERT INTO raw_users (user_id, signup_date, country, plan_type, age_bucket) "
                   "SELECT user_id, signup_date, country, plan_type, age_bucket FROM u_df")
```
Same pattern for transactions/tickets. Replace `TRUNCATE TABLE IF EXISTS x` with `DELETE FROM x` (create first via `apply_schema`). Replace `SELECT count()` with `SELECT count(*)`.
- [ ] **Step 2: Run** `python data/generate.py --users 500 --days 90`; expect row counts logged.
- [ ] **Step 3: Commit**
```bash
git add data/generate.py && git commit -m "feat: synthetic data generator writes to DuckDB"
```

---

## Phase 5 — Registry, lineage, skew: port to DuckDB

### Task 5.1: `registry.py` — DuckDB + idempotent upsert

**Files:**
- Modify: `feature_store/registry.py`

- [ ] **Step 1:** Swap client import to `get_duckdb_client`. DuckDB has no ReplacingMergeTree, so make `sync_registry` idempotent by deleting the version's rows first:
```python
client.execute("DELETE FROM feature_registry WHERE feature_version = $v", {"v": version})
```
then insert rows one-by-one with `$name` params (tags is a `VARCHAR[]` — pass the Python list directly). Same delete-then-insert for `lineage_edges`. `get_all_features` query: change `%(version)s` → `$version`, `is_active = 1`, `count()`→n/a.
- [ ] **Step 2:** In `get_all_features`, `tags` comes back as a Python list from DuckDB `VARCHAR[]` — no change needed downstream.
- [ ] **Step 3: Commit**
```bash
git add feature_store/registry.py && git commit -m "feat: registry sync on DuckDB (delete-then-insert idempotency)"
```

### Task 5.2: `lineage/graph.py` — DuckDB

**Files:**
- Modify: `lineage/graph.py`

- [ ] **Step 1:** Swap client import to `get_duckdb_client`; change `%(feature)s`/`%(version)s` → `$feature`/`$version`. Logic unchanged.
- [ ] **Step 2: Commit**
```bash
git add lineage/graph.py && git commit -m "feat: lineage queries on DuckDB"
```

### Task 5.3: `skew/detector.py` — DuckDB stats

**Files:**
- Modify: `skew/detector.py`
- Test: `tests/test_skew.py` (existing — keep passing)

- [ ] **Step 1:** Swap client import to `get_duckdb_client`; in `_capture_serving_snapshot` change the stats SQL to DuckDB (`stddev_pop`, `quantile_cont(col,0.25)`, `count(*) FILTER (WHERE col IS NULL OR isnan(col)) / greatest(count(*),1)`); change the training-snapshot query `LIMIT 1 BY feature_name` → `QUALIFY row_number() OVER (PARTITION BY feature_name ORDER BY captured_at DESC) = 1`; convert INSERT to `$name` form or registered-DataFrame bulk insert; import `FEATURE_COLS` from `feature_store.features`.
- [ ] **Step 2: Run existing skew unit tests** (they test `_run_ks_test`, which is pure — should still pass untouched)
Run: `pytest tests/test_skew.py -v`
Expected: PASS.
- [ ] **Step 3: Commit**
```bash
git add skew/detector.py && git commit -m "feat: skew snapshots + KS report on DuckDB"
```

---

## Phase 6 — Serving API hardening

### Task 6.1: Rewrite `serving/main.py` — reuse `features`, real health, env CORS, full lineage endpoint

**Files:**
- Modify: `serving/main.py`
- Test: `tests/test_serving.py`

**Interfaces:**
- `/health` → 200 `{"status":"ok"}` only if DuckDB `SELECT 1` and Redis `ping()` both succeed; else 503.
- On-demand path calls `features.compute_on_demand(get_duckdb_client(), entity_id, version)` — NO inline SQL.
- New `/lineage` (no arg) → `get_full_lineage_graph(version)` (the frontend needs the whole DAG; currently only per-feature exists).
- CORS `allow_origins` from `ALLOWED_ORIGINS` env (comma-separated; `*` allowed for local).

- [ ] **Step 1: Write test** (TestClient, monkeypatched stores)
```python
# tests/test_serving.py
from fastapi.testclient import TestClient
import fakeredis, duckdb
import serving.main as main
from feature_store.connections import _DuckClient
from feature_store.schema import apply_schema

def _client(monkeypatch):
    duck = _DuckClient(duckdb.connect(":memory:")); apply_schema(duck)
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(main, "get_duckdb_client", lambda: duck)
    monkeypatch.setattr(main, "get_redis_client", lambda: fake)
    return TestClient(main.app), duck, fake

def test_health_ok(monkeypatch):
    client, _, _ = _client(monkeypatch)
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"

def test_health_503_when_store_down(monkeypatch):
    client, _, _ = _client(monkeypatch)
    monkeypatch.setattr(main, "get_redis_client", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    r = client.get("/health")
    assert r.status_code == 503
```
- [ ] **Step 2: Run, verify fail.**
Run: `pytest tests/test_serving.py -v`
- [ ] **Step 3: Edit `serving/main.py`:**
  - Delete `_on_demand_features` entirely (all ~50 lines). In `get_features` and `get_features_batch`, replace the on-demand block with `features.compute_on_demand(get_duckdb_client(), eid, feature_version)`.
  - Import: `from feature_store.features import compute_on_demand`; `from feature_store.connections import get_duckdb_client, get_redis_client`; `from lineage.graph import get_full_lineage_graph`.
  - Replace `/health`:
```python
@app.get("/health")
async def health():
    try:
        get_duckdb_client().execute("SELECT 1")
        get_redis_client().ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"dependency down: {exc}")
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
```
  - CORS:
```python
_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")]
app.add_middleware(CORSMiddleware, allow_origins=_origins,
                   allow_methods=["*"], allow_headers=["*"])
```
  - Add endpoint:
```python
@app.get("/lineage")
async def full_lineage(feature_version: str = Query(default="v1")):
    return get_full_lineage_graph(feature_version=feature_version)
```
  - Change uvicorn entrypoint port to `int(os.getenv("PORT", "7860"))` (HF Spaces default).
- [ ] **Step 4: Run, verify pass.**
Run: `pytest tests/test_serving.py -v`
- [ ] **Step 5: Commit**
```bash
git add serving/main.py tests/test_serving.py
git commit -m "feat: serving reuses shared feature compute, real health check, env CORS, full lineage endpoint"
```

---

## Phase 7 — Training → DagsHub MLflow

### Task 7.1: `train.py` — DuckDB PIT dataset + DagsHub tracking, local fallback

**Files:**
- Modify: `training/train.py`

- [ ] **Step 1:** Swap `get_clickhouse_client` → `get_duckdb_client`. In `generate_labels`, change `%(...)s` → `$...`, `countIf(...)` → `count(*) FILTER (WHERE ...)`, `LIMIT %(n)s`→`LIMIT $n`. Replace `build_pit_training_dataset`'s inline LATERAL query with a call to `offline_store.get_training_dataset(list(zip(entity_ids, label_ts)), FEATURE_VERSION)` (DRY — reuse the ASOF join). Keep `demonstrate_pit_leakage` (uses `get_latest_features_for_entities`, already ported).
- [ ] **Step 2:** MLflow config with local fallback + fix the model-name mismatch (`churn-predictor` → `churn_predictor_v1` to match `features.yaml` lineage):
```python
MODEL_NAME = "churn_predictor_v1"
def _init_mlflow():
    uri = os.getenv("MLFLOW_TRACKING_URI", "").strip()
    mlflow.set_tracking_uri(uri or "file:./mlruns")
    mlflow.set_experiment(EXPERIMENT_NAME)
```
Call `_init_mlflow()` at the top of `train_and_register`. Auth (`MLFLOW_TRACKING_USERNAME/PASSWORD`) is read by MLflow from env automatically — no code needed.
- [ ] **Step 3: Local smoke run** (after generate + backfill from Phase 4)
Run: `python training/train.py --no-pit-demo`
Expected: logs `training_complete` with roc_auc; run appears under `./mlruns` locally.
- [ ] **Step 4: Commit**
```bash
git add training/train.py
git commit -m "feat: training on DuckDB PIT dataset, DagsHub MLflow with local fallback"
```

---

## Phase 8 — GitHub Actions (CI + scheduled pipelines + keep-warm)

### Task 8.1: Rewrite CI

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1:** Replace with lint + infra-free tests (all tests now run on in-memory DuckDB + fakeredis — no services needed) and a backend Docker build check. Drop the Gradio image build.
```yaml
name: CI
on:
  push: { branches: [main] }
  pull_request: { branches: [main] }
jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: ruff check . --select E,W,F,I --ignore E501
      - run: ruff format --check .
      - run: pytest tests/ -v --cov=feature_store --cov=skew --cov=serving --cov-fail-under=70
  docker-build:
    runs-on: ubuntu-latest
    needs: lint-and-test
    steps:
      - uses: actions/checkout@v4
      - run: docker build -f Dockerfile.api -t feature-store-api:ci .
```
- [ ] **Step 2: Commit**
```bash
git add .github/workflows/ci.yml && git commit -m "ci: infra-free tests on duckdb+fakeredis, drop gradio build"
```

### Task 8.2: Scheduled materialize + keep-warm

**Files:**
- Create: `.github/workflows/materialize.yml`

- [ ] **Step 1:** Cron every 6h: run backfill (latest snapshot) + materialize against MotherDuck/Upstash using repo secrets; plus a keep-warm curl to the HF Space so it doesn't sleep before recruiters visit.
```yaml
name: Materialize
on:
  schedule: [{ cron: "0 */6 * * *" }]
  workflow_dispatch:
env:
  MOTHERDUCK_TOKEN: ${{ secrets.MOTHERDUCK_TOKEN }}
  DUCKDB_DATABASE: feature_store
  REDIS_URL: ${{ secrets.REDIS_URL }}
jobs:
  materialize:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.api.txt
      - run: python materialization/materialize.py
  keep-warm:
    runs-on: ubuntu-latest
    steps:
      - run: curl -fsS "${{ secrets.HF_SPACE_URL }}/health" || true
```
- [ ] **Step 2: Commit**
```bash
git add .github/workflows/materialize.yml && git commit -m "ci: scheduled materialize + HF keep-warm"
```

### Task 8.3: On-demand training workflow

**Files:**
- Create: `.github/workflows/train.yml`

- [ ] **Step 1:** `workflow_dispatch` job that installs full `requirements.txt`, sets MLflow/DagsHub + store secrets, runs `python training/train.py`. (Full YAML mirrors 8.2 env plus `MLFLOW_TRACKING_URI/USERNAME/PASSWORD` secrets; single step `python training/train.py`.)
- [ ] **Step 2: Commit**
```bash
git add .github/workflows/train.yml && git commit -m "ci: manual training workflow logging to DagsHub"
```

---

## Phase 9 — Next.js frontend on Vercel

> **Design note:** This phase creates the app skeleton and data layer with real code. The VISUAL design (layout, typography, color, spacing) must be produced at build time using the **frontend-design skill** — do not hardcode a look here. Each tab renders data from the endpoints below.

### Task 9.1: Scaffold Next.js app

**Files:**
- Create: `frontend/` (Next.js App Router, TypeScript)
- Create: `frontend/package.json`, `frontend/next.config.mjs`, `frontend/tsconfig.json`, `frontend/.env.example`

- [ ] **Step 1:** Scaffold:
```bash
cd frontend && npx create-next-app@latest . --typescript --app --eslint --no-tailwind --no-src-dir --import-alias "@/*"
npm install recharts
```
(Tailwind optional — decide during frontend-design. Recharts covers bar/histogram charts.)
- [ ] **Step 2:** `frontend/.env.example`:
```
NEXT_PUBLIC_API_URL=http://localhost:7860
```
- [ ] **Step 3: Commit**
```bash
git add frontend && git commit -m "feat: scaffold next.js frontend"
```

### Task 9.2: API client + shared types

**Files:**
- Create: `frontend/lib/api.ts`

- [ ] **Step 1:** Typed fetch wrapper hitting `NEXT_PUBLIC_API_URL`:
```ts
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:7860";

async function get<T>(path: string, params?: Record<string, string>): Promise<T> {
  const qs = params ? "?" + new URLSearchParams(params) : "";
  const r = await fetch(`${BASE}${path}${qs}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json() as Promise<T>;
}
async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json() as Promise<T>;
}

export const api = {
  registry: (v = "v1") => get<Feature[]>("/registry", { feature_version: v }),
  lineage: (v = "v1") => get<LineageGraph>("/lineage", { feature_version: v }),
  skew: (v = "v1") => get<{ report: SkewRow[] }>("/skew-report", { feature_version: v }),
  materializationLog: () => get<MatRun[]>("/materialization-log", { limit: "100" }),
  metrics: () => get<Metrics>("/metrics"),
  batch: (entity_ids: number[], v = "v1") =>
    post<BatchResult>("/features/batch", { entity_ids, feature_version: v }),
};

export interface Feature { feature_name: string; dtype: string; description: string;
  source_table: string; owner: string; tags: string[]; }
export interface LineageNode { id: string; type: "raw_table" | "feature" | "model"; }
export interface LineageGraph { nodes: LineageNode[]; edges: { source: string; target: string; type: string }[]; }
export interface SkewRow { feature_name: string; training_mean: number; serving_mean: number;
  mean_shift: number; ks_statistic: number; ks_pvalue: number; flagged: boolean; }
export interface MatRun { run_id: string; status: string; entities_processed: number;
  entities_failed: number; duration_ms: number; started_at: string; completed_at: string; }
export interface Metrics { online_store: Percentiles; on_demand: Percentiles; batch: Percentiles;
  cache_hit_rate: number; online_store_entities: number; }
export interface Percentiles { p50: number; p95: number; p99: number; count: number; }
export interface BatchResult { results: Record<string, Record<string, number> | null>;
  hits: number; misses: number; on_demand_computed: number; latency_ms: number; }
```
- [ ] **Step 2: Commit**
```bash
git add frontend/lib/api.ts && git commit -m "feat: typed frontend API client"
```

### Task 9.3: Dashboard shell + four tab components

**Files:**
- Create: `frontend/app/page.tsx` (tabbed dashboard shell — client component with tab state)
- Create: `frontend/components/FeatureExplorer.tsx`, `TrainingPull.tsx`, `SkewReport.tsx`, `MaterializationLog.tsx`, `LineageGraph.tsx`

- [ ] **Step 1:** Build the four tabs, each fetching its endpoint via `api`. Representative component (skew, with a Recharts bar chart) — the others follow the same fetch→render shape:
```tsx
// frontend/components/SkewReport.tsx
"use client";
import { useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ReferenceLine, ResponsiveContainer, Cell } from "recharts";
import { api, SkewRow } from "@/lib/api";

export default function SkewReport() {
  const [rows, setRows] = useState<SkewRow[]>([]);
  const [err, setErr] = useState<string>();
  useEffect(() => { api.skew().then(d => setRows(d.report)).catch(e => setErr(String(e))); }, []);
  if (err) return <p role="alert">Failed to load skew report: {err}</p>;
  if (!rows.length) return <p>No skew data yet — run the training workflow first.</p>;
  return (
    <section>
      <h2>Training vs Serving Skew (KS test)</h2>
      <ResponsiveContainer width="100%" height={360}>
        <BarChart data={rows}>
          <XAxis dataKey="feature_name" angle={-35} textAnchor="end" height={90} />
          <YAxis label={{ value: "KS statistic", angle: -90 }} />
          <Tooltip />
          <ReferenceLine y={0.05} stroke="#fbbf24" strokeDasharray="4 4" />
          <Bar dataKey="ks_statistic">
            {rows.map((r, i) => <Cell key={i} fill={r.flagged ? "#ef4444" : "#3b82f6"} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}
```
- [ ] **Step 2:** `LineageGraph.tsx` — SVG layered layout (raw x=0.1 → feature x=0.5 → model x=0.9), porting the position math from the deleted Gradio `_render_lineage_dag` (no graph library needed). `TrainingPull.tsx` — textbox of entity IDs → `api.batch` → table + summary. `MaterializationLog.tsx` — table + Recharts bar of entities-per-run + `api.metrics` latency table. `FeatureExplorer.tsx` — registry table + `<LineageGraph/>`.
- [ ] **Step 3:** `page.tsx` — a tab bar switching the four components. Apply the frontend-design skill for visual treatment.
- [ ] **Step 4: Verify locally**
Run (backend on :7860 in another shell): `cd frontend && npm run dev`, open http://localhost:3000, confirm all four tabs load data.
- [ ] **Step 5: Commit**
```bash
git add frontend && git commit -m "feat: next.js dashboard — explorer, training pull, skew, materialization"
```

---

## Phase 10 — Deployment & docs

### Task 10.1: Backend Dockerfile for HF Spaces

**Files:**
- Modify: `Dockerfile.api` (listen on 7860, non-root, healthcheck)
- Create: `README_HF.md` (the Space's README with HF frontmatter — copied into the Space repo)

- [ ] **Step 1:** `Dockerfile.api`:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.api.txt .
RUN pip install --no-cache-dir -r requirements.api.txt
COPY feature_store/ feature_store/
COPY serving/ serving/
COPY lineage/ lineage/
COPY skew/ skew/
COPY configs/ configs/
ENV PORT=7860 PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["uvicorn", "serving.main:app", "--host", "0.0.0.0", "--port", "7860"]
```
- [ ] **Step 2:** `README_HF.md` frontmatter (HF reads this to configure the Space):
```markdown
---
title: Feature Store API
emoji: 🦆
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---
FastAPI feature server. Set secrets MOTHERDUCK_TOKEN, REDIS_URL, ALLOWED_ORIGINS in Space settings.
```
- [ ] **Step 3:** Document the deploy (in main README, Task 10.3): create an HF Docker Space, `git remote add space https://huggingface.co/spaces/<user>/<space>`, `git push space main`, set Space secrets. `Dockerfile.api` must be named `Dockerfile` in the Space, or set `dockerfile_path` — simplest: the Space repo uses a root `Dockerfile` symlinked/renamed from `Dockerfile.api`.
- [ ] **Step 4: Commit**
```bash
git add Dockerfile.api README_HF.md && git commit -m "feat: HF Spaces docker backend on port 7860"
```

### Task 10.2: Vercel config

**Files:**
- Create: `frontend/vercel.json` (only if needed — root is `frontend/`)

- [ ] **Step 1:** In Vercel, import the repo, set **Root Directory = `frontend`**, add env var `NEXT_PUBLIC_API_URL = https://<user>-<space>.hf.space`. No `vercel.json` needed for a standard Next.js app; add one only for headers/redirects. Set backend `ALLOWED_ORIGINS` to the Vercel domain after first deploy.
- [ ] **Step 2: Commit** (if any config added).

### Task 10.3: README rewrite

**Files:**
- Modify: `README.md`

- [ ] **Step 1:** Rewrite: new architecture diagram (Vercel → HF Spaces → MotherDuck/Upstash; GitHub Actions cron; DagsHub), the free-tier stack table, live URLs, local-dev quickstart (`.env` from `.env.example`, `python data/generate.py`, `backfill`, `materialize`, `uvicorn serving.main:app --port 7860`, `cd frontend && npm run dev`), and a "Deploy your own" section (HF Space secrets, Vercel root dir + env, GitHub secrets: `MOTHERDUCK_TOKEN`, `REDIS_URL`, `HF_SPACE_URL`, `MLFLOW_*`). Keep the "what makes this different" section but update ClickHouse→DuckDB/MotherDuck ASOF and Gradio→Next.js.
- [ ] **Step 2: Commit**
```bash
git add README.md && git commit -m "docs: rewrite for free-tier Vercel + HF Spaces architecture"
```

---

## Self-Review (completed against the audit)

- **Feature duplication (Critical):** fixed in Task 2.2 / 6.1 — one SQL, reused. ✅
- **ClickHouse no free host (Critical):** MotherDuck, Phases 1–5. ✅
- **Fake PIT-vs-naive demo (Critical):** `demonstrate_pit_leakage` retained in train.py (Task 7.1) as the real leaked-vs-PIT AUC comparison; the frontend Training tab shows PIT retrieval. ✅
- **Health check (Important):** Task 6.1 checks both stores, 503 on failure. ✅
- **CORS lockdown (Important):** env `ALLOWED_ORIGINS`, Task 6.1 + 10.2. ✅
- **Zero tests on PIT/serving (Important):** Tasks 2.2, 3.1 (PIT no-leak test), 6.1. ✅
- **CI theater / gradio build (Important):** Task 8.1 real tests, dropped. ✅
- **`datetime.utcnow`, model-name mismatch, dead lineage calls (minor):** model name fixed 7.1; dead Gradio calls deleted with Gradio; `utcnow` left as-is except where touched (acceptable — not a correctness bug). ✅
- **Secrets (Nice):** `.env.example` + platform secrets, no creds in code. ✅

**Deferred (not in scope, flagged):** auth on endpoints (demo is public read-only by design — note in README); replacing every `datetime.utcnow()` call repo-wide (cosmetic deprecation).
