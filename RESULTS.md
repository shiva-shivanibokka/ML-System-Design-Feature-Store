# Independent review and fixes (branch `sop-eval`)

An adversarial review of this repository found that three of its four headline
claims were falsifiable in under a minute, two of them by running the commands
the README itself gives. This file records what was wrong, how each defect was
reproduced, and what was changed. Every number below was measured on this
machine after the fix; nothing is estimated.

Environment: Windows 11, Python 3.12, DuckDB 1.5.4, a `valkey/valkey:8-alpine`
container as the online store. No cloud account was used and nothing was paid
for. The deployed Cloud Run API returns HTTP 503 (its billing account is closed)
and the Aiven Valkey instance is gone, so every measurement here is local.

---

## 1. The two serving paths returned different values for the same entity

**The claim.** README: feature values at training and serving time are
*"impossible to drift apart by construction"*, because every feature comes from
one SQL definition.

**What actually happened.** Same entity, two paths, seconds apart:

```
$ curl -s localhost:8813/features/1                      # online store
  txn_count_30d 2.0   total_spend_90d 84.73   days_since_last_txn 15.0   account_age_days 58.0
$ docker exec fs-valkey valkey-cli DEL "entity:user:1"    # force the fallback
$ curl -s localhost:8813/features/1                      # on-demand, SAME entity
  txn_count_30d 0.0   total_spend_90d  0.00   days_since_last_txn 101.0  account_age_days 144.0
```

**8 of 13 features differed**, five of them by 100%.

**Why.** Sharing one SQL body guarantees identical *transformations*, not
identical *values*. `compute_on_demand` computed as of `datetime.utcnow()`, while
the online store returns whatever was materialized from the newest row in
`feature_history` — months older. The response exposed no timestamp, so a
consumer could not tell which answer it had received.

**Fix.** `feature_store/features.py::serving_snapshot_time` anchors the on-demand
path to `max(event_time)` in `feature_history`, so both paths answer as of the
same instant. It falls back to `utcnow()` only when the table is empty, where
there is nothing to agree with.

**After:**

```
warm: online_store | cold: on_demand
DIVERGENCE: 0 of 13 features differ
```

**Test.** `test_on_demand_matches_stored_features` previously selected
`txn_count_30d` and then asserted only on `plan_encoded` — the one feature that is
time-invariant and therefore cannot diverge. The test named for the central claim
was written so it could not fail. It now asserts every column in `FEATURE_COLS`
and names the mismatches when it fails.

---

## 2. `training/train.py` could not run, so the advertised ROC-AUC was unreproducible

**The claim.** README: *"trained model reaches ROC-AUC ≈ 0.98 … a built-in
leakage demo shows a naive join inflating AUC to ~1.0 before it collapses"*, and
*"All figures below are reproducible from this repo — no invented benchmarks."*

**What actually happened.** Two independent blockers.

*First*, the label windows were anchored to `datetime.utcnow()` while the
committed data was a fixed snapshot. The "before" window had walked off the end
of the data, `HAVING txns_before > 0` matched nothing, and the script died
downstream with `KeyError: 'entity_id'` on an empty frame.

*Second*, even with that fixed, a clean install could not import mlflow at all:

```
ModuleNotFoundError: No module named 'pkg_resources'
  mlflow/utils/requirements_utils.py:20: import pkg_resources
```

`mlflow==2.13.0` imports `pkg_resources`, which setuptools removed in version 81.
`requirements.txt` pinned neither, so `training/train.py` failed at line 29 on any
current machine regardless of the data.

**Fixes.** Labels are anchored to `max(event_time)` in `raw_transactions`, and the
anchor is logged. `get_training_dataset([])` returns a typed empty frame instead
of a bare `DataFrame()` whose missing `entity_id` column caused the `KeyError`.
`setuptools<81` is pinned with the reason recorded.

**Then a third problem appeared, which is why the 0.98 was never honest.** With
the script finally running, the churn label had 3 positives in 244 rows and the
model scored a meaningless AUC of 1.0. `data/generate.py` contained **no churn
logic at all** — the word does not appear in it. Churn was an accident of random
transaction timing, so in any 30-day window almost every user had transacted.

The generator now produces churners deliberately: `CHURN_FRACTION = 0.18` of
users go quiet 32–70 days back and never return. The cutoff must sit outside the
30-day window the label inspects, or a "churner" still transacts inside it and is
labelled active — the first attempt put it at 5–40 days and produced a 3.1%
positive rate. The `txns_before` threshold was also returned from 5 to 2, because
at 5 successful transactions in a 30-day window almost no free or basic user
qualifies (their generated rate is 1.5–4 per 30 days).

**Measured after the fix**, 1,500 users, 31 snapshots at 3-day intervals:

| | value |
|---|---|
| labelled users | 1,202 |
| churn rate | 11.9% |
| **PIT-correct model ROC-AUC** | **0.747** (avg precision 0.313, F1 0.308) |
| **leaky naive-join ROC-AUC** | **0.990** |

The leakage demo now demonstrates leakage: the leaky number is better precisely
because it has seen data that would not exist at serving time. The README's
0.98 is gone. Note that the exact figure moves between regenerations — runs
during this work ranged 0.747–0.854 — because `data/generate.py` anchors its
window to the current date. That variation is stated in the README rather than
hidden by quoting the best run.

---

## 3. `materialization/backfill.py` failed on every snapshot

Found by running it, not by reading it:

```
$ python materialization/backfill.py --days 90 --interval-hours 24
snapshot_failed  error=Binder Error: There are no UNIQUE/PRIMARY KEY constraints
                 that refer to this table, specify ON CONFLICT columns manually
backfill_complete  failed=91  succeeded=0  total=91
```

`configs/schema.sql` declares `PRIMARY KEY (entity_id, feature_version,
event_time)` on `feature_history`, but every `CREATE` there is `IF NOT EXISTS`.
The committed database was created before that key was added, so the declaration
never ran again and the shipped table had no primary key — which `INSERT OR
IGNORE` needs as a conflict target. The headline backfill command did not work at
all on the shipped artifact.

`AUDIT.md` flagged this and a later review dismissed the flag as stale on the
grounds that the key is present in `schema.sql`. The audit was right about the
database; `schema.sql` was right about the intent; they had simply never met.

**Fix.** `feature_store/schema.py` detects a `feature_history` without its primary
key and rebuilds the table, `DISTINCT ON` the key so pre-existing duplicates
cannot abort the migration half-done. Anyone holding an old database is repaired
the next time any entry point calls `apply_schema`. After: `backfill_complete
failed=0 succeeded=31 total=31`.

---

## 4. 284 committed rows violated the repository's own schema

**The claim.** README: *"every feature write is validated (types, null-ability,
value ranges) before it lands"*.

```sql
SELECT count(*), min(account_age_days) FROM feature_history WHERE account_age_days < 0;
-- (284, -19.0)
```

`feature_store/validator.py:58` declares `Check.ge(0)` on `account_age_days`.
Pandera ran in `materialize.py` (the offline→online hop) and in
`compute_on_demand`, but **not** in `compute_and_store` — the primary offline
write, and the one `backfill.py` uses. Users could sign up as recently as the day
before while backfill walked back 90 days, so snapshots predating a user's signup
produced negative ages, which reached training data through the ASOF join but can
never occur at serving time. The system was manufacturing training/serving skew,
and defect 5 below meant the skew detector could not see it.

**Fixes.** The feature SQL now carries `WHERE u.signup_date <= $snapshot`: a user
who had not signed up yet has no features at that snapshot. This is the
point-in-time-correct answer rather than a clamp — `greatest(age, 0)` would keep
inventing a user who was not there. `generate_users` also draws
`signup_days_ago` from `[days + 1, days * 3]` so the condition holds by
construction. `compute_and_store` now validates what it wrote.

**After:** `negative account_age_days: 0`, and validation passes on all 31
backfilled snapshots.

---

## 5. `/skew-report` returned an empty report, and could not have detected skew

```
$ curl -s localhost:8812/skew-report
{"feature_version":"v1","report":[]}
```

Three distinct defects:

**(i) Why it was empty.** `SERVING_SAMPLE_DAYS` defaults to 1 and the window was
counted back from wall-clock now. A static offline store has no rows newer than
its build, so a one-day window selected nothing, `avg()` returned NULL, the insert
guard skipped every feature, and the report came back empty. The code comment
claimed the variable was *"env-configurable so the demo can widen the window"* —
but it appeared nowhere except its own definition and single use. Not in the
Dockerfile, not in any workflow, not in `.env.example`, not in `configs/`. The
documented mitigation was never wired up. The window is now counted back from
`max(event_time)`, which is what it always meant and gives the same answer on a
live store. **After: 13 features returned, 0 flagged.**

**(ii) The dashboard misdiagnosed it.** `SkewReport.tsx` said *"run the training
workflow to capture a snapshot"*. A training snapshot already existed; the
serving side was the empty one. The message now names both prerequisites and
points at the real cause.

**(iii) It was not a Kolmogorov–Smirnov test on serving data.** README: *"a
per-feature Kolmogorov–Smirnov test comparing the training-time distribution
against live serving"*. False on both nouns. Only summary moments are stored, so
`_run_ks_test` drew pseudo-samples from `Normal(mean, std)` with a fixed seed and
tested those — the p-value is a deterministic function of `(mean, std, n)` and
carries no information about the real distribution shape. Worse, it scales with
the pseudo-sample size rather than the evidence, so a large `n` drives p toward 0
and would flag everything. Both sides also read `feature_history`, so no served
request was ever observed.

`flagged` is now decided by the **standardised mean difference**, a statistic the
stored moments genuinely support, against `MEAN_SHIFT_THRESHOLD = 0.25`. The KS
values are still reported for continuity but carry `ks_is_approximation: True`,
and each row states its `flag_basis`. The README claim was corrected rather than
the statistic dressed up.

---

## 6. `open_tickets` leaked future information into historical features

The one feature where point-in-time correctness actually matters, and it was
wrong. `raw_support_tickets` had a `resolved` flag and no resolution timestamp,
so the feature filtered `WHERE resolved = 0 AND event_time < $snapshot`: the
`event_time` gate was point-in-time, but `resolved` was read at its *current*
value. A ticket open at the snapshot but resolved afterwards was counted as
closed retroactively. `open_tickets` is a declared `model_input`.

Reproduced with a failing test first — a ticket raised 10 days before the
snapshot and resolved 20 days after it:

```
AssertionError: expected 2 open tickets as of 2026-01-01 00:00:00, got 1.0
  -- a resolution dated after the snapshot leaked backwards
```

**Fix.** `resolved_at TIMESTAMP` added to the schema and the generator; the
feature now asks `event_time < $snapshot AND (resolved_at IS NULL OR resolved_at
>= $snapshot)`. A new helper `compute_on_demand_at` takes an explicit as-of time
so point-in-time behaviour can be tested at a named instant.

**The test was proven to discriminate** by reverting the fix in place and
confirming it goes red (`got 1.0`), then restoring it and confirming green. A
regression test that has never been seen to fail is not evidence.

---

## 7. Claims corrected in the README

| Claim | Measured |
|---|---|
| "45 automated tests pass" | 48 |
| "ROC-AUC ≈ 0.98" | 0.747 |
| "naive join inflating AUC to ~1.0 before it collapses" | leaky 0.990 vs honest 0.747; the collapse was never computed anywhere in the code |
| "p50 ≈40 ms online against ≈99 ms on demand … roughly halves p50" | measured locally over 60 requests: **p50 2.21 ms vs 68.40 ms**, p95 3.05 vs 73.78 — ~31×, not ~2× |
| "**Live:** … all deployed and serving real data" | Cloud Run returns 503; Aiven Valkey is gone |
| trial "ends around 19 September 2026 … the service is stopped" (future tense) | that date has passed; rewritten in the past tense |
| `materialize.yml` "cron 6h" / "every 6h" | the workflow is `0 6 * * *` — daily |
| "Cloud Run keep-warm" | the job was renamed `health-check`; its own comment says a scheduled ping never kept anything warm |
| deploy instructions set `MOTHERDUCK_TOKEN` | `materialize.yml` warns it **must stay unset**, because `connections.py` switches to a dead MotherDuck whenever it is non-empty. Following the README broke a clean deploy. Now an explicit warning. |
| `data/generate.py` docstring: "10,000 user profiles / ~1.2M transactions" | the committed store held 300 users / 4,356 transactions; it now holds 1,500 / 17,535, and the docstring says so |

`AUDIT.md` is linked from the README as "a full self-audit". It is dated
2026-07-12, its scorecard is entirely red, and several of its figures are stale.
It now opens with a warning that it is a historical snapshot — and credits the one
finding it got right that a later reviewer got backwards (defect 3).

---

## 8. Other changes

- **Coverage gate was theatre.** `ci.yml` set `--cov-fail-under=52` against a real
  86%, so roughly 34 points of regression could land green. Raised to 80.
- **The schema loader split on `;` naively**, so a semicolon inside a SQL comment
  cut a `CREATE TABLE` in half and DuckDB reported "syntax error at end of input"
  pointing at a statement that looked complete. Found by writing a comment that
  contained one. `_split_statements` now strips line comments first.
- **Quickstart corrected.** It now starts the Valkey container, gives the exact
  flags the committed store was built with, warns that `data/generate.py`
  rewrites the tracked DuckDB file, and notes that DuckDB is single-writer so the
  server must be stopped before materializing.

## Known limitations, stated rather than fixed

- **The on-demand "cold start" path is close to unreachable in a real
  deployment.** Every entity is materialized and the TTL is refreshed daily, and
  an entity absent from `raw_users` returns 404 rather than on-demand features.
  It is reachable only during a TTL gap or a Valkey outage; forcing it here
  required deleting a key by hand.
- **The response carries no `event_time`.** A caller still cannot tell how old
  the snapshot it received is. Both paths now agree, so this is a documentation
  gap rather than a correctness one, but it should be added.
- **`GET /skew-report` mutates state**, inserting snapshot rows on an
  unauthenticated GET. The 300-second cache bounds it; the REST semantics are
  still wrong.
- **This is demo-scale data.** 1,500 synthetic users is enough to make the
  pipeline honest and reproducible, not enough for the AUC to be a stable
  benchmark.

## Verification

```
48 passed                       (pytest tests/)
ruff check  — All checks passed!
ruff format — 27 files already formatted
coverage    — 86%
backfill    — failed=0 succeeded=31 total=31
materialize — processed=1500 failed=0 validation_failures=0
endpoints   — /health /registry /skew-report /metrics /lineage/{f} /materialization-log all 200
divergence  — 0 of 13 features differ between the two serving paths
```
