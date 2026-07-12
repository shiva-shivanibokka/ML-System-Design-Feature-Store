"use client";
import { useEffect } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, SkewRow } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { DataState } from "./DataState";
import shared from "./shared.module.css";

const KS_THRESHOLD = 0.05;

export default function SkewReport() {
  const { data, loading, error, run } = useApi<{ report: SkewRow[] }>();

  useEffect(() => {
    run(api.skew());
  }, [run]);

  const rows = data?.report ?? [];
  const flaggedCount = rows.filter((r) => r.flagged).length;

  return (
    <div className={shared.panel}>
      <div className={shared.panelHeader}>
        <h2 className={shared.panelTitle}>Training vs. Serving Skew</h2>
        <p className={shared.panelSubtitle}>
          Two-sample Kolmogorov–Smirnov test between the training-time feature distribution and the
          live serving distribution. A feature is flagged when its KS statistic exceeds the p = 0.05
          significance threshold.
        </p>
      </div>

      <div className={shared.card}>
        <div className={shared.cardHeader}>
          <span className={shared.cardHeaderTitle}>KS statistic by feature</span>
          {rows.length > 0 && (
            <span className={flaggedCount > 0 ? shared.badgeFlagged : shared.badgeOk}>
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
          <div style={{ padding: "18px 12px 8px" }}>
            <ResponsiveContainer width="100%" height={340}>
              <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 48, left: 8 }}>
                <CartesianGrid stroke="var(--border)" vertical={false} />
                <XAxis
                  dataKey="feature_name"
                  angle={-35}
                  textAnchor="end"
                  height={90}
                  interval={0}
                  tick={{ fill: "var(--text-dim)", fontSize: 11, fontFamily: "var(--font-mono)" }}
                  axisLine={{ stroke: "var(--border)" }}
                  tickLine={false}
                />
                <YAxis
                  label={{
                    value: "KS statistic",
                    angle: -90,
                    position: "insideLeft",
                    fill: "var(--text-faint)",
                    fontSize: 11,
                  }}
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
                <ReferenceLine
                  y={KS_THRESHOLD}
                  stroke="var(--amber)"
                  strokeDasharray="4 4"
                  label={{
                    value: "p = 0.05",
                    position: "right",
                    fill: "var(--amber)",
                    fontSize: 11,
                  }}
                />
                <Bar dataKey="ks_statistic" radius={[3, 3, 0, 0]}>
                  {rows.map((r) => (
                    <Cell key={r.feature_name} fill={r.flagged ? "var(--rose)" : "var(--cyan)"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </DataState>
      </div>

      {rows.length > 0 && (
        <div className={shared.card}>
          <div className={shared.cardHeader}>
            <span className={shared.cardHeaderTitle}>Per-feature detail</span>
          </div>
          <div className={shared.tableWrap}>
            <table className={shared.table}>
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
                    <td className={shared.numeric}>{r.training_mean.toFixed(3)}</td>
                    <td className={shared.numeric}>{r.serving_mean.toFixed(3)}</td>
                    <td className={shared.numeric}>{r.mean_shift.toFixed(3)}</td>
                    <td className={shared.numeric}>{r.ks_statistic.toFixed(4)}</td>
                    <td className={shared.numeric}>{r.ks_pvalue.toFixed(4)}</td>
                    <td>
                      <span className={r.flagged ? shared.badgeFlagged : shared.badgeOk}>
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
