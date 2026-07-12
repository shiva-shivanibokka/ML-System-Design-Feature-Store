"""
feature_store/registry.py
==========================
Feature registry — reads feature definitions from configs/features.yaml
and syncs them to the DuckDB feature_registry + lineage_edges tables.

Responsibilities:
  - Load feature definitions from YAML
  - Upsert to DuckDB feature_registry on startup
  - Write lineage edges to lineage_edges table
  - Provide get_feature_names() for use by offline store and validator
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
import yaml

from feature_store.connections import get_duckdb_client
from feature_store.schema import apply_schema

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
    Upsert all feature definitions from features.yaml into DuckDB.
    Called at application startup and can be re-run safely (idempotent).

    DuckDB has no ReplacingMergeTree, so idempotency is implemented as
    delete-then-insert of the version's rows.
    """
    config = load_feature_config()
    client = get_duckdb_client()
    apply_schema(client)
    version = config["feature_version"]
    entity_type = config["entity_type"]
    now = datetime.utcnow()

    log.info("syncing_feature_registry", version=version, count=len(config["features"]))

    # ── Upsert feature definitions ──────────────────────────────────────────
    client.execute(
        "DELETE FROM feature_registry WHERE feature_version = $v", {"v": version}
    )
    for feat in config["features"]:
        client.execute(
            """
            INSERT INTO feature_registry
            (feature_name, feature_version, entity_type, dtype, description,
             source_table, transformation, owner, tags, is_active, created_at, deprecated_at)
            VALUES
            ($name, $version, $entity_type, $dtype, $description,
             $source_table, $transformation, $owner, $tags, $is_active, $created_at, $deprecated_at)
            """,
            {
                "name": feat["name"],
                "version": version,
                "entity_type": entity_type,
                "dtype": feat["dtype"],
                "description": feat["description"],
                "source_table": feat["source_table"],
                "transformation": feat["transformation"],
                "owner": feat["owner"],
                "tags": feat.get("tags", []),
                "is_active": 1,
                "created_at": now,
                "deprecated_at": None,
            },
        )
    log.info("feature_registry_synced", rows=len(config["features"]))

    # ── Upsert lineage edges ─────────────────────────────────────────────────
    edges = config.get("lineage_edges", [])
    client.execute(
        "DELETE FROM lineage_edges WHERE feature_version = $v", {"v": version}
    )
    for edge in edges:
        client.execute(
            """
            INSERT INTO lineage_edges
            (source_node, target_node, edge_type, feature_version, created_at)
            VALUES ($source, $target, $edge_type, $version, $created_at)
            """,
            {
                "source": edge["source"],
                "target": edge["target"],
                "edge_type": edge["edge_type"],
                "version": version,
                "created_at": now,
            },
        )
    if edges:
        log.info("lineage_edges_synced", edges=len(edges))


def get_all_features(version: str | None = None) -> list[dict]:
    """Return all active features from registry, optionally filtered by version."""
    client = get_duckdb_client()
    config = load_feature_config()
    v = version or config["feature_version"]

    rows = client.execute(
        """
        SELECT
            feature_name, feature_version, entity_type, dtype,
            description, source_table, transformation, owner, tags,
            is_active, created_at
        FROM feature_registry
        WHERE feature_version = $version AND is_active = 1
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
