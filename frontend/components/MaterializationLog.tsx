"use client";
import { useEffect } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, MatRun, Metrics, Percentiles } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { DataState } from "./DataState";
import shared from "./shared.module.css";

export default function MaterializationLog() {
  const runsApi = useApi<MatRun[]>();
  const metricsApi = useApi<Metrics>();
  const { run: runRuns } = runsApi;
  const { run: runMetrics } = metricsApi;

  useEffect(() => {
    runRuns(api.materializationLog());
    runMetrics(api.metrics());
  }, [runRuns, runMetrics]);

  const runs = runsApi.data ?? [];
  const chartData = [...runs]
    .reverse()
    .map((r) => ({ run: shortRunId(r.run_id), entities: r.entities_processed }));

  return (
    <div className={shared.panel}>
      <div className={shared.panelHeader}>
        <h2 className={shared.panelTitle}>Materialization &amp; Serving Health</h2>
        <p className={shared.panelSubtitle}>
          Batch materialization runs that push offline features into the online store, and live
          latency percentiles for every serving path.
        </p>
      </div>

      <div className={shared.card}>
        <div className={shared.cardHeader}>
          <span className={shared.cardHeaderTitle}>Entities processed per run</span>
        </div>
        <DataState
          loading={runsApi.loading}
          error={runsApi.error}
          empty={runs.length === 0}
          emptyMessage="No materialization runs recorded yet."
          onRetry={() => runsApi.run(api.materializationLog())}
        >
          <div style={{ padding: "18px 12px 8px" }}>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
                <CartesianGrid stroke="var(--border)" vertical={false} />
                <XAxis
                  dataKey="run"
                  tick={{ fill: "var(--text-dim)", fontSize: 11, fontFamily: "var(--font-mono)" }}
                  axisLine={{ stroke: "var(--border)" }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: "var(--text-dim)", fontSize: 11, fontFamily: "var(--font-mono)" }}
                  axisLine={{ stroke: "var(--border)" }}
                  tickLine={false}
                />
                <Tooltip
                  cursor={{ fill: "rgba(255,255,255,0.03)" }}
                  contentStyle={{
                    background: "var(--surface-raised)",
                    border: "1px solid var(--border-strong)",
                    borderRadius: 5,
                    fontSize: 12.5,
                    fontFamily: "var(--font-mono)",
                  }}
                  labelStyle={{ color: "var(--text)" }}
                />
                <Bar dataKey="entities" fill="var(--cyan)" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </DataState>
      </div>

      <div className={shared.card}>
        <div className={shared.cardHeader}>
          <span className={shared.cardHeaderTitle}>Run history</span>
        </div>
        <DataState
          loading={runsApi.loading}
          error={runsApi.error}
          empty={runs.length === 0}
          emptyMessage="No materialization runs recorded yet."
          onRetry={() => runsApi.run(api.materializationLog())}
        >
          <div className={shared.tableWrap}>
            <table className={shared.table}>
              <thead>
                <tr>
                  <th>Run ID</th>
                  <th>Status</th>
                  <th>Processed</th>
                  <th>Failed</th>
                  <th>Duration</th>
                  <th>Completed</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.run_id}>
                    <td className="mono">{r.run_id}</td>
                    <td>
                      <span className={r.status === "success" ? shared.badgeOk : shared.badgeFlagged}>
                        {r.status}
                      </span>
                    </td>
                    <td className={shared.numeric}>{r.entities_processed.toLocaleString()}</td>
                    <td className={shared.numeric}>{r.entities_failed.toLocaleString()}</td>
                    <td className={shared.numeric}>{r.duration_ms.toLocaleString()} ms</td>
                    <td className={shared.numeric}>{formatTime(r.completed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>
      </div>

      <div className={shared.card}>
        <div className={shared.cardHeader}>
          <span className={shared.cardHeaderTitle}>Serving latency</span>
        </div>
        <DataState
          loading={metricsApi.loading}
          error={metricsApi.error}
          empty={!metricsApi.data}
          emptyMessage="No metrics recorded yet."
          onRetry={() => metricsApi.run(api.metrics())}
        >
          {metricsApi.data && (
            <>
              <div style={{ padding: "16px 18px 0" }}>
                <div className={shared.summaryStrip}>
                  <span>
                    <strong>{(metricsApi.data.cache_hit_rate * 100).toFixed(1)}%</strong> cache hit rate
                  </span>
                  <span>
                    <strong>{metricsApi.data.online_store_entities.toLocaleString()}</strong> entities
                    online
                  </span>
                </div>
              </div>
              <div className={shared.tableWrap}>
                <table className={shared.table}>
                  <thead>
                    <tr>
                      <th>Path</th>
                      <th>p50</th>
                      <th>p95</th>
                      <th>p99</th>
                      <th>Samples</th>
                    </tr>
                  </thead>
                  <tbody>
                    <LatencyRow label="Online store" p={metricsApi.data.online_store} />
                    <LatencyRow label="On-demand compute" p={metricsApi.data.on_demand} />
                    <LatencyRow label="Batch" p={metricsApi.data.batch} />
                  </tbody>
                </table>
              </div>
            </>
          )}
        </DataState>
      </div>
    </div>
  );
}

function LatencyRow({ label, p }: { label: string; p: Percentiles }) {
  return (
    <tr>
      <td>{label}</td>
      <td className={shared.numeric}>{p.p50.toFixed(1)} ms</td>
      <td className={shared.numeric}>{p.p95.toFixed(1)} ms</td>
      <td className={shared.numeric}>{p.p99.toFixed(1)} ms</td>
      <td className={shared.numeric}>{p.count.toLocaleString()}</td>
    </tr>
  );
}

function shortRunId(id: string): string {
  return id.length > 8 ? id.slice(-8) : id;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}
