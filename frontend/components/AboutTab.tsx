import Tip from "./Tip";

/**
 * Plain-English explainer tab: what this project is, how data flows
 * through it, what each tab shows, and the tech stack. Static content —
 * no API calls, so no useApi/DataState here.
 */
export default function AboutTab() {
  return (
    <div className="stack">
      <div className="stack-head">
        <div className="stack-head-text">
          <h2 className="stack-title">
            About This Project
            <Tip text="A plain-English explanation of what this feature store demonstrates and how it's built." />
          </h2>
          <p className="stack-sub">
            An end-to-end ML feature store built to demonstrate one thing above all: preventing
            training-serving skew.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">What this is</span>
        </div>
        <div className="card-body about">
          <p>
            <strong>Training-serving skew</strong> — features computed one way when a model is
            trained, and a subtly different way when the model serves live predictions — is
            widely considered the #1 silent bug in production ML. A model can look great in
            offline evaluation and quietly degrade in production because the inputs it sees at
            serve time don&rsquo;t match what it learned from. This project is a working,
            end-to-end feature store built specifically to make that bug structurally
            impossible: every feature is defined exactly once, and that single definition is
            reused everywhere a feature value is produced.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">Architecture &amp; data flow</span>
        </div>
        <div className="card-body about">
          <div className="about-flow">
            <span>Raw tables</span>
            <span className="arrow">→</span>
            <span className="mono">feature_store/features.py</span>
            <span className="arrow">→</span>
            <span>offline store</span>
            <span className="arrow">→</span>
            <span>online store</span>
            <span className="arrow">→</span>
            <span>FastAPI backend</span>
            <span className="arrow">→</span>
            <span>this dashboard</span>
          </div>
          <ul>
            <li>
              <strong>Single feature-computation SQL</strong> — one module,{" "}
              <code className="mono">feature_store/features.py</code>, is reused by offline
              backfill, on-demand serving, and training. There is no second implementation to
              drift out of sync.
            </li>
            <li>
              <strong>Offline store — MotherDuck / DuckDB</strong> — training data is built with
              point-in-time <strong>ASOF joins</strong>, so a training row only ever sees feature
              values as they existed at that row&rsquo;s timestamp. No future data ever leaks
              into a training example.
            </li>
            <li>
              <strong>Online store — Aiven Valkey</strong> — the same feature definitions are
              materialized here for millisecond-latency lookups at serving time.
            </li>
            <li>
              <strong>Backend — FastAPI on Google Cloud Run</strong> — serves the registry,
              lineage, skew report, materialization log, metrics, and the batch feature-fetch
              endpoint this dashboard calls.
            </li>
            <li>
              <strong>Frontend — this Next.js app on Vercel</strong> — the dashboard you&rsquo;re
              looking at.
            </li>
            <li>
              <strong>Batch jobs — GitHub Actions</strong> — scheduled backfill, materialization,
              and training runs.
            </li>
          </ul>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">What each tab shows</span>
        </div>
        <div className="card-body about">
          <ul>
            <li>
              <strong>Feature Explorer</strong> — every feature currently registered, where it
              comes from, and how it flows from raw tables through to the model.
            </li>
            <li>
              <strong>Training Pull</strong> — fetch live features for a batch of entity IDs the
              same way a training job would, and see whether each value came from the online
              store or was computed on demand.
            </li>
            <li>
              <strong>Skew Report</strong> — statistical comparison of the training-time feature
              distribution against the live serving distribution, flagging features that have
              drifted.
            </li>
            <li>
              <strong>Materialization</strong> — the log of batch runs that push offline features
              into the online store, plus serving latency percentiles.
            </li>
          </ul>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">Training</span>
        </div>
        <div className="card-body about">
          <p>
            A <strong>LightGBM churn-prediction model</strong> is trained on point-in-time-correct
            features built with an ASOF join, so no future data leaks into training — it reaches
            an <strong>ROC-AUC ≈ 0.98</strong>. To make the danger concrete, the project also ships
            a deliberate leakage demo: a naive (non-point-in-time) join inflates offline AUC to
            roughly 1.0, then collapses once the same model sees production traffic — a
            side-by-side illustration of exactly the failure this project exists to prevent.
            Training also captures the training-time feature distribution used as the baseline
            for the Skew Report. Experiment tracking runs on <strong>MLflow</strong> (local{" "}
            <code className="mono">./mlruns</code> in this demo, DagsHub-ready for a hosted
            tracking server).
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">Tech stack</span>
        </div>
        <div className="card-body">
          <div className="about-stack">
            {[
              "Python",
              "FastAPI",
              "MotherDuck / DuckDB",
              "Aiven Valkey",
              "LightGBM",
              "MLflow",
              "GitHub Actions",
              "Google Cloud Run",
              "Next.js",
              "React",
              "TypeScript",
              "Vercel",
            ].map((t) => (
              <span key={t} className="tag">
                {t}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
