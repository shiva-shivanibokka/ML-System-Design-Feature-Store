"""
training/train.py
=================
Training pipeline — demonstrates the complete feature store → model workflow.

Steps:
  1. Generate label dataset (churn labels with timestamps)
  2. Pull PIT-correct features from offline store (no leakage)
  3. Show the BUG: what happens WITHOUT PIT joins (leaked features)
  4. Train LightGBM on PIT-correct features
  5. Track experiment with MLflow (params, metrics, feature importances)
  6. Register model in MLflow Model Registry
  7. Save training-time feature distribution snapshot to DuckDB
     (used later by skew detection to compare against serving distributions)

Usage:
    python training/train.py
    python training/train.py --no-pit-demo  # skip the leakage demonstration
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import lightgbm as lgb
import mlflow
import mlflow.lightgbm
import numpy as np
import pandas as pd
import structlog
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).parent.parent))

from feature_store.connections import get_duckdb_client
from feature_store.offline_store import (
    FEATURE_COLS,
    get_feature_stats,
    get_latest_features_for_entities,
    get_training_dataset,
)
from feature_store.registry import load_feature_config

log = structlog.get_logger()

EXPERIMENT_NAME = "churn-prediction-feature-store"
MODEL_NAME = "churn_predictor_v1"
FEATURE_VERSION = "v1"
RANDOM_STATE = 42


def _init_mlflow() -> None:
    """Point MLflow at DagsHub's hosted tracking server if configured,
    otherwise fall back to a local ./mlruns directory so training works
    with zero cloud accounts. Auth (MLFLOW_TRACKING_USERNAME/PASSWORD) is
    read by MLflow from env automatically.
    """
    uri = os.getenv("MLFLOW_TRACKING_URI", "").strip()
    mlflow.set_tracking_uri(uri or "file:./mlruns")
    mlflow.set_experiment(EXPERIMENT_NAME)


# ---------------------------------------------------------------------------
# Label generation — simulates downstream label pipeline
# ---------------------------------------------------------------------------


def generate_labels(client, n_users: int = 10_000) -> pd.DataFrame:
    """
    Generate churn labels for training.

    Label definition: a user who had >= 5 transactions in the 30 days
    BEFORE their label timestamp, but 0 in the 30 days AFTER — is churned.

    Label timestamps are set 30 days in the past so we can query both
    the "before" and "after" windows from historical data.
    """
    log.info("generating_labels")
    now = datetime.utcnow()
    label_date = now - timedelta(days=30)

    # Get all users with their transaction history
    rows = client.execute(
        """
        SELECT
            u.user_id,
            count(*) FILTER (WHERE t.event_time >= $before_start AND t.event_time < $label_date
                    AND t.status = 'success')                            AS txns_before,
            count(*) FILTER (WHERE t.event_time >= $label_date AND t.event_time < $after_end
                    AND t.status = 'success')                            AS txns_after
        FROM raw_users u
        LEFT JOIN raw_transactions t ON t.user_id = u.user_id
        GROUP BY u.user_id
        HAVING txns_before > 0
        LIMIT $n
        """,
        {
            "before_start": label_date - timedelta(days=30),
            "label_date": label_date,
            "after_end": now,
            "n": n_users,
        },
    )

    df = pd.DataFrame(rows, columns=["entity_id", "txns_before", "txns_after"])
    # Churn: had activity before label date but none after
    df["churned"] = ((df["txns_after"] == 0) & (df["txns_before"] >= 2)).astype(int)
    df["label_timestamp"] = label_date

    churn_rate = df["churned"].mean()
    log.info("labels_generated", total=len(df), churn_rate=round(churn_rate, 3))
    return df


# ---------------------------------------------------------------------------
# The PIT leakage demonstration
# ---------------------------------------------------------------------------


def demonstrate_pit_leakage(labels_df: pd.DataFrame) -> None:
    """
    Demonstrates training-serving skew caused by naive feature joins
    (without point-in-time correctness).

    BUG: If you join features at the *current* timestamp instead of
    the *label* timestamp, features computed using data AFTER the label
    date leak into training. The model learns from the future.

    This produces artificially inflated AUC that collapses at serving time.
    """
    log.warning(
        "PIT_LEAKAGE_DEMO",
        message=(
            "Demonstrating label leakage from naive feature join. "
            "In production, this inflates training AUC and collapses "
            "at serving time. The feature store prevents this."
        ),
    )

    entity_ids = labels_df["entity_id"].tolist()
    # Naive join: latest features as of NOW (contains data after label_timestamp)
    naive_df = get_latest_features_for_entities(
        entity_ids, feature_version=FEATURE_VERSION
    )
    naive_df = naive_df.merge(
        labels_df[["entity_id", "churned"]], on="entity_id", how="inner"
    )

    X_naive = naive_df[FEATURE_COLS].fillna(0)
    y_naive = naive_df["churned"]

    if y_naive.nunique() < 2:
        log.warning("pit_demo_skipped", reason="insufficient label variance")
        return

    X_tr, X_te, y_tr, y_te = train_test_split(
        X_naive, y_naive, test_size=0.2, random_state=RANDOM_STATE, stratify=y_naive
    )
    model = lgb.LGBMClassifier(
        n_estimators=100, random_state=RANDOM_STATE, verbosity=-1
    )
    model.fit(X_tr, y_tr)
    auc_leaked = roc_auc_score(y_te, model.predict_proba(X_te)[:, 1])

    log.warning(
        "LEAKED_MODEL_AUC",
        auc=round(auc_leaked, 4),
        message=(
            "This AUC is INFLATED due to future feature leakage. "
            "The model has seen data that would not exist at serving time."
        ),
    )


# ---------------------------------------------------------------------------
# PIT-correct training
# ---------------------------------------------------------------------------


def build_pit_training_dataset(labels_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a training dataset using point-in-time correct feature retrieval.
    Each row's features are looked up at exactly the label_timestamp,
    preventing any data from after the label date from entering training.

    Reuses the shared ASOF join in offline_store.get_training_dataset instead
    of re-implementing PIT SQL here.
    """
    label_pairs = list(
        zip(labels_df["entity_id"].tolist(), labels_df["label_timestamp"].tolist())
    )

    log.info("building_pit_dataset", entities=len(label_pairs))

    feature_df = get_training_dataset(label_pairs, FEATURE_VERSION)

    dataset = feature_df.merge(
        labels_df[["entity_id", "churned"]], on="entity_id", how="inner"
    ).fillna(0)

    log.info(
        "pit_dataset_built",
        rows=len(dataset),
        churn_rate=round(dataset["churned"].mean(), 3),
    )
    return dataset


# ---------------------------------------------------------------------------
# Model training + MLflow tracking
# ---------------------------------------------------------------------------


def train_and_register(dataset: pd.DataFrame) -> str:
    """
    Train LightGBM on PIT-correct features, track with MLflow,
    register in Model Registry, return run_id.
    """
    _init_mlflow()

    X = dataset[FEATURE_COLS].fillna(0)
    y = dataset["churned"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=RANDOM_STATE, stratify=y
    )

    params = {
        "n_estimators": 300,
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_child_samples": 20,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "class_weight": "balanced",
        "random_state": RANDOM_STATE,
        "verbosity": -1,
    }

    log.info("training_model", params=params, train_size=len(X_train))

    with mlflow.start_run(
        run_name=f"lgbm-pit-correct-{datetime.utcnow():%Y%m%d-%H%M}"
    ) as run:
        # Log parameters
        mlflow.log_params(params)
        mlflow.log_param("feature_version", FEATURE_VERSION)
        mlflow.log_param("train_size", len(X_train))
        mlflow.log_param("test_size", len(X_test))
        mlflow.log_param("churn_rate", round(y.mean(), 4))
        mlflow.log_param("pit_correct", True)

        # Train
        model = lgb.LGBMClassifier(**params)
        model.fit(
            X_train,
            y_train,
            eval_set=[(X_test, y_test)],
            callbacks=[lgb.early_stopping(30, verbose=False)],
        )

        # Evaluate
        y_prob = model.predict_proba(X_test)[:, 1]
        y_pred = model.predict(X_test)

        metrics = {
            "roc_auc": round(roc_auc_score(y_test, y_prob), 4),
            "avg_precision": round(average_precision_score(y_test, y_prob), 4),
            "f1": round(f1_score(y_test, y_pred), 4),
            "best_iteration": model.best_iteration_,
        }
        mlflow.log_metrics(metrics)

        # Feature importances
        importances = dict(zip(FEATURE_COLS, model.feature_importances_.tolist()))
        mlflow.log_dict(importances, "feature_importances.json")

        # Log model
        mlflow.lightgbm.log_model(model, "model")

        # Register in Model Registry
        model_uri = f"runs:/{run.info.run_id}/model"
        mlflow.register_model(model_uri, MODEL_NAME)

        log.info("training_complete", run_id=run.info.run_id, **metrics)
        return run.info.run_id


# ---------------------------------------------------------------------------
# Training-time skew snapshot
# ---------------------------------------------------------------------------


def snapshot_training_distribution(feature_version: str = FEATURE_VERSION) -> None:
    """
    Capture statistical distribution of training features and write to
    DuckDB skew_snapshots. This becomes the baseline for comparing
    against serving-time distributions in the skew detection module.
    """
    client = get_duckdb_client()
    stats = get_feature_stats(feature_version=feature_version, since_days=7)
    snapshot_id = str(uuid.uuid4())[:12]
    now = datetime.utcnow()

    rows = []
    for feature_name, s in stats.items():
        rows.append(
            {
                "snapshot_id": snapshot_id,
                "feature_name": feature_name,
                "feature_version": feature_version,
                "context": "training",
                "mean": s["mean"],
                "std": s["std"],
                "p25": s["p25"],
                "p50": s["p50"],
                "p75": s["p75"],
                "p95": s["p95"],
                "null_rate": s["null_rate"],
                "sample_count": s["sample_count"],
                "captured_at": now,
            }
        )

    for row in rows:
        client.execute(
            """
            INSERT INTO skew_snapshots
            (snapshot_id, feature_name, feature_version, context,
             mean, std, p25, p50, p75, p95, null_rate, sample_count, captured_at)
            VALUES
            ($snapshot_id, $feature_name, $feature_version, $context,
             $mean, $std, $p25, $p50, $p75, $p95, $null_rate, $sample_count, $captured_at)
            """,
            row,
        )

    if rows:
        log.info(
            "training_distribution_snapshot_saved",
            features=len(rows),
            snapshot_id=snapshot_id,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Train churn model with feature store")
    parser.add_argument("--no-pit-demo", action="store_true", help="Skip leakage demo")
    parser.add_argument("--feature-version", default="v1")
    args = parser.parse_args()

    client = get_duckdb_client()

    # 1. Generate labels
    labels_df = generate_labels(client)

    # 2. Demonstrate the leakage bug (educational)
    if not args.no_pit_demo:
        demonstrate_pit_leakage(labels_df)

    # 3. Build PIT-correct training dataset
    dataset = build_pit_training_dataset(labels_df)

    # 4. Train + register
    run_id = train_and_register(dataset)

    # 5. Snapshot training distribution for skew detection
    snapshot_training_distribution(feature_version=args.feature_version)

    log.info("pipeline_complete", run_id=run_id)


if __name__ == "__main__":
    import structlog

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    main()
