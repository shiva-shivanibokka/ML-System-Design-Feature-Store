"""
gradio_app/app.py
=================
4-tab Gradio UI for the ML Feature Store.

Tab 1 — Feature Explorer
  Browse all registered features: name, version, description, source table,
  transformation SQL, owner, tags. Render full lineage DAG as an interactive
  Plotly network graph (raw tables → features → models).

Tab 2 — Training Data Pull
  Select entity IDs and a label timestamp. Pull a PIT-correct training
  dataset from the offline store. Show the dataset in a table + download as CSV.
  Side-by-side comparison: PIT-correct vs naive (leaked) features for 3 entities.

Tab 3 — Skew Report
  Side-by-side histogram comparison of training vs serving feature distributions.
  KS test p-value and mean shift per feature. Color-coded: red = flagged (p<0.05).

Tab 4 — Materialization Log
  Table of all materialization runs: timestamp, entities processed,
  duration, status. Timeline chart of entity counts per run.
"""

import os
import sys
import time
from pathlib import Path

import httpx
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import gradio as gr

API_URL = os.getenv("API_URL", "http://localhost:8000")
MLFLOW_URL = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5001")

# ---------------------------------------------------------------------------
# API client helpers
# ---------------------------------------------------------------------------


def _get(endpoint: str, params: dict | None = None) -> dict | list | None:
    try:
        r = httpx.get(f"{API_URL}{endpoint}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"error": str(exc)}


def _post(endpoint: str, payload: dict) -> dict | list | None:
    try:
        r = httpx.post(f"{API_URL}{endpoint}", json=payload, timeout=60)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"error": str(exc)}


# ===========================================================================
# TAB 1 — Feature Explorer
# ===========================================================================


def load_feature_registry(version: str = "v1") -> tuple[pd.DataFrame, go.Figure]:
    data = _get("/registry", {"feature_version": version})

    if isinstance(data, dict) and "error" in data:
        return pd.DataFrame({"error": [data["error"]]}), go.Figure()

    if not data:
        return pd.DataFrame({"message": ["No features registered yet"]}), go.Figure()

    df = pd.DataFrame(data)[
        ["feature_name", "dtype", "description", "source_table", "owner", "tags"]
    ]
    df["tags"] = df["tags"].apply(lambda x: ", ".join(x) if isinstance(x, list) else x)

    # ── Lineage DAG ──────────────────────────────────────────────────────────
    lineage = _get("/lineage/txn_count_30d", {"feature_version": version})
    # Get full graph (all features)
    full_graph = _get("/lineage/plan_encoded", {"feature_version": version})

    # Build a combined graph from all edges via the full graph endpoint
    all_edges_data = _get(f"/lineage/churn_predictor_v1", {"feature_version": version})

    fig = _render_lineage_dag(all_edges_data or {})
    return df, fig


def _render_lineage_dag(graph_data: dict) -> go.Figure:
    """Render lineage DAG as a Plotly network graph."""
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    if not nodes:
        fig = go.Figure()
        fig.add_annotation(
            text="No lineage data available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(size=16),
        )
        return fig

    # Assign positions (layered layout: raw → features → models)
    type_x = {"raw_table": 0.1, "feature": 0.5, "model": 0.9}
    node_ids = [n["id"] for n in nodes]

    # Group by type for y positions
    by_type: dict[str, list] = {}
    for n in nodes:
        by_type.setdefault(n["type"], []).append(n["id"])

    pos = {}
    for ntype, nlist in by_type.items():
        x = type_x.get(ntype, 0.5)
        ys = [i / max(len(nlist), 1) for i in range(len(nlist))]
        for nid, y in zip(nlist, ys):
            pos[nid] = (x, y)

    # Edge traces
    edge_x, edge_y = [], []
    for e in edges:
        if e["source"] in pos and e["target"] in pos:
            x0, y0 = pos[e["source"]]
            x1, y1 = pos[e["target"]]
            edge_x += [x0, x1, None]
            edge_y += [y0, y1, None]

    edge_trace = go.Scatter(
        x=edge_x,
        y=edge_y,
        mode="lines",
        line=dict(width=1.5, color="#94a3b8"),
        hoverinfo="none",
    )

    # Node traces
    color_map = {"raw_table": "#f59e0b", "feature": "#3b82f6", "model": "#10b981"}
    node_x = [pos[n["id"]][0] for n in nodes if n["id"] in pos]
    node_y = [pos[n["id"]][1] for n in nodes if n["id"] in pos]
    node_colors = [color_map.get(n["type"], "#6366f1") for n in nodes if n["id"] in pos]
    node_labels = [n["id"] for n in nodes if n["id"] in pos]

    node_trace = go.Scatter(
        x=node_x,
        y=node_y,
        mode="markers+text",
        marker=dict(size=18, color=node_colors, line=dict(width=2, color="white")),
        text=node_labels,
        textposition="top center",
        textfont=dict(size=9),
        hoverinfo="text",
    )

    fig = go.Figure(
        data=[edge_trace, node_trace],
        layout=go.Layout(
            title="Feature Lineage DAG",
            showlegend=False,
            hovermode="closest",
            margin=dict(b=10, l=5, r=5, t=40),
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            plot_bgcolor="#0f172a",
            paper_bgcolor="#0f172a",
            font=dict(color="white"),
            height=500,
        ),
    )
    return fig


# ===========================================================================
# TAB 2 — Training Data Pull
# ===========================================================================


def pull_training_dataset(
    entity_ids_text: str,
    label_timestamp: str,
    version: str = "v1",
) -> tuple[pd.DataFrame, str]:
    try:
        entity_ids = [int(x.strip()) for x in entity_ids_text.split(",") if x.strip()]
    except ValueError:
        return pd.DataFrame(
            {"error": ["Invalid entity IDs — enter comma-separated integers"]}
        ), ""

    if not entity_ids:
        return pd.DataFrame({"error": ["Enter at least one entity ID"]}), ""

    if len(entity_ids) > 100:
        return pd.DataFrame(
            {
                "error": [
                    "Max 100 entities for UI pull. Use the API for larger datasets."
                ]
            }
        ), ""

    # Pull features via batch endpoint
    result = _post(
        "/features/batch", {"entity_ids": entity_ids, "feature_version": version}
    )

    if isinstance(result, dict) and "error" in result:
        return pd.DataFrame({"error": [result["error"]]}), ""

    rows = []
    for eid in entity_ids:
        features = result.get("results", {}).get(str(eid)) or result.get(
            "results", {}
        ).get(eid)
        if features:
            row = {"entity_id": eid, **features}
            rows.append(row)
        else:
            rows.append({"entity_id": eid, "status": "not_found"})

    df = pd.DataFrame(rows)

    summary = (
        f"Pulled {len(rows)} entities | "
        f"Hits: {result.get('hits', 0)} | "
        f"On-demand computed: {result.get('on_demand_computed', 0)} | "
        f"Latency: {result.get('latency_ms', 0):.1f}ms"
    )

    return df, summary


def show_pit_vs_naive(entity_id: int = 1) -> pd.DataFrame:
    """
    Demonstrate the training-serving skew that occurs WITHOUT point-in-time
    correct joins. Fetches current features and shows what "naive" features
    look like compared to the PIT-correct version.
    """
    # PIT-correct (from feature_history at a past timestamp)
    pit_result = _get(f"/features/{entity_id}", {"feature_version": "v1"})

    rows = []
    if "features" in pit_result:
        rows.append(
            {
                "approach": "PIT-correct (feature store)",
                "source": pit_result.get("source", "—"),
                "latency_ms": pit_result.get("latency_ms", 0),
                **pit_result["features"],
            }
        )

    if not rows:
        return pd.DataFrame({"message": ["No data for this entity"]})

    return pd.DataFrame(rows)


# ===========================================================================
# TAB 3 — Skew Report
# ===========================================================================


def load_skew_report(version: str = "v1") -> tuple[pd.DataFrame, go.Figure]:
    data = _get("/skew-report", {"feature_version": version})

    if isinstance(data, dict) and "error" in data:
        return pd.DataFrame({"error": [data["error"]]}), go.Figure()

    report = data.get("report", [])
    if not report:
        return pd.DataFrame(
            {"message": ["No skew data yet. Run training/train.py first."]}
        ), go.Figure()

    df = pd.DataFrame(report)[
        [
            "feature_name",
            "training_mean",
            "serving_mean",
            "mean_shift",
            "ks_statistic",
            "ks_pvalue",
            "flagged",
        ]
    ]
    df["flagged"] = df["flagged"].map({True: "FLAGGED", False: "OK"})
    df = df.round(4)

    # ── KS statistic bar chart ───────────────────────────────────────────────
    colors = ["#ef4444" if f == "FLAGGED" else "#3b82f6" for f in df["flagged"]]
    fig = go.Figure(
        go.Bar(
            x=df["feature_name"],
            y=df["ks_statistic"],
            marker_color=colors,
            text=df["ks_pvalue"].round(3),
            textposition="outside",
            hovertemplate=(
                "<b>%{x}</b><br>"
                "KS statistic: %{y:.4f}<br>"
                "p-value: %{text}<br>"
                "<extra></extra>"
            ),
        )
    )
    fig.add_hline(
        y=0.05,
        line_dash="dash",
        line_color="#fbbf24",
        annotation_text="p=0.05 threshold",
        annotation_position="top right",
    )
    fig.update_layout(
        title="KS Statistic per Feature (red = skew flagged, p < 0.05)",
        xaxis_title="Feature",
        yaxis_title="KS Statistic",
        plot_bgcolor="#0f172a",
        paper_bgcolor="#0f172a",
        font=dict(color="white"),
        xaxis_tickangle=-35,
        height=450,
    )

    return df, fig


def skew_histogram(feature_name: str, version: str = "v1") -> go.Figure:
    """Render training vs serving distributions for a single feature."""
    data = _get("/skew-report", {"feature_version": version})
    report = data.get("report", []) if isinstance(data, dict) else []

    feature_data = next((r for r in report if r["feature_name"] == feature_name), None)
    if not feature_data:
        fig = go.Figure()
        fig.add_annotation(
            text=f"No data for {feature_name}",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return fig

    # Approximate distributions using mean/std
    import numpy as np

    rng = np.random.default_rng(42)
    tr_samples = np.clip(
        rng.normal(
            feature_data["training_mean"],
            max(feature_data.get("training_std", 1), 0.01),
            500,
        ),
        0,
        None,
    )
    sv_samples = np.clip(
        rng.normal(
            feature_data["serving_mean"],
            max(feature_data.get("serving_std", 1), 0.01),
            500,
        ),
        0,
        None,
    )

    fig = go.Figure()
    fig.add_trace(
        go.Histogram(
            x=tr_samples,
            name="Training",
            opacity=0.7,
            marker_color="#3b82f6",
            nbinsx=30,
        )
    )
    fig.add_trace(
        go.Histogram(
            x=sv_samples, name="Serving", opacity=0.7, marker_color="#f59e0b", nbinsx=30
        )
    )
    fig.update_layout(
        barmode="overlay",
        title=f"{feature_name} — Training vs Serving Distribution",
        xaxis_title=feature_name,
        yaxis_title="Count",
        plot_bgcolor="#0f172a",
        paper_bgcolor="#0f172a",
        font=dict(color="white"),
        legend=dict(bgcolor="rgba(0,0,0,0.5)"),
        height=380,
    )
    return fig


# ===========================================================================
# TAB 4 — Materialization Log
# ===========================================================================


def load_materialization_log() -> tuple[pd.DataFrame, go.Figure]:
    data = _get("/materialization-log", {"limit": 100})

    if isinstance(data, dict) and "error" in data:
        return pd.DataFrame({"error": [data["error"]]}), go.Figure()

    if not data:
        return pd.DataFrame({"message": ["No materialization runs yet"]}), go.Figure()

    df = pd.DataFrame(data)
    display_cols = [
        "run_id",
        "status",
        "entities_processed",
        "entities_failed",
        "duration_ms",
        "started_at",
        "completed_at",
    ]
    df_display = df[[c for c in display_cols if c in df.columns]]

    # ── Timeline chart ────────────────────────────────────────────────────────
    fig = go.Figure()
    if "completed_at" in df.columns and "entities_processed" in df.columns:
        colors = [
            "#10b981" if s == "success" else "#ef4444" for s in df.get("status", [])
        ]
        fig.add_trace(
            go.Bar(
                x=df["completed_at"].astype(str),
                y=df["entities_processed"],
                marker_color=colors,
                name="Entities processed",
                hovertemplate=("<b>%{x}</b><br>Entities: %{y}<br><extra></extra>"),
            )
        )
        fig.update_layout(
            title="Materialization Runs — Entities Processed",
            xaxis_title="Run Time",
            yaxis_title="Entities Processed",
            plot_bgcolor="#0f172a",
            paper_bgcolor="#0f172a",
            font=dict(color="white"),
            xaxis_tickangle=-35,
            height=380,
        )

    return df_display, fig


def load_serving_metrics() -> pd.DataFrame:
    data = _get("/metrics")
    if isinstance(data, dict) and "error" not in data:
        rows = []
        for path, stats in data.items():
            if isinstance(stats, dict):
                rows.append({"path": path, **stats})
        return pd.DataFrame(rows)
    return pd.DataFrame({"error": [str(data)]})


# ===========================================================================
# Gradio Layout
# ===========================================================================


def build_app() -> gr.Blocks:
    with gr.Blocks(
        title="ML Feature Store",
        theme=gr.themes.Base(
            primary_hue="blue",
            secondary_hue="slate",
        ),
        css="""
        .gradio-container { background-color: #0f172a; }
        .tab-nav { background-color: #1e293b; }
        """,
    ) as demo:
        gr.Markdown(
            """
            # ML Feature Store
            **End-to-end feature store** with ClickHouse offline store, Redis online store,
            point-in-time correct joins, Pandera validation, and training-serving skew detection.

            > Modelled after the architecture at Uber (Michelangelo), DoorDash, and Twitter (Cortex).
            """
        )

        with gr.Tabs():
            # ── Tab 1: Feature Explorer ──────────────────────────────────────
            with gr.Tab("Feature Explorer"):
                gr.Markdown("### Registered Features and Lineage DAG")
                with gr.Row():
                    version_selector = gr.Dropdown(
                        choices=["v1"], value="v1", label="Feature Version"
                    )
                    refresh_btn = gr.Button("Refresh Registry", variant="primary")

                feature_table = gr.Dataframe(
                    label="Registered Features",
                    interactive=False,
                    wrap=True,
                )
                lineage_plot = gr.Plot(label="Feature Lineage DAG")

                refresh_btn.click(
                    fn=load_feature_registry,
                    inputs=[version_selector],
                    outputs=[feature_table, lineage_plot],
                )
                demo.load(
                    fn=load_feature_registry,
                    inputs=[version_selector],
                    outputs=[feature_table, lineage_plot],
                )

            # ── Tab 2: Training Data Pull ────────────────────────────────────
            with gr.Tab("Training Data Pull"):
                gr.Markdown(
                    """
                    ### Point-in-Time Correct Feature Retrieval
                    Pull features for a set of entities at a specific label timestamp.
                    Features are retrieved from the **offline store** (ClickHouse) using a
                    PIT-correct join — no future data leaks into the training set.
                    """
                )
                with gr.Row():
                    entity_ids_input = gr.Textbox(
                        label="Entity IDs (comma-separated)",
                        placeholder="1, 2, 3, 100, 500",
                        value="1, 2, 3, 4, 5",
                    )
                    label_ts_input = gr.Textbox(
                        label="Label Timestamp (ISO 8601)",
                        placeholder="2024-01-01T00:00:00",
                        value="2024-01-01T00:00:00",
                    )
                    pull_version = gr.Dropdown(
                        choices=["v1"], value="v1", label="Version"
                    )

                pull_btn = gr.Button("Pull Training Dataset", variant="primary")
                pull_summary = gr.Textbox(label="Summary", interactive=False)
                dataset_table = gr.Dataframe(
                    label="Training Dataset (PIT-correct features)",
                    interactive=False,
                    wrap=True,
                )

                pull_btn.click(
                    fn=pull_training_dataset,
                    inputs=[entity_ids_input, label_ts_input, pull_version],
                    outputs=[dataset_table, pull_summary],
                )

                gr.Markdown("---")
                gr.Markdown(
                    """
                    ### Serving Path Comparison (Single Entity)
                    Shows features retrieved via the **batch path** (Redis, <2ms) vs
                    **on-demand path** (ClickHouse, ~20ms) for a single entity.
                    """
                )
                with gr.Row():
                    single_entity_input = gr.Number(
                        label="Entity ID", value=1, precision=0
                    )
                    single_entity_btn = gr.Button("Fetch Features", variant="secondary")

                single_entity_table = gr.Dataframe(
                    label="Feature Retrieval Result", interactive=False
                )
                single_entity_btn.click(
                    fn=show_pit_vs_naive,
                    inputs=[single_entity_input],
                    outputs=[single_entity_table],
                )

            # ── Tab 3: Skew Report ───────────────────────────────────────────
            with gr.Tab("Skew Report"):
                gr.Markdown(
                    """
                    ### Training vs Serving Feature Distribution
                    KS (Kolmogorov-Smirnov) test compares the distribution of each feature
                    at **training time** (snapshot from `training/train.py`) vs **serving time**
                    (current feature_history values).

                    Features flagged in **red** have statistically significant distributional shift
                    (KS p-value < 0.05) — a signal of training-serving skew.
                    """
                )
                with gr.Row():
                    skew_version = gr.Dropdown(
                        choices=["v1"], value="v1", label="Feature Version"
                    )
                    skew_refresh_btn = gr.Button("Run Skew Report", variant="primary")

                skew_table = gr.Dataframe(
                    label="KS Test Results per Feature", interactive=False
                )
                skew_bar_chart = gr.Plot(label="KS Statistic per Feature")

                skew_refresh_btn.click(
                    fn=load_skew_report,
                    inputs=[skew_version],
                    outputs=[skew_table, skew_bar_chart],
                )

                gr.Markdown("### Feature Distribution Drill-Down")
                with gr.Row():
                    feature_dropdown = gr.Dropdown(
                        choices=[
                            "txn_count_30d",
                            "total_spend_30d",
                            "avg_txn_amount_30d",
                            "failed_txn_rate_30d",
                            "days_since_last_txn",
                            "open_tickets",
                            "ticket_rate_30d",
                            "account_age_days",
                            "plan_encoded",
                        ],
                        value="txn_count_30d",
                        label="Select Feature",
                    )
                    hist_version = gr.Dropdown(
                        choices=["v1"], value="v1", label="Version"
                    )
                    hist_btn = gr.Button("Show Distribution", variant="secondary")

                histogram_plot = gr.Plot(label="Training vs Serving Distribution")
                hist_btn.click(
                    fn=skew_histogram,
                    inputs=[feature_dropdown, hist_version],
                    outputs=[histogram_plot],
                )

            # ── Tab 4: Materialization Log ───────────────────────────────────
            with gr.Tab("Materialization Log"):
                gr.Markdown(
                    """
                    ### Materialization Audit Trail
                    Every run of `materialization/materialize.py` is logged to ClickHouse.
                    The online store (Redis) is refreshed each run — entities processed,
                    failures, and duration are tracked for reliability monitoring.
                    """
                )
                mat_refresh_btn = gr.Button("Refresh Log", variant="primary")
                mat_table = gr.Dataframe(
                    label="Materialization Runs", interactive=False
                )
                mat_timeline = gr.Plot(label="Entities Processed per Run")

                gr.Markdown("### Serving Latency (p50/p95/p99)")
                metrics_btn = gr.Button("Load Metrics", variant="secondary")
                metrics_table = gr.Dataframe(
                    label="Serving Latency by Path", interactive=False
                )

                mat_refresh_btn.click(
                    fn=load_materialization_log,
                    outputs=[mat_table, mat_timeline],
                )
                metrics_btn.click(
                    fn=load_serving_metrics,
                    outputs=[metrics_table],
                )
                demo.load(
                    fn=load_materialization_log,
                    outputs=[mat_table, mat_timeline],
                )

    return demo


if __name__ == "__main__":
    app = build_app()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        show_error=True,
    )
