"use client";
import { FormEvent, useState } from "react";
import { api, BatchResult } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { DataState } from "./DataState";

export default function TrainingPull() {
  const [input, setInput] = useState("1, 2, 3, 4, 5");
  const { data, loading, error, run } = useApi<BatchResult>(false);

  const ids = parseIds(input);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (ids.length === 0) return;
    run(api.batch(ids));
  }

  const featureCols = data ? uniqueFeatureCols(data.results) : [];

  return (
    <div className="stack">
      <div className="stack-head">
        <div className="stack-head-text">
          <h2 className="stack-title">Training Pull</h2>
          <p className="stack-sub">
            Fetch current features for a batch of entities the way a training job would — served from
            the online store when materialized, computed on demand otherwise.
          </p>
        </div>
        <span className="chip mono">POST /features/batch</span>
      </div>

      <form className="field-form" onSubmit={submit}>
        <label className="field-label" htmlFor="entity-ids">
          Entity IDs (comma-separated)
        </label>
        <div className="field-row">
          <input
            id="entity-ids"
            className="text-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="1, 2, 3"
            spellCheck={false}
          />
          <button type="submit" className="btn-primary" disabled={ids.length === 0 || loading}>
            {loading ? "Pulling…" : "Pull features"}
          </button>
        </div>
        {ids.length === 0 && input.trim() !== "" && (
          <p className="field-hint">Enter numeric entity IDs separated by commas.</p>
        )}
      </form>

      <div className="card">
        <div className="card-head">
          <span className="section-label">Results</span>
        </div>
        <DataState
          loading={loading}
          error={error}
          empty={!loading && !error && !data}
          emptyMessage="Enter entity IDs above and pull to see results."
          onRetry={() => run(api.batch(ids))}
        >
          {data && (
            <>
              <div style={{ padding: "16px 18px 0" }}>
                <div className="tiles">
                  <div className="tile">
                    <div className="tile-v">{data.hits}</div>
                    <div className="tile-k">Online hits</div>
                  </div>
                  <div className="tile">
                    <div className="tile-v">{data.on_demand_computed}</div>
                    <div className="tile-k">Computed on demand</div>
                  </div>
                  <div className="tile">
                    <div className="tile-v">{data.misses}</div>
                    <div className="tile-k">Misses</div>
                  </div>
                  <div className="tile">
                    <div className="tile-v">{data.latency_ms.toFixed(1)}</div>
                    <div className="tile-k">Latency (ms)</div>
                  </div>
                </div>
              </div>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Entity</th>
                      {featureCols.map((c) => (
                        <th key={c}>{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(data.results).map(([entityId, features]) => (
                      <tr key={entityId}>
                        <td className="mono">{entityId}</td>
                        {features ? (
                          featureCols.map((c) => (
                            <td key={c} className="num">
                              {features[c] !== undefined ? formatNum(features[c]) : "—"}
                            </td>
                          ))
                        ) : (
                          <td colSpan={featureCols.length} className="miss-cell">
                            no features available
                          </td>
                        )}
                      </tr>
                    ))}
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

function parseIds(raw: string): number[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isFinite(n));
}

function uniqueFeatureCols(results: BatchResult["results"]): string[] {
  const cols = new Set<string>();
  for (const v of Object.values(results)) {
    if (v) for (const k of Object.keys(v)) cols.add(k);
  }
  return Array.from(cols);
}

function formatNum(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(3);
}
