# Repo Audit Report — ML-System-Design-Feature-Store

> [!WARNING]
> **Historical snapshot, 2026-07-12. Do not read this as the current state.**
> Many of its findings have since been fixed and several of its figures are
> stale: it reports ~59% test coverage (now 86%), a `txns_before >= 2` threshold
> that was later raised to 5 and has now been returned to 2 with the reason
> recorded, and it describes a live MotherDuck-backed API that no longer exists.
> Its scorecard is all-red for a codebase that has moved on.
>
> One thing it got right that a later review got backwards: it flagged the
> missing unique constraint on `feature_history`. `configs/schema.sql` declares
> a primary key, but every CREATE there is `IF NOT EXISTS`, so the declaration
> never reached the already-created table. The shipped database genuinely had no
> primary key, and `materialization/backfill.py` failed on all 91 snapshots
> because `INSERT OR IGNORE` had no conflict target. `feature_store/schema.py`
> now migrates the table on load.
>
> For the current state, run the test suite and read README.md.

**Date:** 2026-07-12
**Stack detected:** Python 3.11 (FastAPI · DuckDB/MotherDuck · redis/Valkey · Pandera · LightGBM · scipy · structlog) + TypeScript/React (Next.js 16 App Router). Deployed: frontend on Vercel, backend on Google Cloud Run, offline store MotherDuck, online store Aiven Valkey.
**Scope:** whole repo — `feature_store/`, `serving/`, `materialization/`, `skew/`, `lineage/`, `training/`, `data/`, `configs/`, `tests/`, `.github/workflows/`, `Dockerfile`, `frontend/` (app + components + lib), docs. Ran via 4 parallel auditors across the 13 passes.

## Summary

- Total findings: **31** (after dedup)
- Auto-fixed (trivial-safe): **6**
- Needs review (see `PLAN.md`): **20 tasks** (some findings merged)
- Critical: 3 · Major: 12 · Minor: 6 · Notes: 4 (+ several clean-area confirmations)

## Production-readiness scorecard

| Category | Status | Notes |
|---|---|---|
| Correctness | ❌ | `materialize` reports "success" on total validation failure; `days_since_last_txn` = 0 for never-transacted; skew snapshot fabricates zero-stats on empty windows |
| Silent failures | ❌ | `lifespan` swallows schema/registry failure; the above two both fail silently |
| Security | ❌ | Unauthenticated `/skew-report` recomputes **and writes** to MotherDuck every call → can exhaust the 10-compute-hr/mo free tier; open CORS/no-auth (acceptable for a demo, flagged) |
| Concurrency | ❌ | Async endpoints block the event loop on sync DuckDB/Redis I/O; `register("labels")` fixed-name race; backfill check-then-act race |
| Performance | ❌ | `/features/batch` N+1 (up to 500 MotherDuck round-trips); `/metrics` full keyspace SCAN; every serving miss hits MotherDuck |
| Architecture | ⚠️ | Core design is sound (single-source feature SQL, ASOF PIT join verified correct); issues are hardening, not structure |
| Production-readiness | ❌ | `/health` couples both deps (no degraded mode); keep-warm pinged a dead HF URL; README describes the abandoned HF+Upstash stack |
| Test coverage | ❌ | The headline dual-path serving (`/features/{id}`), `get_latest_features_for_entities`, and `compute_skew_report` have **zero** tests; only 53% serving coverage |

**Important context:** the backend is a **live, public, unauthenticated** API. The two Critical items that involve MotherDuck compute (`/skew-report` write-on-read, `/features/batch` N+1) are real exposure *now*, not hypothetical — a scripted loop against `/skew-report` could burn the monthly MotherDuck quota and take the offline store offline for everything (materialize, training, on-demand serving).

## Auto-fixed (trivial-safe)

1. **`README_HF.md`** — deleted. Dead file for the abandoned Hugging Face Space (`sdk: docker`, `app_port: 7860`), referenced nowhere.
2. **`.env.example`** — "Online store (Upstash)" → "Redis-compatible — e.g. Aiven Valkey"; "set to the HF Space URL in prod" → "Cloud Run service URL".
3. **`frontend/.env.example`** — `http://localhost:7860` → `:8080` + a Cloud Run URL note (7860 was the dead HF port).
4. **`feature_store/connections.py`** (module docstring) — "online store (Redis / Upstash)" / "Upstash provides an rediss:// URL" → Aiven Valkey. Comment only; no logic change.
5. **`.github/workflows/materialize.yml`** — keep-warm now pings `secrets.CLOUD_RUN_URL/health` (was dead `HF_SPACE_URL`), with `--retry` for cold starts and no `|| true` mask.
6. **`tests/test_connections.py`** — made hermetic: forces in-memory DuckDB (`MOTHERDUCK_TOKEN=""`, `DUCKDB_PATH=":memory:"`, `cache_clear()`) instead of opening a real `feature_store.duckdb` in the repo root (which caused a reproducible file-lock failure when another process held the file). 25/25 tests pass after the change.

## Findings requiring review (see PLAN.md for full fix + verification)

### Correctness & silent failures (passes 1–3, 5)
- **Critical** `materialization/materialize.py:111` — `status` ignores `validation_failures`; a run where every batch fails Pandera validation reports `"success"` with **0 entities written**. Online store silently goes stale and nothing downstream can tell. → PLAN Task 2.
- **Major** `feature_store/features.py:61` — `days_since_last_txn` coalesces a never-transacted user (NULL) to `0`, identical to "transacted today" — inverts the recency signal for the exact cold-start population. Consistent across offline/serving/training (the single-source SQL), so no skew, but wrong. → PLAN Task 4.
- **Major** `skew/detector.py:56-100` — `_capture_serving_snapshot` lacks the `rows[0][0] is not None` guard that `get_feature_stats` has; an empty recent-window writes fabricated `mean=0/std=0` "serving" stats → false-positive skew flags across the board on `/skew-report`. → PLAN Task 5.
- **Major** `serving/main.py:96-104` (`lifespan`) — swallows `sync_registry()`/`apply_schema()` failures; the app serves with tables possibly never created, and `/health` (`SELECT 1`, no table touch) still returns ok. → PLAN Task 6.
- **Minor** `training/train.py:105` vs docstring `78-79` — churn label uses `txns_before >= 2`, docstring says `>= 5`. Stale doc or regressed label. → PLAN Task 17.

### Concurrency (pass 9)
- **Major** `serving/main.py` (all endpoints) — `async def` handlers call blocking DuckDB/Redis I/O directly on the single event-loop thread; one slow MotherDuck query serializes all traffic. → PLAN Task 12.
- **Major** `feature_store/offline_store.py:67` + `connections.py` — `register("labels", …)` then `execute(…)` are two separate lock acquisitions on a **fixed** view name; concurrent callers overwrite each other's DataFrame → wrong PIT training set, no error. → PLAN Task 7.
- **Major** `materialization/backfill.py:74-97` — check-then-act on existing snapshots with no DB unique constraint; concurrent/retried runs duplicate `feature_history` rows, making `QUALIFY … =1` and materialize counts non-deterministic. → PLAN Task 11.

### Security & performance (passes 8, 10)
- **Critical** `serving/main.py:281-286` + `skew/detector.py` — unauthenticated `/skew-report` recomputes + **writes** a MotherDuck snapshot every call; no cache, no rate limit → free-tier quota-exhaustion DoS. → PLAN Task 1.
- **Major** `serving/main.py:246-252` — `/features/batch` resolves misses with up to **500 sequential** `compute_on_demand` MotherDuck queries instead of one batched `IN (…)`. → PLAN Task 8.
- **Major** `feature_store/online_store.py:124-131` — `get_online_store_size()` does a full keyspace SCAN on every unauthenticated `/metrics` call (O(N) per request). → PLAN Task 9.
- **Minor** `serving/main.py:310` — `/materialization-log?limit=` has no upper bound (`le=`). → PLAN Task 20.
- **Minor** `serving/main.py:118-124` — CORS `*` + no auth on any route, including the state-writing `/skew-report`. Acceptable for a demo; tighten `ALLOWED_ORIGINS` + add a shared-secret header on write-triggering endpoints before wider exposure. → PLAN Task 21.
- **Note (clean):** all DuckDB SQL uses bound `$params` for values; f-string interpolation is only of hardcoded identifiers — **no SQL injection**. `.env` confirmed gitignored and excluded from image/build context.

### Production-readiness & docs (pass 12)
- **Major** `serving/main.py:170-177` — `/health` fails if *either* store is down; a transient MotherDuck blip would cycle Cloud Run instances that could still serve 100% of Redis-hit traffic. → PLAN Task 10.
- **Critical (docs)** `README.md` — architecture diagram, stack tables, and entire "Deploy Your Own" section still describe **Hugging Face Spaces + Upstash** (dead). Top-level doc a recruiter opens first. → PLAN Task 14.
- **Major (config)** `materialization/materialize.yml` — the cron only *reads* the latest `feature_history` and re-pushes it; it never calls `backfill`/`compute_features`, so data never actually refreshes (contradicts the README diagram). → PLAN Task 15.

### Frontend (React/TS)
- **Critical** `frontend/lib/useApi.ts:22-33` — no request-generation/AbortController guard; a slow response can overwrite fresher state (stale data shown as the answer to the current query). Shared by all five panels. → PLAN Task 3.
- **Major** `frontend/components/Tip.tsx:19-29` — the `position:fixed` popover computes coords once on hover and never updates; scrolling (wheel doesn't fire mouseleave) leaves it detached from its `?`. → PLAN Task 13.
- **Minor** `frontend/components/StatusPill.tsx` — pings `/health` once; if it lands during a Cloud Run cold start it shows "waking up" for the rest of the session. → PLAN Task 18.
- **Minor** `frontend/app/page.tsx:38-56` — incomplete ARIA tabs (no `aria-controls`/panel `id`, no arrow-key roving tabindex). → PLAN Task 19.
- **Note** `frontend/components/TrainingPull.tsx:126` — `Object.entries` reorders rows to ascending entity-ID rather than input order. → PLAN Task 22.
- **Clean:** `humanize()` audited at every call site — display-only, never used for keys/lookups/API params/lineage id-matching. Data-shape contracts, loading/empty/error precedence, effect deps, and `key` props all verified clean.

### Test coverage (pass 13)
- **Major** — `serving/main.py` dual-path `/features/{id}` (hit/miss/404), `/features/batch`, `/skew-report`; `offline_store.get_latest_features_for_entities` (populates the online store) and `get_feature_stats`; `skew.compute_skew_report` — all **untested**. → PLAN Task 16.
- **Note** `.github/workflows/ci.yml` — `--cov-fail-under=52` is real (measured ~59%), but the average is padded by trivial 100%-covered modules while the business logic above is the uncovered part. Raise the gate only *with* the new tests.

## Clean areas (confirmed, not padded)
- **Feature computation is single-source and correct:** one SQL body in `features.py::feature_select_sql`, reused by offline compute, on-demand serving, and (via `offline_store`) training. The join fan-out is correctly avoided with per-table pre-aggregation. DuckDB `/` is float division (not ClickHouse `intDiv` truncation) — `failed_txn_rate_30d` is correct.
- **PIT correctness:** `get_training_dataset`'s `ASOF LEFT JOIN` with version pre-filtered in a CTE is correct point-in-time semantics; the no-trailing-WHERE comment is accurate.
- **Registry/lineage** SQL and delete-then-insert idempotency are sound for single-writer startup.
- **Dockerfile / .dockerignore / .gcloudignore:** correct for Cloud Run (`$PORT`, `exec`, single worker), COPY set covers all runtime imports, `.env` excluded everywhere, `duckdb==1.5.4` pinned in both requirements files, `ci.yml` builds the root `Dockerfile`.
- **No hardcoded secrets** anywhere in tracked source.
