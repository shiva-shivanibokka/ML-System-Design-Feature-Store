"use client";
import { useState } from "react";
import StatusPill from "@/components/StatusPill";
import FeatureExplorer from "@/components/FeatureExplorer";
import TrainingPull from "@/components/TrainingPull";
import SkewReport from "@/components/SkewReport";
import MaterializationLog from "@/components/MaterializationLog";
import styles from "./page.module.css";

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
    <div className={styles.shell}>
      <header className={styles.header}>
        <div className={styles.headerTop}>
          <div className={styles.brand}>
            <span className={styles.brandMark} aria-hidden="true">
              ◆
            </span>
            <span className={styles.brandName}>FEATURESTORE</span>
          </div>
          <StatusPill />
        </div>
        <p className={styles.tagline}>
          An end-to-end ML feature platform — a <strong>DuckDB / MotherDuck</strong> offline store, an{" "}
          <strong>Upstash Redis</strong> online store, point-in-time correct training joins via{" "}
          <span className="mono">ASOF JOIN</span>, and continuous training/serving skew detection. One
          feature definition, reused everywhere it&rsquo;s needed.
        </p>
      </header>

      <nav className={styles.tabs} aria-label="Dashboard sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`${styles.tab} ${t.id === activeId ? styles.tabActive : ""}`}
            onClick={() => setActiveId(t.id)}
            aria-current={t.id === activeId ? "page" : undefined}
          >
            <span className={styles.tabNumber}>{t.number}</span>
            {t.label}
          </button>
        ))}
      </nav>

      <main className={styles.main}>
        <Active />
      </main>

      <footer className={styles.footer}>
        <span className="mono">feature_store/features.py</span> — one SQL module computes every
        feature; offline backfill, on-demand serving, and PIT training joins all call it, so serving
        can never drift from training.
      </footer>
    </div>
  );
}
