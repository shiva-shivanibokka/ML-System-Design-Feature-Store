"use client";
import { useEffect } from "react";
import { api, Feature } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { DataState } from "./DataState";
import LineageGraph from "./LineageGraph";
import shared from "./shared.module.css";

export default function FeatureExplorer() {
  const { data: rows, loading, error, run } = useApi<Feature[]>();

  useEffect(() => {
    run(api.registry());
  }, [run]);

  return (
    <div className={shared.panel}>
      <div className={shared.panelHeader}>
        <h2 className={shared.panelTitle}>Feature Registry</h2>
        <p className={shared.panelSubtitle}>
          Every feature currently defined in <code className="mono">feature_store/features.py</code>
          {" "}— the single SQL module reused by offline compute, on-demand serving, and PIT training joins.
        </p>
      </div>

      <div className={shared.card}>
        <div className={shared.cardHeader}>
          <span className={shared.cardHeaderTitle}>Registered features</span>
        </div>
        <DataState
          loading={loading}
          error={error}
          empty={!!rows && rows.length === 0}
          emptyMessage="No features registered yet — run the registry sync."
          onRetry={() => run(api.registry())}
        >
          <div className={shared.tableWrap}>
            <table className={shared.table}>
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
                    <td className={shared.numeric}>{f.dtype}</td>
                    <td className="mono">{f.source_table}</td>
                    <td>{f.owner}</td>
                    <td>
                      {f.tags.map((t) => (
                        <span key={t} className={shared.tag}>
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

      <div className={shared.card}>
        <div className={shared.cardHeader}>
          <span className={shared.cardHeaderTitle}>Lineage graph</span>
        </div>
        <LineageGraph />
      </div>
    </div>
  );
}
