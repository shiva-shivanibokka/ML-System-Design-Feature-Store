"use client";
import { useEffect } from "react";
import { api, SkewRow } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { DataState } from "./DataState";

const KS_THRESHOLD = 0.05;

export default function SkewReport() {
  const { data, loading, error, run } = useApi<{ report: SkewRow[] }>();

  useEffect(() => {
    run(api.skew());
  }, [run]);

  const rows = data?.report ?? [];
  const flaggedCount = rows.filter((r) => r.flagged).length;
  const maxKs = Math.max(KS_THRESHOLD * 1.4, ...rows.map((r) => r.ks_statistic), 0.001);
  const thresholdPct = Math.min(100, (KS_THRESHOLD / maxKs) * 100);

  return (
    <div className="stack">
      <div className="stack-head">
        <div className="stack-head-text">
          <h2 className="stack-title">Training vs. Serving Skew</h2>
          <p className="stack-sub">
            Two-sample Kolmogorov–Smirnov test between the training-time feature distribution and the
            live serving distribution. A feature is flagged when its KS statistic exceeds the p = 0.05
            significance threshold.
          </p>
        </div>
        <span className="chip">KS test · p = 0.05</span>
      </div>

      {rows.length > 0 && (
        <div className="readout">
          <div className="readout-big">
            {flaggedCount} / {rows.length}
          </div>
          <div className="readout-lbl">features flagged for drift</div>
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <span className="section-label">KS statistic by feature</span>
          {rows.length > 0 && (
            <span className={flaggedCount > 0 ? "badge badge-flagged" : "badge badge-ok"}>
              {flaggedCount > 0 ? `${flaggedCount} flagged` : "no skew detected"}
            </span>
          )}
        </div>
        <DataState
          loading={loading}
          error={error}
          empty={rows.length === 0}
          emptyMessage="No skew data yet — run the training workflow to capture a snapshot."
          onRetry={() => run(api.skew())}
        >
          <div style={{ padding: "18px" }}>
            <div className="bars-legend" style={{ marginBottom: 10 }}>
              <span>
                <span className="swatch" style={{ background: "var(--cyan)" }} />
                stable
              </span>
              <span>
                <span className="swatch" style={{ background: "var(--rose)" }} />
                flagged
              </span>
              <span>
                <span className="swatch" style={{ background: "var(--amber)" }} />
                p = 0.05 threshold
              </span>
            </div>
            <div className="bars">
              {rows.map((r) => (
                <div className="bar-row" key={r.feature_name}>
                  <span className="bar-name" title={r.feature_name}>
                    {r.feature_name}
                  </span>
                  <span className="bar-track">
                    <span
                      className={`bar-fill ${r.flagged ? "rose" : "cyan"}`}
                      style={{ width: `${Math.min(100, (r.ks_statistic / maxKs) * 100)}%` }}
                    />
                    <span className="bar-threshold" style={{ left: `${thresholdPct}%` }} />
                  </span>
                  <span className="bar-val">{r.ks_statistic.toFixed(3)}</span>
                </div>
              ))}
            </div>
          </div>
        </DataState>
      </div>

      {rows.length > 0 && (
        <div className="card">
          <div className="card-head">
            <span className="section-label">Per-feature detail</span>
          </div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Feature</th>
                  <th>Training mean</th>
                  <th>Serving mean</th>
                  <th>Mean shift</th>
                  <th>KS statistic</th>
                  <th>KS p-value</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.feature_name}>
                    <td className="mono">{r.feature_name}</td>
                    <td className="num">{r.training_mean.toFixed(3)}</td>
                    <td className="num">{r.serving_mean.toFixed(3)}</td>
                    <td className="num">{r.mean_shift.toFixed(3)}</td>
                    <td className="num">{r.ks_statistic.toFixed(4)}</td>
                    <td className="num">{r.ks_pvalue.toFixed(4)}</td>
                    <td>
                      <span className={r.flagged ? "badge badge-flagged" : "badge badge-ok"}>
                        {r.flagged ? "flagged" : "stable"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
