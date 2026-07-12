"use client";
import { useEffect } from "react";
import { api, Feature } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { DataState } from "./DataState";
import LineageGraph from "./LineageGraph";

export default function FeatureExplorer() {
  const { data: rows, loading, error, run } = useApi<Feature[]>();

  useEffect(() => {
    run(api.registry());
  }, [run]);

  return (
    <div className="stack">
      <div className="stack-head">
        <div className="stack-head-text">
          <h2 className="stack-title">Feature Registry</h2>
          <p className="stack-sub">
            Every feature currently defined in <code className="mono">feature_store/features.py</code>.
          </p>
        </div>
        <span className="chip">registry · v1</span>
      </div>

      <p className="callout">
        <strong>One SQL module, every path.</strong> Offline backfill, on-demand serving, and
        point-in-time training joins all call the same feature definitions, so serving can never
        drift from training.
      </p>

      <div className="card">
        <div className="card-head">
          <span className="section-label">Registered features</span>
        </div>
        <DataState
          loading={loading}
          error={error}
          empty={!!rows && rows.length === 0}
          emptyMessage="No features registered yet — run the registry sync."
          onRetry={() => run(api.registry())}
        >
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Feature</th>
                  <th>Type</th>
                  <th>Source table</th>
                  <th>Owner</th>
                  <th>Tags</th>
                  <th>Description</th>
                </tr>
              </thead>
              <tbody>
                {rows?.map((f) => (
                  <tr key={f.feature_name}>
                    <td className="mono">{f.feature_name}</td>
                    <td className="num">{f.dtype}</td>
                    <td className="mono">{f.source_table}</td>
                    <td>{f.owner}</td>
                    <td>
                      {f.tags.map((t) => (
                        <span key={t} className="tag">
                          {t}
                        </span>
                      ))}
                    </td>
                    <td style={{ color: "var(--text-dim)" }}>{f.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">Lineage graph</span>
        </div>
        <LineageGraph />
      </div>
    </div>
  );
}
