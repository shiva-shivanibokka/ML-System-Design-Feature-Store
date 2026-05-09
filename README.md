# ML System Design: End-to-End Feature Store

A production-grade feature store built to the same architectural standards as **Uber Michelangelo**, **DoorDash**, and **Twitter Cortex** — the systems that serve billions of ML predictions per day.

Solves the **#1 silent production bug in ML systems**: training-serving skew caused by features computed differently at training time vs serving time.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Raw Data Sources (Upstream)                        │
│   raw_users · raw_transactions · raw_support_tickets                 │
└────────────────────────────┬─────────────────────────────────────────┘
                             │  Feature computation (SQL transforms)
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│             Offline Store — ClickHouse (columnar OLAP)               │
│  · feature_history  (immutable, append-only, versioned snapshots)    │
│  · Point-in-time correct joins (no future leakage)                   │
│  · Backfill: compute features at every historical timestamp          │
│  · Pandera validation on every write                                 │
└──────────────┬────────────────────────────┬─────────────────────────┘
               │  Materialization            │  Training pipeline
               │  (APScheduler, 6h)          │  (PIT join → LightGBM)
               ▼                             ▼
┌──────────────────────────┐   ┌────────────────────────────────────┐
│  Online Store — Redis    │   │  MLflow Model Registry             │
│  Hash-per-entity pattern │   │  Experiment tracking + versioning  │
│  HGETALL → <2ms          │   └────────────────────────────────────┘
└──────────────┬───────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Feature Server — FastAPI                           │
│                                                                        │
│  BATCH PATH:     Redis HGETALL         → <2ms   (materialized)       │
│  ON-DEMAND PATH: ClickHouse query      → ~20ms  (cold-start)         │
│                                                                        │
│  /features/{id}  · /features/batch  · /skew-report                  │
│  /lineage/{feature}  · /registry  · /materialization-log            │
└──────────────────────────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Gradio UI — 4 tabs                                 │
│  Tab 1: Feature Explorer   — registry + lineage DAG                  │
│  Tab 2: Training Data Pull — PIT-correct dataset download            │
│  Tab 3: Skew Report        — KS test per feature (training vs serving)│
│  Tab 4: Materialization Log — audit trail + serving latency metrics  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## What Makes This Different

### 1. ClickHouse as the Offline Store
Most feature store tutorials use PostgreSQL or SQLite for historical features. ClickHouse is the columnar OLAP database used by **Cloudflare, Uber, and ByteDance** for analytical workloads at scale. Columnar storage makes range queries (e.g., "all features for the last 90 days") 10-100x faster than row-oriented databases.

### 2. Point-in-Time Correct Joins (Demonstrated with a Bug)
The training pipeline includes a deliberate **label leakage demonstration** — it first shows what happens with a naive feature join (inflated AUC), then fixes it with PIT-correct retrieval. This is the question interviewers at Uber and DoorDash ask: "What is training-serving skew and how does a feature store prevent it?"

### 3. Dual-Path Serving
- **Batch path**: pre-materialized Redis hash, returned in <2ms
- **On-demand path**: ClickHouse query for cold-start entities, ~20ms
- Every request logs which path was taken — latency difference is visible in Tab 4

### 4. Pandera Schema Validation
Every feature write to the offline store is validated before it lands. Prevents silent NaN propagation (users with no transactions produce NaN from ClickHouse aggregations), out-of-range rates, and invalid plan encodings.

### 5. Feature Lineage Graph
Every feature has a provenance DAG: which raw tables it was derived from, what transformation was applied, and which models consume it. Rendered as an interactive Plotly network graph in the Gradio UI.

### 6. KS Test Skew Detection
The `/skew-report` endpoint runs a Kolmogorov-Smirnov test per feature comparing training-time distributions (captured during `train.py`) against current serving-time distributions. Features with p-value < 0.05 are flagged — the statistical signal that your model is receiving different inputs than it was trained on.

### 7. GitHub Actions CI
First system design repo with a real CI pipeline: ruff lint → pytest unit tests (no infra required) → Docker build check. Runs on every push.

---

## Stack

| Component | Tool | Why |
|---|---|---|
| Offline store | **ClickHouse 24.3** | Columnar OLAP — not used in any other repo |
| Online store | **Redis 7** | Hash-per-entity, sub-2ms HGETALL |
| Feature server | **FastAPI** | Dual-path with latency logging |
| Feature validation | **Pandera** | Schema enforcement at write time |
| Materialization | **APScheduler** | Embedded scheduler — no Airflow instance |
| Skew detection | **SciPy KS test** | Per-feature statistical comparison |
| Training | **LightGBM + MLflow** | PIT-correct + experiment tracking |
| UI | **Gradio 4** | 4-tab dashboard with Plotly charts |
| CI | **GitHub Actions** | ruff + pytest + Docker build |
| Observability | **structlog** JSON logs | Structured, searchable by request_id |

---

## Quick Start

```bash
# 1. Start all services
docker-compose up --build

# 2. Seed raw data (10K users, 90 days of transactions)
docker-compose exec api python data/generate.py

# 3. Backfill feature history (90 days of snapshots)
docker-compose exec api python materialization/backfill.py --days 90

# 4. Materialize to online store (Redis)
docker-compose exec api python materialization/materialize.py --once

# 5. Train the churn model (includes PIT leakage demo)
docker-compose exec api python training/train.py
```

| Service | URL |
|---|---|
| Gradio UI | http://localhost:7860 |
| Feature API docs | http://localhost:8000/docs |
| MLflow UI | http://localhost:5001 |
| ClickHouse playground | http://localhost:8123/play |

---

## Project Structure

```
ML-System-Design-Feature-Store/
├── configs/
│   ├── clickhouse/init.sql      # ClickHouse schema (all 6 tables)
│   ├── features.yaml            # Feature definitions + lineage edges
│   └── config.yaml              # App config
├── data/
│   └── generate.py              # Synthetic data → ClickHouse raw tables
├── feature_store/
│   ├── connections.py           # ClickHouse + Redis client singletons
│   ├── registry.py              # Feature definitions sync (YAML → ClickHouse)
│   ├── offline_store.py         # ClickHouse: compute, PIT join, stats
│   ├── online_store.py          # Redis: HGETALL, pipeline batch write
│   └── validator.py             # Pandera schema validation
├── materialization/
│   ├── backfill.py              # Historical feature computation (resumable)
│   └── materialize.py           # Offline → Online sync (APScheduler)
├── training/
│   └── train.py                 # PIT demo + LightGBM + MLflow
├── serving/
│   └── main.py                  # FastAPI dual-path feature server
├── skew/
│   └── detector.py              # KS test per feature, snapshot capture
├── lineage/
│   └── graph.py                 # Lineage DAG queries
├── gradio_app/
│   └── app.py                   # 4-tab Gradio dashboard
├── tests/
│   ├── test_validator.py        # Pandera unit tests (no infra)
│   └── test_skew.py             # KS test unit tests (no infra)
├── .github/workflows/ci.yml     # Lint + test + Docker build CI
├── docker-compose.yml
├── Dockerfile.api
├── Dockerfile.gradio
├── requirements.api.txt
├── requirements.gradio.txt
└── requirements.txt
```

---

## Key Concepts Demonstrated

**Training-Serving Skew (the core problem)**
Without a feature store, ML engineers compute features manually for training and then re-implement (often differently) at serving time. The feature store prevents this by being the single computation source for both.

**Point-in-Time Correct Joins**
For each (entity, label_timestamp) pair in the training set, we retrieve the latest feature value where `event_time <= label_timestamp`. This prevents features computed using future data from leaking into training labels — a bug that inflates training AUC but collapses at deployment.

**Offline vs Online Store**
- Offline (ClickHouse): historical feature values, used for training. Append-only. Supports time-travel queries.
- Online (Redis): latest feature values per entity, used for serving. Sub-millisecond access via HGETALL.

**Feature Materialization**
The scheduled job that copies the latest offline store values to the online store. Without this, the online store would serve stale features.

**Feature Versioning**
Features are tagged with a version (v1, v2). Version bumps are required when transformation logic changes. Models are linked to the feature version they were trained on.
