# Fix Plan — ML-System-Design-Feature-Store

Generated from repo-bug-audit on 2026-07-12. 20 tasks, ordered by severity (Critical first). Trivial-safe findings were already auto-fixed (see AUDIT.md). Nothing below has been applied — these change behavior and need review.

> Execution note: this is compatible with `superpowers:writing-plans` / `execute-plan`. "Missing test" tasks are placed immediately before the fix they cover (write test → watch fail → fix).

---

## Task 1: Cache `/skew-report` so it can't exhaust the MotherDuck free tier
- **File:** `serving/main.py` (`/skew-report`), optionally `skew/detector.py`
- **Category:** Security + Performance (pass 8/10) · **Severity:** Critical
- **Finding:** `/skew-report` is a public, unauthenticated GET that recomputes 13-feature aggregates AND writes a `skew_snapshots` row to MotherDuck on every call. MotherDuck free tier = 10 compute-hrs/month total.
- **Why it matters:** a scripted loop (or a dashboard on a short timer) exhausts the monthly quota and locks out the *entire* offline store — materialize, backfill, training, and on-demand serving all fail for the rest of the month. This is live and public right now.
- **Proposed change:** memoize the report with a short TTL so repeated hits are served from memory, not MotherDuck.
  ```python
  import time
  _skew_cache: dict[str, tuple[float, dict]] = {}
  _SKEW_TTL = 300  # seconds

  @app.get("/skew-report")
  async def skew_report(feature_version: str = Query(default="v1")):
      now = time.time()
      hit = _skew_cache.get(feature_version)
      if hit and now - hit[0] < _SKEW_TTL:
          return hit[1]
      try:
          report = compute_skew_report(feature_version=feature_version)
      except Exception as exc:
          log.error("skew_report_failed", error=str(exc))
          raise HTTPException(status_code=500, detail=str(exc))
      payload = {"feature_version": feature_version, "report": report}
      _skew_cache[feature_version] = (now, payload)
      return payload
  ```
- **Also:** consider gating the *write* in `_capture_serving_snapshot` behind the cache too (don't write a snapshot on every request). Simplest: only capture-and-write inside the cache-miss branch (already the case with the above).
- **Verification:** `pytest tests/test_serving.py -k skew` (add a test that a 2nd call within TTL does NOT invoke `compute_skew_report` — monkeypatch a call counter). Manual: `for i in 1 2 3; do curl -s $API/skew-report >/dev/null; done` and confirm only one MotherDuck snapshot row is written.
- **Depends on:** none.

## Task 2: `materialize` must not report "success" when validation wiped every batch
- **File:** `materialization/materialize.py:111`
- **Category:** Silent failure / Correctness (pass 2) · **Severity:** Critical
- **Finding:** `status = "success" if failed == 0 else (…)` ignores `validation_failures`.
- **Why it matters:** if every batch fails Pandera validation, `failed`=0 and `processed`=0, so the run logs `"success"` with zero Redis writes; the online store silently goes stale and monitoring/dashboard can't detect it.
- **Proposed change:**
  ```python
  total_failed = failed + validation_failures
  status = "success" if total_failed == 0 else ("partial" if processed > 0 else "failed")
  ```
  (and log `total_failed`).
- **Verification:** unit test: monkeypatch `validate_feature_batch` to raise for all batches, assert returned `status == "failed"` and `processed == 0`. `pytest tests/test_materialize.py -k validation_failure`.
- **Depends on:** none.

## Task 3: Guard `useApi` against out-of-order responses
- **File:** `frontend/lib/useApi.ts:22-33`
- **Category:** Frontend correctness (race) · **Severity:** Critical (UX)
- **Finding:** `run(promise)` has no generation counter / AbortController; a slow earlier request can settle after a newer one and overwrite fresh state with stale data.
- **Why it matters:** on a Cloud-Run cold start (seconds) a user re-submitting `TrainingPull` sees results for the *previous* input rendered as if current — silently wrong data. All five panels share this hook.
- **Proposed change:** add a monotonically-increasing ref; ignore a resolution whose generation is stale.
  ```ts
  const gen = useRef(0);
  const run = useCallback((p: Promise<T>) => {
    const mine = ++gen.current;
    setState({ data: null, loading: true, error: null });
    p.then(
      (data) => { if (mine === gen.current) setState({ data, loading: false, error: null }); },
      (e) => { if (mine === gen.current) setState({ data: null, loading: false, error: String(e) }); },
    );
  }, []);
  ```
- **Verification:** `cd frontend && npm run build`. Manual: throttle network, submit TrainingPull twice fast, confirm the final render matches the last input.
- **Depends on:** none.

## Task 4: Fix `days_since_last_txn` for never-transacted users
- **File:** `feature_store/features.py:61` (and a test)
- **Category:** Correctness / feature quality · **Severity:** Major
- **Finding:** NULL (no successful txn ever) → coalesced to `0`, identical to "transacted today".
- **Why it matters:** cold-start/inactive users look maximally recent on this feature, inverting its signal — and it flows identically into training, so the model learns the wrong thing (no skew, but wrong).
- **Proposed change:** use a large sentinel instead of 0 (and retrain so the model sees it). In `feature_select_sql`:
  ```sql
  coalesce(date_diff('day', t.last_success_txn, $snapshot), 9999)::DOUBLE AS days_since_last_txn,
  ```
  Update the Pandera check if it caps this feature; add `FEATURE_DESCRIPTIONS`/docs note. Retrain (`python training/train.py`) and re-run backfill/materialize so stored values reflect the change.
- **Verification:** extend `tests/test_features.py` — seed a user with zero successful txns, assert `days_since_last_txn == 9999`, and a user who transacted "today" ≈ 0. `pytest tests/test_features.py -k days_since`.
- **Depends on:** re-seed (Task 15 workflow / manual) after applying.

## Task 5: Guard skew serving-snapshot against empty windows
- **File:** `skew/detector.py:56-100` (`_capture_serving_snapshot`)
- **Category:** Silent failure / Correctness · **Severity:** Major
- **Finding:** no `rows[0][0] is not None` guard (unlike `get_feature_stats`); an empty recent window writes fabricated `mean=0/std=0` serving stats → false skew flags.
- **Why it matters:** the Skew tab flags every feature as drifted whenever recent `feature_history` is empty (e.g. cron interval > sample window), misleading the headline monitoring feature.
- **Proposed change:** mirror `get_feature_stats`'s guard — skip a feature whose aggregate is NULL, and exclude it from `sv_map` in `compute_skew_report` so it isn't KS-tested against a fake baseline.
  ```python
  mean = result[0][0]
  if mean is None:
      continue  # empty window — don't fabricate a serving stat
  ```
- **Verification:** `pytest tests/test_skew.py -k empty_window` — seed training snapshot only (no recent serving rows), assert the feature is absent from the report rather than flagged.
- **Depends on:** none.

## Task 6: Fail startup (or health) when schema/registry didn't apply
- **File:** `serving/main.py:96-104` (`lifespan`), and/or `/health`
- **Category:** Production-readiness / silent failure · **Severity:** Major
- **Finding:** `lifespan` catches and swallows `sync_registry()` (which calls `apply_schema`); app serves with tables possibly missing, `/health` still ok.
- **Why it matters:** a bad deploy (missing config, MotherDuck auth blip at boot) yields an instance that 500s every real request while reporting healthy — Cloud Run keeps routing to it.
- **Proposed change:** either re-raise on startup failure (Cloud Run marks the revision unhealthy), or make `/health` touch a real table. Recommended `/health` addition:
  ```python
  get_duckdb_client().execute("SELECT count(*) FROM feature_registry")
  ```
  (fails 503 if the schema was never applied). Keep the registry-sync log, but don't let a missing schema read as healthy.
- **Verification:** `pytest tests/test_serving.py -k health_schema_missing` — point at an empty in-memory DB with no `apply_schema`, assert `/health` → 503.
- **Depends on:** relates to Task 10 (health redesign) — do together.

## Task 7: Make `register()`+`execute()` atomic / uniquely-named
- **File:** `feature_store/offline_store.py:60-84`, `feature_store/connections.py`
- **Category:** Concurrency · **Severity:** Major
- **Finding:** `register("labels", df)` + `execute(...)` are two separate lock acquisitions on a fixed view name; concurrent callers clobber each other's `labels`.
- **Why it matters:** two concurrent `get_training_dataset` calls (or a future endpoint reusing it) silently join against the wrong label DataFrame → corrupt PIT training set, no error.
- **Proposed change:** unique per-call view name, or a combined locked method. Minimal:
  ```python
  view = f"labels_{uuid4().hex}"
  client.register(view, labels)
  try:
      rows = client.execute(sql.replace(" labels ", f" {view} "), {...})
  finally:
      client.raw.unregister(view)  # add unregister() passthrough to _DuckClient
  ```
  Better: add `_DuckClient.query_with_df(name, df, sql, params)` that registers+executes+unregisters under one `self._lock`. Apply the same pattern to `data/generate.py` (`u_df`/`t_df`/`s_df`).
- **Verification:** `pytest tests/test_pit_join.py` still passes; add a concurrency test spawning two `get_training_dataset` threads with different labels and asserting neither sees the other's rows.
- **Depends on:** none.

## Task 8: Batch the `/features/batch` on-demand fallback
- **File:** `serving/main.py:246-252`, `feature_store/features.py`
- **Category:** Performance · **Severity:** Major
- **Finding:** misses resolved with up to 500 sequential `compute_on_demand` MotherDuck queries.
- **Why it matters:** a cold-cache 500-entity batch holds the DuckDB lock and burns MotherDuck compute for 500× the single-entity cost, blocking all other requests.
- **Proposed change:** add a batched compute using the existing `entity_filter` hook with an `IN` list (bound param), e.g. `compute_on_demand_batch(client, ids, version)` doing `feature_select_sql(entity_filter="AND u.user_id IN (SELECT UNNEST($uids))")`, then map results back. Replace the per-entity miss loop with one call.
- **Verification:** `pytest tests/test_serving.py -k batch_on_demand` — fakeredis empty (all misses) + in-memory DuckDB seeded, assert one query resolves all and results match per-entity compute.
- **Depends on:** none.

## Task 9: Replace `/metrics` keyspace SCAN with a maintained counter
- **File:** `feature_store/online_store.py:124-131`, write path + `serving/main.py:/metrics`
- **Category:** Performance · **Severity:** Major
- **Finding:** `get_online_store_size()` SCANs the whole keyspace per unauthenticated `/metrics` call.
- **Why it matters:** O(N) per request against the hosted Valkey instance; a monitoring probe scales cost/latency with store size.
- **Proposed change:** maintain a Redis `SET` of entity ids (or an integer counter) updated in `write_entities_pipeline`/`delete_entity`, and use `SCARD`/`GET`. Or cache the SCAN count with a short TTL if you don't want to touch the write path.
- **Verification:** `pytest tests/test_online_store.py -k size` — write N entities, assert size == N via the counter, delete one, assert N-1.
- **Depends on:** none.

## Task 10: Component-level `/health` (degraded mode)
- **File:** `serving/main.py:170-177`
- **Category:** Production-readiness · **Severity:** Major
- **Finding:** `/health` 503s if either store is down, even though Redis-hit traffic still works without DuckDB.
- **Why it matters:** a transient MotherDuck outage (e.g. quota exhaustion mid-month) cycles Cloud Run instances that could still serve materialized traffic.
- **Proposed change:** report per-component status and only fail on total unavailability:
  ```python
  status = {}
  for name, check in (("redis", lambda: get_redis_client().ping()),
                      ("duckdb", lambda: get_duckdb_client().execute("SELECT 1"))):
      try: check(); status[name] = "ok"
      except Exception as e: status[name] = f"down: {e}"
  ok = any(v == "ok" for v in status.values())
  return JSONResponse(status_code=200 if ok else 503,
                      content={"status": "ok" if all(v=="ok" for v in status.values()) else "degraded", "components": status})
  ```
- **Verification:** `pytest tests/test_serving.py -k health_degraded` — redis up, duckdb down → 200 "degraded"; both down → 503.
- **Depends on:** merge with Task 6.

## Task 11: Add a uniqueness constraint on `feature_history`
- **File:** `configs/schema.sql`, `feature_store/features.py` (`compute_and_store`)
- **Category:** Concurrency / idempotency · **Severity:** Major
- **Finding:** no unique constraint on `(entity_id, feature_version, event_time)`; concurrent/retried backfill duplicates rows; `compute_and_store` does a blind INSERT.
- **Why it matters:** duplicate snapshots make `QUALIFY row_number()=1` (materialize) and materialize counts non-deterministic.
- **Proposed change:** add `PRIMARY KEY (entity_id, feature_version, event_time)` (or a `UNIQUE`) to `feature_history` in `schema.sql`, and make `compute_and_store` use `INSERT OR IGNORE`/delete-the-snapshot-first (matching `registry.py`'s idempotent pattern). Note: changing the DDL requires re-creating the MotherDuck table (migration step).
- **Verification:** `pytest tests/test_features.py -k idempotent` — call `compute_and_store` twice for the same snapshot, assert row count unchanged.
- **Depends on:** Task 4 (both touch feature_history / re-seed) — sequence the re-seed once.

## Task 12: Stop blocking the event loop in async endpoints
- **File:** `serving/main.py` (all routes), `feature_store/connections.py`
- **Category:** Concurrency · **Severity:** Major
- **Finding:** `async def` handlers call sync DuckDB/Redis directly on the loop thread.
- **Why it matters:** one slow MotherDuck query stalls every concurrent request (single Cloud Run worker); the advertised "<2ms" latencies assume non-blocking concurrency.
- **Proposed change:** offload blocking calls with `from starlette.concurrency import run_in_threadpool` (wrap `get_entity`, `compute_on_demand`, `get_entities_batch`, the DuckDB reads), or switch Redis to `redis.asyncio`. The DuckDB lock stays (serializes DB access) but the offload keeps the loop free for Redis-hit requests. Larger change — touches every endpoint.
- **Verification:** `pytest tests/test_serving.py` still green; load test (optional) `hey -n 200 -c 20 $API/health` shows p99 not gated behind a slow `/features` miss.
- **Depends on:** none, but do after the correctness tasks.

## Task 13: `Tip` popover must follow the trigger on scroll
- **File:** `frontend/components/Tip.tsx:19-29`
- **Category:** Frontend correctness · **Severity:** Major (UX)
- **Finding:** `position:fixed` popover coords computed once on hover; scrolling detaches it from the `?`.
- **Why it matters:** on long/scrollable views (AboutTab, wide tables) the tip floats away from its button — looks broken.
- **Proposed change:** while shown, add passive `scroll` (capture) + `resize` listeners that re-run the position calc (or `hide()`); remove them in cleanup on hide/unmount.
  ```ts
  useEffect(() => {
    if (!open) return;
    const update = () => setPos(computeFromRect(btnRef.current));
    window.addEventListener("scroll", update, { passive: true, capture: true });
    window.addEventListener("resize", update);
    return () => { window.removeEventListener("scroll", update, true); window.removeEventListener("resize", update); };
  }, [open]);
  ```
- **Verification:** `npm run build`; manual: open a tip, scroll, confirm it tracks (or closes).
- **Depends on:** none.

## Task 14: Rewrite `README.md` for the real (Cloud Run) stack
- **File:** `README.md`
- **Category:** Docs / production-readiness · **Severity:** Critical (portfolio-facing)
- **Finding:** architecture diagram, stack tables, and the whole "Deploy Your Own" section describe Hugging Face Spaces + Upstash (abandoned).
- **Why it matters:** it's the first thing a recruiter/interviewer reads and it describes infrastructure that isn't used; the "port 7860 / HF" caption contradicts the actual Cloud Run `$PORT`/8080 Dockerfile.
- **Proposed change:** rewrite: architecture diagram (Vercel → Cloud Run FastAPI → MotherDuck offline + Aiven Valkey online; GitHub Actions cron; MLflow local/DagsHub-ready), free-tier stack table (MotherDuck, Aiven Valkey, Cloud Run, Vercel, GitHub Actions), and Deploy section (Cloud Run `gcloud run deploy --source .` with env vars, Vercel root=`frontend` + `NEXT_PUBLIC_API_URL`, GitHub secrets `MOTHERDUCK_TOKEN`/`REDIS_URL`/`CLOUD_RUN_URL`). Add the live URLs. Fix the `Dockerfile` caption in Project Structure to "Cloud Run image ($PORT/8080)".
- **Verification:** re-read against the actual `Dockerfile`, `.github/workflows/*`, and deployed URLs — no HF/Upstash/7860 references remain (`grep -riE "hugging|upstash|7860" README.md` empty).
- **Depends on:** none.

## Task 15: Make the scheduled cron actually refresh data (or correct the claim)
- **File:** `.github/workflows/materialize.yml`
- **Category:** Config / production-readiness · **Severity:** Major
- **Finding:** the `materialize` job only re-pushes the latest existing `feature_history`; it never computes a new snapshot, so data never refreshes — contradicting the README diagram.
- **Why it matters:** the "keeps the online store fresh every 6h" story is false as written.
- **Proposed change:** decide and implement one: (a) add a small backfill step before materialize — `python materialization/backfill.py --days 1 --interval-hours 24` then `python materialization/materialize.py`; or (b) if the demo data is intentionally static, change the README/diagram to say "re-materialize only" and drop the "backfill" wording. Needs the GitHub secrets set to actually run.
- **Verification:** trigger the workflow (`workflow_dispatch`) with secrets set; confirm a new `feature_history` snapshot timestamp appears in MotherDuck (option a) or that docs match behavior (option b).
- **Depends on:** Task 14 (keep docs + workflow consistent).

## Task 16: Add tests for the dual-path serving + offline/skew logic (the headline features)
- **File:** `tests/test_serving.py`, `tests/test_offline_store.py` (new), `tests/test_skew.py`
- **Category:** Test coverage · **Severity:** Major
- **Finding:** `/features/{id}` (hit/miss/404), `/features/batch`, `/skew-report`, `get_latest_features_for_entities`, `get_feature_stats`, `compute_skew_report` all have zero tests.
- **Why it matters:** the one thing this repo exists to demonstrate — leakage-free dual-path serving — is unverified; regressions ship silently (as several already did during the migration).
- **Proposed change:** with in-memory DuckDB + fakeredis: (1) `/features/{id}` returns the Redis value on a hit, computes on a miss, 404s unknown; (2) `get_latest_features_for_entities` returns only the latest row per entity as-of cutoff (seed 2 snapshots); (3) `get_feature_stats` percentiles/null_rate on known values; (4) `compute_skew_report` end-to-end with a seeded training + serving snapshot. Then raise `--cov-fail-under` to match.
- **Verification:** `pytest tests/ -q` all green; coverage of `serving/main.py` and `offline_store.py` materially up.
- **Depends on:** write these *before/with* Tasks 8, 5 (they exercise the same code).

## Task 17: Reconcile the churn-label threshold (2 vs 5)
- **File:** `training/train.py:105` vs docstring `78-79`
- **Category:** Logic/doc mismatch · **Severity:** Minor
- **Finding:** code labels churn with `txns_before >= 2`; docstring says `>= 5`.
- **Why it matters:** a looser label than documented makes the churn target noisier; interviewers reading the docstring see a different definition than what trained the model.
- **Proposed change:** decide the intended threshold; update code and docstring to match (and retrain if you change the code).
- **Verification:** `python training/train.py --no-pit-demo` runs; label churn-rate logged is sane; docstring == code.
- **Depends on:** none.

## Task 18: Poll `/health` in `StatusPill`
- **File:** `frontend/components/StatusPill.tsx`
- **Category:** Frontend UX · **Severity:** Minor
- **Finding:** health checked once; a cold-start "waking up" reading never corrects.
- **Proposed change:** `setInterval` (15–30s) re-checking until `ok`, reusing the existing `cancelled` cleanup; clear on `ok`/unmount.
- **Verification:** `npm run build`; manual: load during a cold start, confirm the pill flips to "live" within an interval.
- **Depends on:** none.

## Task 19: Complete the ARIA tabs pattern
- **File:** `frontend/app/page.tsx:38-56`
- **Category:** Accessibility · **Severity:** Minor
- **Finding:** no `aria-controls`/panel `id`/`aria-labelledby`; no arrow-key roving tabindex.
- **Proposed change:** pair each tab (`id="tab-{id}"`, `aria-controls="panel-{id}"`) with the panel (`id="panel-{id}"`, `aria-labelledby="tab-{id}"`); add a keydown handler for Left/Right/Home/End over the tab list with roving `tabIndex`.
- **Verification:** `npm run build`; keyboard: arrow keys move between tabs; screen-reader announces tab↔panel.
- **Depends on:** none.

## Task 20: Bound `/materialization-log?limit=`
- **File:** `serving/main.py:310`
- **Category:** Performance / DoS surface · **Severity:** Minor
- **Proposed change:** `limit: int = Query(default=50, ge=1, le=1000)`.
- **Verification:** `pytest tests/test_serving.py -k limit` — `?limit=100000` → 422.
- **Depends on:** none.

## Task 21: Tighten CORS + protect the write-triggering endpoint (optional hardening)
- **File:** `serving/main.py`
- **Category:** Security · **Severity:** Minor
- **Finding:** CORS `*`, no auth on any route including state-writing `/skew-report`.
- **Proposed change:** set `ALLOWED_ORIGINS` to the Vercel domain(s) in the Cloud Run env (already env-driven — no code change needed), and optionally add a shared-secret header check on `/skew-report` (and any future write endpoint). Keep GET read endpoints open (public demo).
- **Verification:** browser fetch from the Vercel domain still works; a fetch from another origin is blocked by CORS.
- **Depends on:** Task 1 (both touch `/skew-report`).

## Task 22: Preserve input order in the Training Pull results table
- **File:** `frontend/components/TrainingPull.tsx:126`
- **Category:** Frontend (note) · **Severity:** Minor
- **Proposed change:** iterate the parsed input `ids` and look up `data.results[String(id)]` instead of `Object.entries(data.results)` (which reorders numeric-string keys ascending).
- **Verification:** `npm run build`; input `5, 1, 3` renders rows in that order.
- **Depends on:** none.

---

## Not scheduled (Notes — track, don't necessarily fix now)
- `datetime.utcnow()` is deprecated (Python 3.12+); migrate to `datetime.now(timezone.utc)` before a Python upgrade removes it. No current tz bug (all datetimes are naive/UTC-consistent).
- `validator.py` `numerical_cols` duplicates `features.py::FEATURE_COLS` — import it to prevent silent drift when a feature is added.
- `frontend/lib/format.ts` has no unit test and there's no JS test runner wired in CI — YAGNI unless the frontend becomes a review focus.
- `ci.yml` coverage gate (52%) is padded by trivial modules — raise it only alongside Task 16.
