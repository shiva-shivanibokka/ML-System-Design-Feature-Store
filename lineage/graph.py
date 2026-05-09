"""
lineage/graph.py
================
Feature lineage graph — provenance DAG for feature traceability.

For every feature, we can answer:
  - What raw tables does this feature come from?
  - What transformation was applied?
  - Which models consume this feature?
  - What other features share the same source?

The lineage_edges table in ClickHouse stores directed edges:
  source_node → target_node (with edge_type: source | transform | model_input)

The Gradio UI uses the nodes/edges from get_full_lineage_graph() to render
an interactive Plotly network graph.
"""

from __future__ import annotations

from feature_store.connections import get_clickhouse_client


def get_lineage_for_feature(
    feature_name: str,
    feature_version: str = "v1",
) -> dict:
    """
    Return the lineage subgraph for a specific feature:
      - upstream: raw tables that feed into this feature
      - downstream: models that consume this feature
      - siblings: other features sharing the same source table

    Returns a dict with nodes and edges for rendering.
    """
    client = get_clickhouse_client()

    # Upstream edges (what feeds into this feature)
    upstream = client.execute(
        """
        SELECT source_node, target_node, edge_type
        FROM lineage_edges
        WHERE target_node = %(feature)s
          AND feature_version = %(version)s
        """,
        {"feature": feature_name, "version": feature_version},
    )

    # Downstream edges (what consumes this feature)
    downstream = client.execute(
        """
        SELECT source_node, target_node, edge_type
        FROM lineage_edges
        WHERE source_node = %(feature)s
          AND feature_version = %(version)s
        """,
        {"feature": feature_name, "version": feature_version},
    )

    # Collect all unique nodes
    nodes = set()
    edges = []

    for src, tgt, etype in upstream:
        nodes.add(src)
        nodes.add(tgt)
        edges.append({"source": src, "target": tgt, "type": etype})

    for src, tgt, etype in downstream:
        nodes.add(src)
        nodes.add(tgt)
        edges.append({"source": src, "target": tgt, "type": etype})

    nodes.add(feature_name)

    # Classify node types for rendering
    node_types = {}
    raw_tables = {"raw_users", "raw_transactions", "raw_support_tickets"}
    for node in nodes:
        if node in raw_tables:
            node_types[node] = "raw_table"
        elif node.startswith("churn_") or "_predictor" in node:
            node_types[node] = "model"
        else:
            node_types[node] = "feature"

    return {
        "feature_name": feature_name,
        "feature_version": feature_version,
        "nodes": [{"id": n, "type": node_types.get(n, "feature")} for n in nodes],
        "edges": edges,
    }


def get_full_lineage_graph(feature_version: str = "v1") -> dict:
    """
    Return the complete lineage graph across all features.
    Used by the Gradio Feature Explorer tab to render the full DAG.
    """
    client = get_clickhouse_client()

    rows = client.execute(
        """
        SELECT source_node, target_node, edge_type
        FROM lineage_edges
        WHERE feature_version = %(version)s
        ORDER BY edge_type, source_node
        """,
        {"version": feature_version},
    )

    nodes = set()
    edges = []
    for src, tgt, etype in rows:
        nodes.add(src)
        nodes.add(tgt)
        edges.append({"source": src, "target": tgt, "type": etype})

    raw_tables = {"raw_users", "raw_transactions", "raw_support_tickets"}
    node_list = []
    for node in nodes:
        if node in raw_tables:
            ntype = "raw_table"
        elif "_predictor" in node or node.startswith("churn_"):
            ntype = "model"
        else:
            ntype = "feature"
        node_list.append({"id": node, "type": ntype})

    return {
        "feature_version": feature_version,
        "node_count": len(node_list),
        "edge_count": len(edges),
        "nodes": node_list,
        "edges": edges,
    }
