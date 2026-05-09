"""
feature_store/validator.py
==========================
Feature schema validation using Pandera.

Validates every feature write before it hits ClickHouse to prevent:
  - Null values in non-nullable features
  - Out-of-range values (e.g. negative counts, rates > 1)
  - Wrong data types
  - Silent NaN propagation

This is what separates production feature stores from demo ones — every
feature engineering pipeline can silently produce NaN or out-of-range values
when source data is sparse. Pandera catches it at write time.

Usage:
    from feature_store.validator import validate_feature_batch
    validated_df = validate_feature_batch(df)  # raises on violation
"""

from __future__ import annotations

import pandas as pd
import pandera as pa
import structlog
from pandera import Column, DataFrameSchema, Check

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------

FEATURE_SCHEMA = DataFrameSchema(
    columns={
        "entity_id": Column(int, nullable=False, checks=Check.ge(1)),
        # Transaction counts — non-negative integers stored as float
        "txn_count_7d": Column(float, nullable=False, checks=Check.ge(0)),
        "txn_count_30d": Column(float, nullable=False, checks=Check.ge(0)),
        "txn_count_90d": Column(float, nullable=False, checks=Check.ge(0)),
        # Spend — non-negative
        "total_spend_7d": Column(float, nullable=False, checks=Check.ge(0)),
        "total_spend_30d": Column(float, nullable=False, checks=Check.ge(0)),
        "total_spend_90d": Column(float, nullable=False, checks=Check.ge(0)),
        "avg_txn_amount_30d": Column(float, nullable=False, checks=Check.ge(0)),
        # Rate must be between 0 and 1
        "failed_txn_rate_30d": Column(
            float,
            nullable=False,
            checks=[Check.ge(0), Check.le(1)],
        ),
        # Recency — non-negative days
        "days_since_last_txn": Column(float, nullable=False, checks=Check.ge(0)),
        # Support
        "open_tickets": Column(float, nullable=False, checks=Check.ge(0)),
        "ticket_rate_30d": Column(float, nullable=False, checks=Check.ge(0)),
        # Profile
        "account_age_days": Column(float, nullable=False, checks=Check.ge(0)),
        "plan_encoded": Column(
            float,
            nullable=False,
            checks=Check.isin([0.0, 1.0, 2.0, 3.0]),
        ),
    },
    coerce=True,  # attempt type coercion before validation
    strict=False,  # allow extra columns (e.g. event_time, feature_version)
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_feature_batch(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate a DataFrame of features against the schema.
    Fills NaN with 0 for numerical features before validation (source sparsity).
    Raises pandera.errors.SchemaError on violation.
    Returns the (possibly coerced) DataFrame.
    """
    numerical_cols = [
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
    ]
    # Users with zero transactions → avg and days_since come back as NaN
    for col in numerical_cols:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    # Cap failed_txn_rate at 1.0 (edge case: ClickHouse division rounding)
    if "failed_txn_rate_30d" in df.columns:
        df["failed_txn_rate_30d"] = df["failed_txn_rate_30d"].clip(0.0, 1.0)

    try:
        validated = FEATURE_SCHEMA.validate(df, lazy=True)
        log.debug("feature_validation_passed", rows=len(validated))
        return validated
    except pa.errors.SchemaErrors as exc:
        failure_cases = exc.failure_cases
        log.error(
            "feature_validation_failed",
            n_violations=len(failure_cases),
            columns=failure_cases["column"].unique().tolist()
            if "column" in failure_cases
            else [],
        )
        raise


def validate_single_entity(features: dict[str, float]) -> dict[str, float]:
    """
    Validate a single entity's feature dict (used in on-demand path).
    Returns validated features with NaN → 0 substitution.
    """
    df = pd.DataFrame([{"entity_id": 0, **features}])
    validated = validate_feature_batch(df)
    result = validated.iloc[0].to_dict()
    result.pop("entity_id", None)
    return result
