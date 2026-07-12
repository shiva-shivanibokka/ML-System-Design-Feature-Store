"use client";
import { FormEvent, useState } from "react";
import { api, BatchResult } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { DataState } from "./DataState";
import shared from "./shared.module.css";
import styles from "./TrainingPull.module.css";

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
    <div className={shared.panel}>
      <div className={shared.panelHeader}>
        <h2 className={shared.panelTitle}>Training Pull</h2>
        <p className={shared.panelSubtitle}>
          Fetch current features for a batch of entities the way a training job would — served from the
          online store when materialized, computed on demand otherwise, through{" "}
          <code className="mono">POST /features/batch</code>.
        </p>
      </div>

      <form className={styles.form} onSubmit={submit}>
        <label className={styles.label} htmlFor="entity-ids">
          Entity IDs (comma-separated)
        </label>
        <div className={styles.row}>
          <input
            id="entity-ids"
            className={styles.input}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="1, 2, 3"
            spellCheck={false}
          />
          <button type="submit" className={styles.submit} disabled={ids.length === 0 || loading}>
            {loading ? "Pulling…" : "Pull features"}
          </button>
        </div>
        {ids.length === 0 && input.trim() !== "" && (
          <p className={styles.hint}>Enter numeric entity IDs separated by commas.</p>
        )}
      </form>

      <div className={shared.card}>
        <div className={shared.cardHeader}>
          <span className={shared.cardHeaderTitle}>Results</span>
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
              <div className={styles.summaryWrap}>
                <div className={shared.summaryStrip}>
                  <span>
                    <strong>{data.hits}</strong> online-store hits
                  </span>
                  <span>
                    <strong>{data.on_demand_computed}</strong> computed on demand
                  </span>
                  <span>
                    <strong>{data.misses}</strong> misses
                  </span>
                  <span>
                    <strong>{data.latency_ms.toFixed(1)} ms</strong> total latency
                  </span>
                </div>
              </div>
              <div className={shared.tableWrap}>
                <table className={shared.table}>
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
                            <td key={c} className={shared.numeric}>
                              {features[c] !== undefined ? formatNum(features[c]) : "—"}
                            </td>
                          ))
                        ) : (
                          <td colSpan={featureCols.length} className={styles.missRow}>
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
