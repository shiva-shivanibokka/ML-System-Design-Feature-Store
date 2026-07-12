# ML System Design: End-to-End Feature Store

A production-grade feature store built to the same architectural pattern as **Uber Michelangelo**, **DoorDash**, and **Twitter Cortex** — the systems that serve billions of ML predictions per day.

Solves the **#1 silent production bug in ML systems**: training-serving skew caused by features computed differently at training time vs serving time. And it runs entirely on **free tiers** — no paid infra, no credit card required to fork and deploy your own copy.

---

## Architecture

```
┌────────────────────────┐        ┌──────────────────────────────────────┐
│  Vercel (Next.js)      │──HTTP──▶│  Hugging Face Spaces (Docker)        │
│  Dashboard: explorer,  │        │  FastAPI feature server, port 7860   │
│  training pull, skew,  │◀───────│  Dual-path: Redis batch + on-demand  │
│  materialization log   │  JSON  │  DuckDB query                        │
└────────────────────────┘        └───────┬───────────────┬─────────────┘
                                           │               │
                              offline read/write     online read/write
                                           │               │
                                           ▼               ▼
                              ┌────────────────────┐  ┌──────────────────┐
                              │  MotherDuck         │  │  Upstash Redis   │
                              │  (hosted DuckDB)    │  │  hash-per-entity │
                              │  feature_history,    │  │  HGETALL <2ms    │
                              │  registry, lineage,  │  └──────────────────┘
                              │  skew snapshots       │
                              │  ASOF JOIN = PIT       │
                              │  correctness           │
                              └─────────┬──────────────┘
                                        │
                     ┌──────────────────┼──────────────────┐
                     │                                      │
                     ▼                                      ▼
          ┌───────────────────────┐          ┌──────────────────────────┐
          │  GitHub Actions        │          │  GitHub Actions          │
          │  materialize.yml       │          │  train.yml (dispatch)    │
          │  cron 6h: backfill +   │          │  PIT training dataset →  │
          │  materialize + keep-   │          │  LightGBM → DagsHub      │
          │  warm ping to HF Space │          │  MLflow tracking         │
          └───────────────────────┘          └──────────────────────────┘
```

The single biggest correctness win in this design: feature computation lives in **exactly one place** — `feature_store/features.py`. Offline backfill, on-demand serving, and the training PIT join all call the same SQL. There is no second implementation to drift out of sync.

---

## Free-Tier Stack

| Component | Service | Free-tier limit |
|---|---|---|
| Offline store | **MotherDuck** (hosted DuckDB) | 10 GB storage / 10 compute-hours per month |
| Online store | **Upstash Redis** | 256 MB / 500K commands per month |
| Feature server | **Hugging Face Spaces** (Docker) | 2 vCPU / 16 GB RAM, sleeps when idle |
| Frontend | **Vercel** (Hobby plan) | Unlimited personal projects, generous bandwidth |
| Experiment tracking | **DagsHub** (hosted MLflow) | Free public repo with MLflow tracking server |
| Batch jobs / CI | **GitHub Actions** | 2,000 free minutes/month on public repos |

No Supabase, no Fly.io, no Render, no paid databases. Everything above has a permanent free tier.

---

## What Makes This Different

### 1. DuckDB `ASOF JOIN` for Point-in-Time Correctness
The offline store is **MotherDuck** (DuckDB hosted in the cloud). Training labels are joined against feature history with DuckDB's native `ASOF JOIN`: for each `(entity_id, label_timestamp)`, it matches the most recent feature snapshot with `event_time <= label_timestamp`. No future data can leak into a training row — this is the exact temporal join that commercial feature stores (Feast, Tecton, Hopsworks) are built around.

### 2. Single-Source Feature Computation (the anti-skew guarantee)
`feature_store/features.py` holds **one SQL `SELECT`** that computes all 13 features. It's reused by three call sites:
- `compute_and_store()` — offline backfill, writes `feature_history`
- `compute_on_demand()` — serving's cold-start fallback
- the training pipeline's PIT join, via `offline_store.get_training_dataset()`

If a feature's logic ever changes, it changes in one file and every consumer picks it up identically. This is what actually prevents training-serving skew — most tutorials just describe the problem; this repo structurally rules it out.

### 3. Point-in-Time Leakage, Demonstrated as a Bug
`training/train.py` includes a deliberate **label leakage demonstration**: it first trains on naively-joined (leaky) features and shows the inflated AUC, then retrains on the PIT-correct dataset and shows the honest number. This is the exact question interviewers at Uber/DoorDash ask: "what is training-serving skew, and how does a feature store prevent it?"

### 4. Dual-Path Serving
- **Batch path**: pre-materialized Redis hash, `HGETALL` in <2ms
- **On-demand path**: DuckDB/MotherDuck query for cold-start entities, ~20ms
- Every request logs which path was taken and its latency; the dashboard's materialization tab surfaces the difference.

### 5. Pandera Schema Validation
Every feature write to the offline store is validated before it lands — catches null propagation, out-of-range rates, and invalid plan encodings before they hit `feature_history`.

### 6. Feature Lineage Graph
Every feature has a provenance record: which raw tables it derives from, what transformation was applied, which models consume it. The Next.js dashboard renders it as an interactive DAG (`/lineage` endpoint, full graph, no arg needed).

### 7. KS-Test Skew Detection
`/skew-report` runs a Kolmogorov-Smirnov test per feature, comparing the distribution captured during `train.py` against current serving-time distributions. Features with p-value < 0.05 are flagged — the statistical signal your model is now seeing different inputs than it trained on.

### 8. Infra-Free CI
Every test in `tests/` runs against in-memory DuckDB and `fakeredis` — no live services required. CI runs lint, format check, the full test suite, and a Docker build check on every push.

---

## Stack

| Layer | Tool | Why |
|---|---|---|
| Offline store | **MotherDuck (DuckDB)** | Free-tier hosted analytical DB with native `ASOF JOIN` |
| Online store | **Upstash Redis** | Serverless, URL-configured, hash-per-entity, sub-2ms reads |
| Feature server | **FastAPI** on **Hugging Face Spaces** | Dual-path serving with latency logging, Docker Space |
| Frontend | **Next.js (App Router) + Recharts** on **Vercel** | Dashboard: explorer, training pull, skew, materialization log |
| Feature validation | **Pandera** | Schema enforcement at write time |
| Skew detection | **SciPy KS test** | Per-feature statistical comparison |
| Training | **LightGBM** + **MLflow (DagsHub)** | PIT-correct training + hosted experiment tracking |
| Batch jobs | **GitHub Actions** (cron + manual dispatch) | Scheduled materialize, keep-warm ping, on-demand training |
| Observability | **structlog** JSON logs | Structured, searchable by request_id |

---

## Local Dev Quickstart

Zero cloud accounts required — DuckDB falls back to a local file, Redis to `localhost`, MLflow to `./mlruns`.

```bash
# 1. Configure environment (all values default to local-only)
cp .env.example .env

# 2. Install dependencies
pip install -r requirements.txt

# 3. Seed synthetic data (users, transactions, support tickets)
python data/generate.py

# 4. Backfill 90 days of feature snapshots
python materialization/backfill.py --days 90

# 5. Materialize the latest snapshot into the online store
python materialization/materialize.py

# 6. Start the feature server
uvicorn serving.main:app --port 7860

# 7. In another shell, start the dashboard
cd frontend && npm install && npm run dev
```

Feature API docs: `http://localhost:7860/docs`. Dashboard: `http://localhost:3000`.

If you want a local Redis instead of pointing `REDIS_URL` at Upstash: `docker run -p 6379:6379 redis:7-alpine`.

---

## Deploy Your Own

### Backend — Hugging Face Space
1. Create a new **Docker** Space at huggingface.co/new-space.
2. `git remote add space https://huggingface.co/spaces/<user>/<space>` and `git push space main`. HF auto-detects the root `Dockerfile` (listens on port 7860).
3. In the Space's **Settings → Variables and secrets**, set: `MOTHERDUCK_TOKEN`, `REDIS_URL`, `ALLOWED_ORIGINS`, `DUCKDB_DATABASE`.
4. Optionally copy `README_HF.md` over the Space's `README.md` for the HF frontmatter (title/emoji/colors).

### Seed the stores (one-time, required)
A fresh deploy has empty stores, so the API returns 404s / empty tables until you seed once. Point your local env at the cloud services (set `MOTHERDUCK_TOKEN`, `DUCKDB_DATABASE`, `REDIS_URL` in `.env`) and run:
```bash
python data/generate.py                    # raw tables → MotherDuck
python materialization/backfill.py --days 90   # feature_history snapshots
python materialization/materialize.py      # latest features → Upstash Redis
```
After this the scheduled `materialize.yml` keeps the online store fresh every 6h. Run `train.yml` (or `python training/train.py` with the DagsHub env vars) once to populate the model registry and the training-time skew baseline.

### Frontend — Vercel
1. Import the repo into Vercel.
2. Set **Root Directory = `frontend`**.
3. Add env var `NEXT_PUBLIC_API_URL = https://<your-space>.hf.space`.
4. After the first deploy, set the backend's `ALLOWED_ORIGINS` (Space secret) to your Vercel domain, e.g. `https://<your-app>.vercel.app`.

### Batch jobs — GitHub Actions
Add these repo secrets (Settings → Secrets and variables → Actions):
- `MOTHERDUCK_TOKEN`, `REDIS_URL` — used by `materialize.yml` (scheduled every 6h) and its keep-warm ping
- `HF_SPACE_URL` — the Space's public URL, used for the keep-warm ping
- `MLFLOW_TRACKING_URI`, `MLFLOW_TRACKING_USERNAME`, `MLFLOW_TRACKING_PASSWORD` — used by `train.yml` (manual dispatch) for DagsHub-hosted MLflow

---

## Project Structure

```
ML-System-Design-Feature-Store/
├── configs/
│   ├── schema.sql                # DuckDB schema (all tables)
│   ├── features.yaml             # Feature definitions + lineage edges
│   └── config.yaml               # App config
├── data/
│   └── generate.py               # Synthetic data → DuckDB raw tables
├── feature_store/
│   ├── connections.py            # DuckDB/MotherDuck + Redis client factories
│   ├── features.py               # THE single source of feature computation
│   ├── schema.py                 # Idempotent schema apply
│   ├── registry.py               # Feature definitions sync (YAML → DuckDB)
│   ├── offline_store.py          # DuckDB: compute, ASOF PIT join, stats
│   ├── online_store.py           # Redis: HGETALL, pipeline batch write
│   └── validator.py              # Pandera schema validation
├── materialization/
│   ├── backfill.py               # Historical feature computation
│   └── materialize.py            # Offline → online sync
├── training/
│   └── train.py                  # PIT leakage demo + LightGBM + MLflow
├── serving/
│   └── main.py                   # FastAPI dual-path feature server
├── skew/
│   └── detector.py                # KS test per feature, snapshot capture
├── lineage/
│   └── graph.py                   # Lineage DAG queries
├── frontend/                      # Next.js dashboard (deploys to Vercel)
│   ├── app/                       # App Router pages
│   ├── components/                # Explorer, TrainingPull, SkewReport, MaterializationLog
│   └── lib/api.ts                 # Typed API client
├── tests/                         # Infra-free: in-memory DuckDB + fakeredis
├── .github/workflows/
│   ├── ci.yml                     # Lint + test + Docker build
│   ├── materialize.yml            # Scheduled backfill/materialize + keep-warm
│   └── train.yml                  # Manual-dispatch training run
├── Dockerfile                     # HF Spaces backend image (root file, port 7860)
├── README_HF.md                   # HF Space frontmatter/README
├── requirements.api.txt           # Backend runtime deps
└── requirements.txt               # Full dev deps (API + training + tooling)
```

---

## Key Concepts Demonstrated

**Training-Serving Skew (the core problem)**
Without a feature store, ML engineers compute features manually for training and then re-implement (often differently) at serving time. This repo prevents it structurally: `feature_store/features.py` is the single computation path for offline, on-demand, and training.

**Point-in-Time Correct Joins**
For each `(entity, label_timestamp)` pair in the training set, DuckDB's `ASOF JOIN` retrieves the latest feature snapshot where `event_time <= label_timestamp`. This prevents features computed using future data from leaking into training labels — a bug that inflates training AUC and collapses at deployment.

**Offline vs Online Store**
- Offline (MotherDuck): historical feature values, used for training. Append-only, supports time-travel queries via `ASOF JOIN`.
- Online (Upstash Redis): latest feature values per entity, used for serving. Sub-millisecond access via `HGETALL`.

**Feature Materialization**
The scheduled GitHub Actions job that copies the latest offline store values into the online store. Without this, the online store would serve stale features.

**Feature Versioning**
Features are tagged with a version (`v1`, `v2`, ...). Version bumps are required when transformation logic changes. Models are linked to the feature version they were trained on.

---

## Known Trade-offs

This is a portfolio/demo system, and a few things are deliberately out of scope: endpoints are public read-only (no auth) since the point is to demonstrate the feature-store architecture, not build an auth layer; the HF Space's disk is ephemeral by design (all durable state lives in MotherDuck/Upstash, never on the Space filesystem); and the backend runs a single uvicorn worker on purpose (see the `Dockerfile` comment) rather than scaling workers, since the whole stack is sized for free-tier compute-hours, not production traffic.
