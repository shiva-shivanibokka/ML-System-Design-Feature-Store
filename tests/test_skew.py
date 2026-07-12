"""
tests/test_skew.py
==================
Unit tests for the skew detection KS test logic.
Runs without ClickHouse — tests the statistical comparison directly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from skew.detector import _run_ks_test


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

        result = _run_ks_test(self._make_stats(5.0, 1.0), self._make_stats(50.0, 1.0), "f")
        assert type(result["flagged"]) is bool
        json.dumps(result)  # raises if any numpy scalar leaked in
