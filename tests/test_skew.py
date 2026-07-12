"""
tests/test_skew.py
==================
Unit tests for the skew detection KS test logic.
Runs without ClickHouse — tests the statistical comparison directly.
"""

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from feature_store.connections import _DuckClient
from feature_store.schema import apply_schema
from skew import detector
from skew.detector import _run_ks_test, compute_skew_report


class TestKSTest:
    def _make_stats(self, mean: float, std: float, n: int = 500) -> dict:
        return {
            "mean": mean,
            "std": std,
            "p25": mean - 0.7 * std,
            "p50": mean,
            "p75": mean + 0.7 * std,
            "p95": mean + 1.6 * std,
            "null_rate": 0.0,
            "sample_count": n,
        }

    def test_identical_distributions_not_flagged(self):
        stats = self._make_stats(10.0, 2.0)
        result = _run_ks_test(stats, stats, "test_feature")
        assert not result["flagged"]
        assert result["ks_pvalue"] >= 0.05

    def test_large_mean_shift_flagged(self):
        """Distributions with very different means should be flagged."""
        tr_stats = self._make_stats(10.0, 2.0)
        sv_stats = self._make_stats(50.0, 2.0)  # 20-sigma shift
        result = _run_ks_test(tr_stats, sv_stats, "test_feature")
        assert result["flagged"]
        assert result["ks_pvalue"] < 0.05

    def test_similar_distributions_not_flagged(self):
        """Small shifts within noise should not be flagged."""
        tr_stats = self._make_stats(10.0, 2.0)
        sv_stats = self._make_stats(10.1, 2.0)  # tiny shift
        result = _run_ks_test(tr_stats, sv_stats, "test_feature")
        # p-value should be high (not flagged)
        assert result["ks_pvalue"] >= 0.05

    def test_result_has_required_fields(self):
        stats = self._make_stats(5.0, 1.0)
        result = _run_ks_test(stats, stats, "my_feature")
        required = [
            "feature_name",
            "training_mean",
            "serving_mean",
            "mean_shift",
            "ks_statistic",
            "ks_pvalue",
            "flagged",
        ]
        for field in required:
            assert field in result, f"Missing field: {field}"

    def test_zero_std_handled(self):
        """Features with zero variance (constant) should not crash."""
        stats = self._make_stats(0.0, 0.0)
        result = _run_ks_test(stats, stats, "constant_feature")
        assert isinstance(result["ks_statistic"], float)

    def test_result_is_json_serializable(self):
        """flagged must be a builtin bool, not numpy.bool_ — FastAPI's JSON
        encoder cannot serialize numpy scalars, which 500s /skew-report."""
        import json

        result = _run_ks_test(
            self._make_stats(5.0, 1.0), self._make_stats(50.0, 1.0), "f"
        )
        # numpy.bool_ is NOT a subclass of bool, so isinstance(..., bool) is False
        # for a leaked numpy scalar — exactly what this guards against.
        assert isinstance(result["flagged"], bool)
        json.dumps(result)  # raises if any numpy scalar leaked in


def test_empty_window_excludes_feature_instead_of_fabricating(monkeypatch):
    """No recent feature_history rows -> the serving snapshot must skip the
    feature rather than write a fake mean=0/std=0 row that gets KS-tested
    against a real training baseline and flagged as skew."""
    client = _DuckClient(duckdb.connect(":memory:"))
    apply_schema(client)
    monkeypatch.setattr(detector, "get_duckdb_client", lambda: client)

    # Seed only a training snapshot for one feature; feature_history stays empty
    # so the serving window has zero rows for every feature.
    client.execute(
        """
        INSERT INTO skew_snapshots
        (snapshot_id, feature_name, feature_version, context,
         mean, std, p25, p50, p75, p95, null_rate, sample_count, captured_at)
        VALUES
        ($sid, 'txn_count_7d', 'v1', 'training', 10, 2, 8, 10, 12, 14, 0.0, 500, $now)
        """,
        {"sid": str(uuid.uuid4())[:12], "now": datetime(2024, 1, 1)},
    )

    report = compute_skew_report(feature_version="v1")

    assert report == []
    # Confirm nothing fabricated for the empty window either.
    rows = client.execute(
        "SELECT count(*) FROM skew_snapshots WHERE context = 'serving'"
    )
    assert rows[0][0] == 0


def test_compute_skew_report_end_to_end_with_seeded_snapshots(monkeypatch):
    """Seed a training snapshot directly, and seed feature_history so the
    fresh serving-snapshot capture has real rows to aggregate. End-to-end:
    capture -> load training -> KS test -> report."""
    client = _DuckClient(duckdb.connect(":memory:"))
    apply_schema(client)
    monkeypatch.setattr(detector, "get_duckdb_client", lambda: client)

    client.execute(
        """
        INSERT INTO skew_snapshots
        (snapshot_id, feature_name, feature_version, context,
         mean, std, p25, p50, p75, p95, null_rate, sample_count, captured_at)
        VALUES
        ($sid, 'txn_count_7d', 'v1', 'training', 10, 2, 8, 10, 12, 14, 0.0, 500, $now)
        """,
        {"sid": str(uuid.uuid4())[:12], "now": datetime(2024, 1, 1)},
    )

    now = datetime.utcnow()
    cols = [
        "entity_id",
        "entity_type",
        "feature_version",
        "event_time",
        "txn_count_7d",
        "txn_count_30d",
        "txn_count_90d",
        "total_spend_7d",
        "total_spend_30d",
        "total_spend_90d",
        "avg_txn_amount_30d",
        "failed_txn_rate_30d",
        "days_since_last_txn",
        "open_tickets",
        "ticket_rate_30d",
        "account_age_days",
        "plan_encoded",
        "computed_at",
    ]
    rows = [
        (
            i,
            "user",
            "v1",
            now - timedelta(hours=1),
            10.0,
            10.0,
            10.0,
            10.0,
            10.0,
            10.0,
            10.0,
            0.0,
            1.0,
            0.0,
            0.0,
            10.0,
            2.0,
            now,
        )
        for i in range(1, 6)
    ]
    client.register("h", pd.DataFrame(rows, columns=cols))
    client.execute("INSERT INTO feature_history SELECT * FROM h")

    report = compute_skew_report(feature_version="v1")

    assert len(report) == 1
    result = report[0]
    assert result["feature_name"] == "txn_count_7d"
    assert result["serving_mean"] == 10.0
    assert result["training_mean"] == 10.0
    assert result["serving_sample_count"] == 5
