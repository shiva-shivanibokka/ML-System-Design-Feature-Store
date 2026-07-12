"use client";
import { useState } from "react";
import StatusPill from "@/components/StatusPill";
import FeatureExplorer from "@/components/FeatureExplorer";
import TrainingPull from "@/components/TrainingPull";
import SkewReport from "@/components/SkewReport";
import MaterializationLog from "@/components/MaterializationLog";

const TABS = [
  { id: "explorer", number: "01", label: "Explorer", Component: FeatureExplorer },
  { id: "training", number: "02", label: "Training Pull", Component: TrainingPull },
  { id: "skew", number: "03", label: "Skew Report", Component: SkewReport },
  { id: "materialization", number: "04", label: "Materialization", Component: MaterializationLog },
] as const;

export default function Home() {
  const [activeId, setActiveId] = useState<(typeof TABS)[number]["id"]>("explorer");
  const active = TABS.find((t) => t.id === activeId) ?? TABS[0];
  const Active = active.Component;

  return (
    <main className="wrap">
      <header className="hero">
        <h1>Feature Store</h1>
        <p>
          A point-in-time correct feature platform: <strong>MotherDuck (DuckDB)</strong> computes and
          backfills every feature offline, <strong>Aiven Valkey</strong> serves it online in
          milliseconds, and one SQL module — <span className="mono">feature_store/features.py</span> —
          defines each feature exactly once, so training and serving can never drift apart.
        </p>
        <div className="live-row">
          <StatusPill />
        </div>
      </header>

      <nav className="tabs" role="tablist" aria-label="Dashboard sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            className="tab"
            aria-selected={t.id === activeId}
            onClick={() => setActiveId(t.id)}
          >
            <span className="tab-num">{t.number}</span>
            {t.label}
          </button>
        ))}
      </nav>

      <section className="stage" role="tabpanel">
        <Active />
      </section>

      <p className="footer">Built by Shivani Bokka</p>
    </main>
  );
}
