"use client";
import { useEffect } from "react";
import { api, MatRun, Metrics, Percentiles } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { DataState } from "./DataState";
import Tip from "./Tip";

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
  const maxEntities = Math.max(1, ...chartData.map((d) => d.entities));

  return (
    <div className="stack">
      <div className="stack-head">
        <div className="stack-head-text">
          <h2 className="stack-title">
            Materialization &amp; Serving Health
            <Tip text="Batch jobs that copy computed features from the offline store into the online store (Valkey) for fast serving, plus live latency metrics." />
          </h2>
          <p className="stack-sub">
            Batch materialization runs that push offline features into the online store, and live
            latency percentiles for every serving path.
          </p>
        </div>
        <span className="chip">runs · latency</span>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">
            Entities processed per run
            <Tip text="Number of entities materialized into the online store in each batch run." />
          </span>
        </div>
        <DataState
          loading={runsApi.loading}
          error={runsApi.error}
          empty={runs.length === 0}
          emptyMessage="No materialization runs recorded yet."
          onRetry={() => runsApi.run(api.materializationLog())}
        >
          <div style={{ padding: "18px" }}>
            <div className="bars">
              {chartData.map((d, i) => (
                <div className="bar-row" key={`${d.run}-${i}`}>
                  <span className="bar-name mono" title={d.run}>
                    {d.run}
                  </span>
                  <span className="bar-track">
                    <span
                      className="bar-fill cyan"
                      style={{ width: `${(d.entities / maxEntities) * 100}%` }}
                    />
                  </span>
                  <span className="bar-val">{d.entities.toLocaleString()}</span>
                </div>
              ))}
            </div>
          </div>
        </DataState>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">
            Run history
            <Tip text="Log of materialization batch runs." />
          </span>
        </div>
        <DataState
          loading={runsApi.loading}
          error={runsApi.error}
          empty={runs.length === 0}
          emptyMessage="No materialization runs recorded yet."
          onRetry={() => runsApi.run(api.materializationLog())}
        >
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>
                    Run ID
                    <Tip text="Unique identifier for this materialization run." />
                  </th>
                  <th>
                    Status
                    <Tip text="success / partial / failed." />
                  </th>
                  <th>
                    Processed
                    <Tip text="Entities written to the online store in this run." />
                  </th>
                  <th>
                    Failed
                    <Tip text="Entities that failed to materialize in this run." />
                  </th>
                  <th>
                    Duration
                    <Tip text="How long the run took." />
                  </th>
                  <th>
                    Completed
                    <Tip text="Timestamp when the run finished." />
                  </th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.run_id}>
                    <td className="mono">{r.run_id}</td>
                    <td>
                      <span className={r.status === "success" ? "badge badge-ok" : "badge badge-flagged"}>
                        {r.status}
                      </span>
                    </td>
                    <td className="num">{r.entities_processed.toLocaleString()}</td>
                    <td className="num">{r.entities_failed.toLocaleString()}</td>
                    <td className="num">{r.duration_ms.toLocaleString()} ms</td>
                    <td className="num">{formatTime(r.completed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">
            Serving latency
            <Tip text="Latency percentiles for each way features are served, plus how often requests hit the online store cache." />
          </span>
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
                <div className="tiles">
                  <div className="tile">
                    <div className="tile-v">{(metricsApi.data.cache_hit_rate * 100).toFixed(1)}%</div>
                    <div className="tile-k">
                      Cache hit rate
                      <Tip text="Share of lookups served from the online store (Valkey) vs recomputed on demand." />
                    </div>
                  </div>
                  <div className="tile">
                    <div className="tile-v">{metricsApi.data.online_store_entities.toLocaleString()}</div>
                    <div className="tile-k">
                      Entities online
                      <Tip text="Number of entities currently materialized in the online store." />
                    </div>
                  </div>
                </div>
              </div>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>
                        Path
                        <Tip text="Which serving path these percentiles measure (online store, on-demand compute, or batch)." />
                      </th>
                      <th>
                        p50
                        <Tip text="50th percentile serving latency (ms)." />
                      </th>
                      <th>
                        p95
                        <Tip text="95th percentile serving latency (ms)." />
                      </th>
                      <th>
                        p99
                        <Tip text="99th percentile serving latency (ms)." />
                      </th>
                      <th>
                        Samples
                        <Tip text="Number of requests measured." />
                      </th>
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
      <td className="num">{p.p50.toFixed(1)} ms</td>
      <td className="num">{p.p95.toFixed(1)} ms</td>
      <td className="num">{p.p99.toFixed(1)} ms</td>
      <td className="num">{p.count.toLocaleString()}</td>
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
