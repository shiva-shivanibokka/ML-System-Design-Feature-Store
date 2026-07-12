"use client";
import { useEffect } from "react";
import { api, Feature } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { humanize, describeFeature } from "@/lib/format";
import { DataState } from "./DataState";
import LineageGraph from "./LineageGraph";
import Tip from "./Tip";

export default function FeatureExplorer() {
  const { data: rows, loading, error, run } = useApi<Feature[]>();

  useEffect(() => {
    run(api.registry());
  }, [run]);

  return (
    <div className="stack">
      <div className="stack-head">
        <div className="stack-head-text">
          <h2 className="stack-title">
            Feature Registry
            <Tip text="Every feature currently registered in the feature store, with its type, source table, owner, and tags." />
          </h2>
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
          <span className="section-label">
            Registered features
            <Tip text="Full feature registry — one row per feature defined in feature_store/features.py." />
          </span>
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
                  <th>
                    Feature
                    <Tip text="The feature's key, as used in lookups and training data — hover a name to see its raw key." />
                  </th>
                  <th>
                    Type
                    <Tip text="The feature's data type." />
                  </th>
                  <th>
                    Source table
                    <Tip text="The raw table this feature is computed from." />
                  </th>
                  <th>
                    Owner
                    <Tip text="Team or person responsible for this feature." />
                  </th>
                  <th>
                    Tags
                    <Tip text="Free-form labels used to group related features." />
                  </th>
                  <th>
                    Description
                    <Tip text="What this feature measures." />
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows?.map((f) => (
                  <tr key={f.feature_name}>
                    <td className="mono" title={f.feature_name}>
                      {humanize(f.feature_name)}
                    </td>
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
                    <td style={{ color: "var(--text-dim)" }}>
                      {f.description || describeFeature(f.feature_name)}
                    </td>
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
            Lineage graph
            <Tip text="A directed graph showing how raw tables flow into derived features and into the trained model." />
          </span>
        </div>
        <LineageGraph />
      </div>
    </div>
  );
}
