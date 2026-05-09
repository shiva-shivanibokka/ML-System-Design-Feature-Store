"""
feature_store/registry.py
==========================
Feature registry — reads feature definitions from configs/features.yaml
and syncs them to the ClickHouse feature_registry + lineage_edges tables.

Responsibilities:
  - Load feature definitions from YAML
  - Upsert to ClickHouse feature_registry on startup
  - Write lineage edges to lineage_edges table
  - Provide get_feature_names() for use by offline store and validator
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
import yaml

from feature_store.connections import get_clickhouse_client

log = structlog.get_logger()

_CONFIG_PATH = Path(__file__).parent.parent / "configs" / "features.yaml"


def load_feature_config() -> dict[str, Any]:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_feature_names(config: dict | None = None) -> list[str]:
    if config is None:
        config = load_feature_config()
    return [f["name"] for f in config["features"]]


def sync_registry() -> None:
    """
    Upsert all feature definitions from features.yaml into ClickHouse.
    Called at application startup and can be re-run safely (idempotent).
    """
    config = load_feature_config()
    client = get_clickhouse_client()
    version = config["feature_version"]
    entity_type = config["entity_type"]
    now = datetime.utcnow()

    log.info("syncing_feature_registry", version=version, count=len(config["features"]))

    # ── Upsert feature definitions ──────────────────────────────────────────
    rows = []
    for feat in config["features"]:
        rows.append(
            (
                feat["name"],
                version,
                entity_type,
                feat["dtype"],
                feat["description"],
                feat["source_table"],
                feat["transformation"],
                feat["owner"],
                feat.get("tags", []),
                1,  # is_active
                now,
                None,  # deprecated_at
            )
        )

    client.execute(
        """
        INSERT INTO feature_registry
        (feature_name, feature_version, entity_type, dtype, description,
         source_table, transformation, owner, tags, is_active, created_at, deprecated_at)
        VALUES
        """,
        rows,
    )
    log.info("feature_registry_synced", rows=len(rows))

    # ── Upsert lineage edges ─────────────────────────────────────────────────
    edge_rows = [
        (
            edge["source"],
            edge["target"],
            edge["edge_type"],
            version,
            now,
        )
        for edge in config.get("lineage_edges", [])
    ]
    if edge_rows:
        client.execute(
            """
            INSERT INTO lineage_edges
            (source_node, target_node, edge_type, feature_version, created_at)
            VALUES
            """,
            edge_rows,
        )
        log.info("lineage_edges_synced", edges=len(edge_rows))


def get_all_features(version: str | None = None) -> list[dict]:
    """Return all active features from registry, optionally filtered by version."""
    client = get_clickhouse_client()
    config = load_feature_config()
    v = version or config["feature_version"]

    rows = client.execute(
        """
        SELECT
            feature_name, feature_version, entity_type, dtype,
            description, source_table, transformation, owner, tags,
            is_active, created_at
        FROM feature_registry
        WHERE feature_version = %(version)s AND is_active = 1
        ORDER BY feature_name
        """,
        {"version": v},
    )

    columns = [
        "feature_name",
        "feature_version",
        "entity_type",
        "dtype",
        "description",
        "source_table",
        "transformation",
        "owner",
        "tags",
        "is_active",
        "created_at",
    ]
    return [dict(zip(columns, row)) for row in rows]
