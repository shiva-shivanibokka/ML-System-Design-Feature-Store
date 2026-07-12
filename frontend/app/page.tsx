"use client";
import { KeyboardEvent, useRef, useState } from "react";
import StatusPill from "@/components/StatusPill";
import FeatureExplorer from "@/components/FeatureExplorer";
import TrainingPull from "@/components/TrainingPull";
import SkewReport from "@/components/SkewReport";
import MaterializationLog from "@/components/MaterializationLog";
import AboutTab from "@/components/AboutTab";

const TABS = [
  { id: "explorer", number: "01", label: "Explorer", Component: FeatureExplorer },
  { id: "training", number: "02", label: "Training Pull", Component: TrainingPull },
  { id: "skew", number: "03", label: "Skew Report", Component: SkewReport },
  { id: "materialization", number: "04", label: "Materialization", Component: MaterializationLog },
  { id: "about", number: "05", label: "About", Component: AboutTab },
] as const;

export default function Home() {
  const [activeId, setActiveId] = useState<(typeof TABS)[number]["id"]>("explorer");
  const active = TABS.find((t) => t.id === activeId) ?? TABS[0];
  const Active = active.Component;
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  function onTabKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = -1;
    if (e.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = TABS.length - 1;
    else return;

    e.preventDefault();
    setActiveId(TABS[next].id);
    tabRefs.current[next]?.focus();
  }

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
        {TABS.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              tabRefs.current[i] = el;
            }}
            id={`tab-${t.id}`}
            type="button"
            role="tab"
            className="tab"
            aria-selected={t.id === activeId}
            aria-controls={`panel-${t.id}`}
            tabIndex={t.id === activeId ? 0 : -1}
            onClick={() => setActiveId(t.id)}
            onKeyDown={(e) => onTabKeyDown(e, i)}
          >
            <span className="tab-num">{t.number}</span>
            {t.label}
          </button>
        ))}
      </nav>

      <section className="stage" role="tabpanel" id={`panel-${active.id}`} aria-labelledby={`tab-${active.id}`}>
        <Active />
      </section>

      <p className="footer">Built by Shivani Bokka</p>
    </main>
  );
}
