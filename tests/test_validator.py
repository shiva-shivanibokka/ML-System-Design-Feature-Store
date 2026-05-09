"""
tests/test_validator.py
=======================
Unit tests for the Pandera feature schema validator.
These tests run without ClickHouse or Redis — pure logic tests.
"""

import pandas as pd
import pytest
import pandera.errors

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from feature_store.validator import validate_feature_batch, validate_single_entity


def _valid_row(**overrides) -> dict:
    base = {
        "entity_id": 1,
        "txn_count_7d": 3.0,
        "txn_count_30d": 12.0,
        "txn_count_90d": 35.0,
        "total_spend_7d": 45.0,
        "total_spend_30d": 180.0,
        "total_spend_90d": 520.0,
        "avg_txn_amount_30d": 15.0,
        "failed_txn_rate_30d": 0.05,
        "days_since_last_txn": 2.0,
        "open_tickets": 0.0,
        "ticket_rate_30d": 1.0,
        "account_age_days": 365.0,
        "plan_encoded": 1.0,
    }
    base.update(overrides)
    return base


class TestValidateFeatureBatch:
    def test_valid_batch_passes(self):
        df = pd.DataFrame([_valid_row(), _valid_row(entity_id=2, txn_count_7d=5.0)])
        validated = validate_feature_batch(df)
        assert len(validated) == 2

    def test_nan_filled_to_zero(self):
        """Users with no transactions produce NaN — should be coerced to 0."""
        row = _valid_row(txn_count_7d=float("nan"), avg_txn_amount_30d=float("nan"))
        df = pd.DataFrame([row])
        validated = validate_feature_batch(df)
        assert validated.iloc[0]["txn_count_7d"] == 0.0
        assert validated.iloc[0]["avg_txn_amount_30d"] == 0.0

    def test_failed_rate_capped_at_one(self):
        """ClickHouse division rounding can produce values slightly above 1.0."""
        row = _valid_row(failed_txn_rate_30d=1.0001)
        df = pd.DataFrame([row])
        validated = validate_feature_batch(df)
        assert validated.iloc[0]["failed_txn_rate_30d"] <= 1.0

    def test_negative_txn_count_fails(self):
        row = _valid_row(txn_count_7d=-1.0)
        df = pd.DataFrame([row])
        with pytest.raises(Exception):
            validate_feature_batch(df)

    def test_failed_rate_above_one_fails(self):
        row = _valid_row(failed_txn_rate_30d=1.5)
        df = pd.DataFrame([row])
        with pytest.raises(Exception):
            validate_feature_batch(df)

    def test_invalid_plan_encoded_fails(self):
        row = _valid_row(plan_encoded=5.0)  # Only 0,1,2,3 are valid
        df = pd.DataFrame([row])
        with pytest.raises(Exception):
            validate_feature_batch(df)

    def test_extra_columns_allowed(self):
        """strict=False means extra columns (event_time etc.) don't fail validation."""
        row = _valid_row()
        row["event_time"] = "2024-01-01"
        row["feature_version"] = "v1"
        df = pd.DataFrame([row])
        validated = validate_feature_batch(df)
        assert len(validated) == 1


class TestValidateSingleEntity:
    def test_valid_entity_passes(self):
        features = {k: v for k, v in _valid_row().items() if k != "entity_id"}
        result = validate_single_entity(features)
        assert isinstance(result, dict)
        assert "txn_count_30d" in result

    def test_nan_coerced(self):
        features = {k: v for k, v in _valid_row().items() if k != "entity_id"}
        features["txn_count_7d"] = float("nan")
        result = validate_single_entity(features)
        assert result["txn_count_7d"] == 0.0
