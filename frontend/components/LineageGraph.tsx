"use client";
import { useEffect, useMemo } from "react";
import { api, LineageGraph as LineageGraphData, LineageNode } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { DataState } from "./DataState";

const TYPE_ORDER: LineageNode["type"][] = ["raw_table", "feature", "model"];
const COLUMN_X = { raw_table: 90, feature: 420, model: 750 };
const WIDTH = 840;
const ROW_HEIGHT = 56;
const TOP_PAD = 44;

type Positioned = LineageNode & { x: number; y: number };

/**
 * Renders /lineage as a plain layered SVG DAG — no graph library. Raw
 * tables sit left, derived features middle, models right, matching the
 * actual direction data flows through this system. Edges are drawn as
 * gently curved connectors so crossing lines stay legible at a glance.
 */
export default function LineageGraph() {
  const { data, loading, error, run } = useApi<LineageGraphData>();

  useEffect(() => {
    run(api.lineage());
  }, [run]);

  const positioned = useMemo(() => {
    if (!data) return null;
    const byType: Record<string, LineageNode[]> = { raw_table: [], feature: [], model: [] };
    for (const n of data.nodes) (byType[n.type] ??= []).push(n);

    const nodes: Positioned[] = [];
    let maxRows = 1;
    for (const type of TYPE_ORDER) {
      const col = byType[type] ?? [];
      maxRows = Math.max(maxRows, col.length);
      col.forEach((n, i) => {
        nodes.push({ ...n, x: COLUMN_X[type], y: TOP_PAD + i * ROW_HEIGHT });
      });
    }
    const height = TOP_PAD * 2 + Math.max(0, maxRows - 1) * ROW_HEIGHT;
    const byId = new Map(nodes.map((n) => [n.id, n]));
    return { nodes, byId, height: Math.max(height, 160) };
  }, [data]);

  return (
    <DataState
      loading={loading}
      error={error}
      empty={!!data && data.nodes.length === 0}
      emptyMessage="No lineage recorded yet — sync the feature registry to populate the graph."
      onRetry={() => run(api.lineage())}
    >
      {positioned && (
        <div className="lineage-wrap">
          <div className="lineage-legend">
            <LegendItem swatch="lineage-swatch-raw" label="raw table" />
            <LegendItem swatch="lineage-swatch-feature" label="feature" />
            <LegendItem swatch="lineage-swatch-model" label="model" />
          </div>
          <svg
            className="lineage-svg"
            viewBox={`0 0 ${WIDTH} ${positioned.height}`}
            role="img"
            aria-label="Lineage graph from raw tables through features to models"
          >
            <defs>
              <pattern id="lineage-grid" width="18" height="18" patternUnits="userSpaceOnUse">
                <circle cx="1" cy="1" r="1" fill="rgba(255,255,255,0.05)" />
              </pattern>
              <marker
                id="lineage-arrow"
                viewBox="0 0 8 8"
                refX="7"
                refY="4"
                markerWidth="7"
                markerHeight="7"
                orient="auto-start-reverse"
              >
                <path d="M0,0 L8,4 L0,8 z" fill="var(--border-strong)" />
              </marker>
            </defs>
            <rect width={WIDTH} height={positioned.height} fill="url(#lineage-grid)" />

            {data!.edges.map((e, i) => {
              const s = positioned.byId.get(e.source);
              const t = positioned.byId.get(e.target);
              if (!s || !t) return null;
              const midX = (s.x + t.x) / 2;
              const path = `M ${s.x + 74} ${s.y + 16} C ${midX} ${s.y + 16}, ${midX} ${t.y + 16}, ${t.x - 4} ${t.y + 16}`;
              return (
                <path
                  key={`${e.source}-${e.target}-${i}`}
                  d={path}
                  fill="none"
                  stroke="var(--border-strong)"
                  strokeWidth={1.4}
                  markerEnd="url(#lineage-arrow)"
                />
              );
            })}

            {positioned.nodes.map((n) => (
              <g key={n.id} transform={`translate(${n.x}, ${n.y})`}>
                <rect
                  width={78}
                  height={30}
                  rx={4}
                  className={
                    n.type === "raw_table"
                      ? "lineage-node-raw"
                      : n.type === "feature"
                        ? "lineage-node-feature"
                        : "lineage-node-model"
                  }
                />
                <text x={39} y={19} textAnchor="middle" className="lineage-node-label">
                  {truncate(n.id, 12)}
                </text>
                <title>{n.id}</title>
              </g>
            ))}
          </svg>
        </div>
      )}
    </DataState>
  );
}

function LegendItem({ swatch, label }: { swatch: string; label: string }) {
  return (
    <span className="lineage-legend-item">
      <span className={`lineage-legend-swatch ${swatch}`} />
      {label}
    </span>
  );
}

function truncate(s: string, n: number) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
