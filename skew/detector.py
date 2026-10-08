"""
skew/detector.py
================
Training-serving skew detection using KS test per feature.

What is training-serving skew?
  The features used to train the model are computed differently (or at a
  different time) from the features used at serving time. The model sees
  a different input distribution than it was trained on → silent degradation.

This module:
  1. Reads training-time feature distributions from DuckDB skew_snapshots
  2. Samples current serving-time features from DuckDB feature_history
  3. Runs a KS (Kolmogorov-Smirnov) test per feature
  4. Flags features where KS p-value < 0.05 (statistically significant skew)
  5. Writes the serving-time snapshot to skew_snapshots for trend analysis

The /skew-report API endpoint calls compute_skew_report().
The frontend Skew tab reads this endpoint and renders histograms + KS results.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import structlog
from scipy import stats

from feature_store.connections import get_duckdb_client
from feature_store.features import FEATURE_COLS

log = structlog.get_logger()

KS_THRESHOLD = 0.05  # p-value below which we flag skew

# Standardised mean difference above which a feature is flagged.
#
# This, not the KS p-value, is what decides `flagged`. Only summary moments are
# stored, so the KS test below runs on pseudo-samples drawn from
# Normal(mean, std) with a fixed seed -- its p-value is a deterministic function
# of (mean, std, n) and carries no information about the real distribution
# shape. Worse, it scales with the pseudo-sample size rather than with the
# evidence, so a large n drives p toward 0 and would flag every feature.
# A standardised mean difference is a statistic the stored moments genuinely
# support. 0.25 is a conventional "small but real" effect size.
MEAN_SHIFT_THRESHOLD = 0.25

# Days of recent serving data to sample, counted back from the newest row in
# feature_history rather than from wall-clock now.
#
# Counting back from now was the bug. A static offline store baked into an image
# has rows no newer than the build, so a one-day window from "now" selects
# nothing, avg() returns NULL, the insert guard below skips every feature, and
# /skew-report returns {"report": []} -- a blank dashboard tab that reads as a
# broken endpoint. The code comment here used to say the window was
# "env-configurable so the demo can widen it", but SERVING_SAMPLE_DAYS appeared
# nowhere except its own definition and single use: not in the Dockerfile, not in
# any workflow, not in .env.example, not in configs/. The documented mitigation
# was never wired up, so the endpoint was empty out of the box.
#
# Anchoring to max(event_time) makes the window mean "the most recent serving
# data that exists", which is what it was always supposed to mean, and gives the
# same answer on a live store where the newest row IS roughly now.
SERVING_SAMPLE_DAYS = int(os.getenv("SERVING_SAMPLE_DAYS", "1"))


# ---------------------------------------------------------------------------
# Snapshot capture
# ---------------------------------------------------------------------------


def _capture_serving_snapshot(client, feature_version: str) -> str:
    """
    Compute distribution stats for the most recent serving-time features
    and write to skew_snapshots. Returns the snapshot_id.
    """
    snapshot_id = str(uuid.uuid4())[:12]
    newest = client.execute(
        "SELECT max(event_time) FROM feature_history WHERE feature_version = $version",
        {"version": feature_version},
    )
    latest_row = newest[0][0] if newest else None
    anchor = latest_row if latest_row is not None else datetime.utcnow()
    since = anchor - timedelta(days=SERVING_SAMPLE_DAYS)
    now = datetime.utcnow()

    for col in FEATURE_COLS:
        result = client.execute(
            f"""
            SELECT
                avg({col})              AS mean,
                stddev_pop({col})       AS std,
                quantile_cont({col}, 0.25) AS p25,
                quantile_cont({col}, 0.50) AS p50,
                quantile_cont({col}, 0.75) AS p75,
                quantile_cont({col}, 0.95) AS p95,
                (count(*) FILTER (WHERE {col} IS NULL OR isnan({col})))
                    / greatest(count(*), 1) AS null_rate,
                count(*)                AS sample_count
            FROM feature_history
            WHERE feature_version = $version
              AND event_time >= $since
            """,
            {"version": feature_version, "since": since},
        )
        if result and result[0][0] is not None:
            mean, std, p25, p50, p75, p95, null_rate, n = result[0]
            client.execute(
                """
                INSERT INTO skew_snapshots
                (snapshot_id, feature_name, feature_version, context,
                 mean, std, p25, p50, p75, p95, null_rate, sample_count, captured_at)
                VALUES
                ($snapshot_id, $feature_name, $feature_version, $context,
                 $mean, $std, $p25, $p50, $p75, $p95, $null_rate, $sample_count, $captured_at)
                """,
                {
                    "snapshot_id": snapshot_id,
                    "feature_name": col,
                    "feature_version": feature_version,
                    "context": "serving",
                    "mean": float(mean or 0),
                    "std": float(std or 0),
                    "p25": float(p25 or 0),
                    "p50": float(p50 or 0),
                    "p75": float(p75 or 0),
                    "p95": float(p95 or 0),
                    "null_rate": float(null_rate or 0),
                    "sample_count": int(n or 0),
                    "captured_at": now,
                },
            )
    return snapshot_id


# ---------------------------------------------------------------------------
# KS test
# ---------------------------------------------------------------------------


def _run_ks_test(
    training_stats: dict,
    serving_stats: dict,
    feature_name: str,
) -> dict[str, Any]:
    """
    Approximate a KS test using the summary statistics available.
    We reconstruct pseudo-samples using a normal approximation:
      sample ~ Normal(mean, std)
    Then run scipy.stats.ks_2samp on the pseudo-samples.

    For production, you would store actual sample values; here we use
    the statistical moments which is sufficient for detecting large skew.
    """
    tr = training_stats
    sv = serving_stats

    n_sample = min(tr.get("sample_count", 500), sv.get("sample_count", 500), 1000)
    n_sample = max(n_sample, 50)

    rng = np.random.default_rng(seed=42)
    # Use truncated normal to avoid negative values for count/spend features
    tr_samples = np.clip(
        rng.normal(tr["mean"], max(tr["std"], 1e-6), n_sample), 0, None
    )
    sv_samples = np.clip(
        rng.normal(sv["mean"], max(sv["std"], 1e-6), n_sample), 0, None
    )

    ks_stat, ks_pvalue = stats.ks_2samp(tr_samples, sv_samples)

    mean_shift = abs(tr["mean"] - sv["mean"]) / max(tr["std"], 1e-6)
    # bool(...) unwraps numpy.bool_ — FastAPI's JSON encoder can't serialize the
    # numpy scalar that scipy's comparison returns, which 500s /skew-report.
    flagged = bool(mean_shift >= MEAN_SHIFT_THRESHOLD)

    return {
        "feature_name": feature_name,
        "training_mean": round(tr["mean"], 4),
        "training_std": round(tr["std"], 4),
        "training_p50": round(tr["p50"], 4),
        "serving_mean": round(sv["mean"], 4),
        "serving_std": round(sv["std"], 4),
        "serving_p50": round(sv["p50"], 4),
        "mean_shift": round(float(mean_shift), 4),
        "ks_statistic": round(float(ks_stat), 4),
        "ks_pvalue": round(float(ks_pvalue), 4),
        # Both ks_* values are computed on a normal approximation of the stored
        # moments, not on the observed values. Reported for continuity, not
        # relied on: see MEAN_SHIFT_THRESHOLD.
        "ks_is_approximation": True,
        "flagged": flagged,
        "flag_basis": "standardised mean difference",
        "training_sample_count": tr.get("sample_count", 0),
        "serving_sample_count": sv.get("sample_count", 0),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_skew_report(feature_version: str = "v1") -> list[dict[str, Any]]:
    """
    Compute a full skew report comparing training vs serving distributions.

    Steps:
    1. Capture current serving-time snapshot
    2. Load latest training-time snapshot from DuckDB
    3. Run KS test per feature
    4. Return sorted report (flagged features first)
    """
    client = get_duckdb_client()
    log.info("computing_skew_report", version=feature_version)

    # ── 1. Capture fresh serving snapshot ────────────────────────────────────
    serving_snapshot_id = _capture_serving_snapshot(client, feature_version)

    # ── 2. Load latest training snapshot ────────────────────────────────────
    tr_rows = client.execute(
        """
        SELECT
            feature_name, mean, std, p25, p50, p75, p95,
            null_rate, sample_count
        FROM skew_snapshots
        WHERE feature_version = $version
          AND context = 'training'
        QUALIFY row_number() OVER (PARTITION BY feature_name ORDER BY captured_at DESC) = 1
        """,
        {"version": feature_version},
    )

    # ── 3. Load serving snapshot just captured ───────────────────────────────
    sv_rows = client.execute(
        """
        SELECT
            feature_name, mean, std, p25, p50, p75, p95,
            null_rate, sample_count
        FROM skew_snapshots
        WHERE snapshot_id = $sid
        """,
        {"sid": serving_snapshot_id},
    )

    cols = [
        "feature_name",
        "mean",
        "std",
        "p25",
        "p50",
        "p75",
        "p95",
        "null_rate",
        "sample_count",
    ]
    tr_map = {row[0]: dict(zip(cols, row)) for row in tr_rows}
    sv_map = {row[0]: dict(zip(cols, row)) for row in sv_rows}

    if not tr_map:
        log.warning("no_training_snapshot_found", hint="Run training/train.py first")
        return []

    # ── 4. KS test per feature ───────────────────────────────────────────────
    report = []
    for feature_name in FEATURE_COLS:
        if feature_name not in tr_map or feature_name not in sv_map:
            continue
        result = _run_ks_test(tr_map[feature_name], sv_map[feature_name], feature_name)
        report.append(result)

    # Sort: flagged first, then by KS statistic descending
    report.sort(key=lambda x: (-int(x["flagged"]), -x["ks_statistic"]))

    n_flagged = sum(1 for r in report if r["flagged"])
    log.info("skew_report_complete", features=len(report), flagged=n_flagged)
    return report
