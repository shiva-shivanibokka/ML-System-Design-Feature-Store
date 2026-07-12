import Tip from "./Tip";

/**
 * Plain-English explainer tab: the problem this project solves, how every
 * major design decision traces back to that problem, what each tab shows,
 * and the full tech stack. Static content — no API calls, so no
 * useApi/DataState here. Deliberately long-form and unbounded in height;
 * it scrolls with the page like any other prose page, never in its own
 * clipped box.
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
            serve time don&rsquo;t match what it learned from. There&rsquo;s no stack trace for
            this failure; the service stays up, requests still return 200s, and the only symptom
            is a slow, hard-to-diagnose drop in prediction quality. This project is a working,
            end-to-end feature store built specifically to make that bug structurally impossible:
            every feature is defined exactly once, and that single definition is reused
            everywhere a feature value is produced — offline backfill, online materialization,
            and on-demand compute all call the same code.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">
            The problem: training-serving skew
            <Tip text="Why two implementations of the same feature logic are the most common way ML systems quietly break." />
          </span>
        </div>
        <div className="card-body about">
          <p>
            Skew creeps in whenever a feature has two implementations that are supposed to agree
            but don&rsquo;t. It rarely happens on purpose — it happens because the training
            pipeline and the serving path are usually built by different code, at different
            times, sometimes by different people, and &ldquo;compute the same number&rdquo; turns
            out to be a much easier promise to break than it sounds.
          </p>
          <p className="callout">
            <strong>Before (skewed):</strong> a nightly Spark/SQL job computes{" "}
            <code className="mono">total_spend_30d</code> for training using a UTC-midnight
            30-day window. The serving path, written separately in application code for latency
            reasons, computes the &ldquo;same&rdquo; feature live using the server&rsquo;s local
            clock and a slightly different rounding rule. For most users the two numbers happen
            to match. For anyone near the day boundary, or with a transaction that rounds
            differently, they don&rsquo;t — a user showing <code className="mono">$0</code> spent
            in training might show <code className="mono">$42</code> at serving. Offline the
            model reports a healthy ROC-AUC, because training data is internally consistent with
            itself. In production, the model is quietly evaluating a distribution it never
            trained on, and nobody gets paged.
          </p>
          <p>
            <strong>After (this project):</strong> both paths import and execute the same
            function from <code className="mono">feature_store/features.py</code>. There is no
            second implementation to drift out of sync, so this specific class of bug is closed
            off by construction rather than caught by testing.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">
            Point-in-time correctness &amp; the ASOF join
            <Tip text="Why a plain join on entity ID leaks future information into training data." />
          </span>
        </div>
        <div className="card-body about">
          <p>
            Training examples are built by joining each labeled event (e.g. &ldquo;did this user
            churn?&rdquo;) to the feature values as they existed <em>at that event&rsquo;s
            timestamp</em> — not the feature values as they exist today. In DuckDB this is an{" "}
            <code className="mono">ASOF JOIN</code>: for every label row, it finds the most recent
            feature snapshot at or before that row&rsquo;s timestamp, and nothing after it.
          </p>
          <p>
            A naive join on entity ID alone doesn&rsquo;t have that time boundary — it matches
            each label to whatever the <em>current</em> feature row happens to be, regardless of
            when the label was actually observed. A churn label from January can end up joined to
            a transaction-count feature computed in June, silently handing the model information
            that didn&rsquo;t exist yet at prediction time. This is the textbook definition of
            label leakage, and it&rsquo;s the reason a leaky offline model can look almost
            perfect and then fall apart the moment it meets real, unlabeled, present-tense data —
            see the leakage demo in the Training section below.
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
          <span className="section-label">
            Why two stores: MotherDuck vs. Valkey
            <Tip text="Why an OLAP warehouse and an in-memory key-value store each do a job the other one is bad at." />
          </span>
        </div>
        <div className="card-body about">
          <p>
            <strong>MotherDuck (DuckDB)</strong> is a columnar analytical warehouse — excellent at
            scanning millions of historical rows to backfill features or run a point-in-time ASOF
            join, but not built to answer &ldquo;give me this one user&rsquo;s features&rdquo; in
            single-digit milliseconds under load. <strong>Aiven Valkey</strong> (a Redis-compatible
            in-memory store) is the opposite: a hash lookup by entity ID is a sub-millisecond
            operation, but it has no facility for scanning or joining large historical tables.
          </p>
          <p>
            Rather than force one system to do both jobs badly, the pipeline computes and
            backfills in the store built for analytical scans, then materializes just the current
            value for every entity into the store built for point lookups. Two stores, each doing
            the one thing it&rsquo;s actually good at.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">
            Dual serving paths
            <Tip text="What happens when a requested entity is already materialized versus when it isn't." />
          </span>
        </div>
        <div className="card-body about">
          <p>
            Most feature requests hit the fast path: the entity was already materialized into
            Valkey by the last scheduled run, so the API returns the cached value directly — an{" "}
            <strong>online-store hit</strong>. If an entity hasn&rsquo;t been materialized yet
            (a brand-new user, or a cache that hasn&rsquo;t caught up), the API falls back to
            computing the feature <strong>on demand</strong> straight from the offline store,
            using the exact same feature definitions, rather than returning nothing. This is
            precisely what the Training Pull tab&rsquo;s hits / computed-on-demand / misses tiles
            are showing: a live count of which path served each entity in that batch.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">
            Materialization pipeline &amp; scheduling
            <Tip text="The scheduled job that keeps the online store's cached values fresh." />
          </span>
        </div>
        <div className="card-body about">
          <p>
            A <strong>GitHub Actions</strong> workflow runs on a schedule, recomputing every
            feature for every known entity from the offline store and writing the current values
            into Valkey. Each run is logged with entities processed, entities failed, and
            duration — visible on the Materialization tab, along with serving-latency percentiles
            for every path. At this scale, a scheduled GitHub Actions job is a complete,
            free-tier substitute for a dedicated orchestrator like Airflow — no separate
            scheduler to run or pay for.
          </p>
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
            side-by-side illustration of exactly the failure this project exists to prevent. A
            model that scores 1.0 offline is a red flag, not a triumph; it usually means the
            answer key leaked into the exam.
          </p>
          <p>
            The same training run also captures a snapshot of the training-time feature
            distribution — the mean, and enough shape to run a statistical test against — which
            becomes the baseline the Skew Report compares live serving data to. Experiment
            tracking runs on <strong>MLflow</strong> (local <code className="mono">./mlruns</code>{" "}
            in this demo, DagsHub-ready for a hosted tracking server), so every run&rsquo;s
            parameters, metrics, and artifacts are reproducible.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">
            Data validation — Pandera
            <Tip text="Schema checks that catch bad upstream data before it reaches training or serving." />
          </span>
        </div>
        <div className="card-body about">
          <p>
            Before a batch enters the training set or a materialization run writes to Valkey, it
            passes through a <strong>Pandera</strong> schema check — types, null-ability, and
            value ranges (for example, <code className="mono">failed_txn_rate_30d</code> must fall
            within <code className="mono">[0, 1]</code>). If upstream data breaks a contract, the
            pipeline fails loudly and immediately at that stage, instead of silently writing bad
            values into a trained model or an online lookup where the failure would surface much
            later, and much more expensively.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">
            Skew detection — the KS test
            <Tip text="The statistical test behind the Skew Report tab." />
          </span>
        </div>
        <div className="card-body about">
          <p>
            The Skew Report runs a two-sample <strong>Kolmogorov–Smirnov (KS) test</strong> for
            every feature, comparing the training-time distribution snapshot against the current
            live serving distribution. The KS statistic is the largest gap between their two
            cumulative distributions — 0 means identical, larger means more drift — and a feature
            is flagged when that gap is statistically significant at{" "}
            <code className="mono">p &lt; 0.05</code>. It&rsquo;s the same idea as the
            before/after example above, made measurable: instead of hoping training and serving
            still agree, this continuously checks.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="section-label">
            Feature lineage
            <Tip text="What the lineage graph on the Explorer tab is for." />
          </span>
        </div>
        <div className="card-body about">
          <p>
            The lineage graph on the Explorer tab traces the dependency chain from raw tables,
            through derived features, to the model that consumes them. It&rsquo;s the audit trail
            for a very practical question: if a raw table&rsquo;s schema is about to change, what
            actually breaks downstream? Pick a model and trace it backward, and every feature and
            source table it ultimately depends on is right there — no spelunking through code to
            find out.
          </p>
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
          <span className="section-label">
            Tech stack &amp; deployment
            <Tip text="Every piece runs on a free tier — a production-shaped system doesn't require production-scale spend." />
          </span>
        </div>
        <div className="card-body about">
          <p>
            The whole system is deployed entirely on free tiers, by design: <strong>MotherDuck</strong>&rsquo;s
            free tier for the offline warehouse, <strong>Aiven</strong>&rsquo;s free plan for
            Valkey, <strong>FastAPI</strong> on <strong>Google Cloud Run</strong> (scale-to-zero,
            pay-per-request — comfortably inside the free tier at this traffic level), this
            dashboard on <strong>Vercel</strong>&rsquo;s free hobby plan, and{" "}
            <strong>GitHub Actions</strong>&rsquo; free minutes for scheduled backfill,
            materialization, and training runs. The point is that a system shaped like a real
            production feature store — with point-in-time correctness, dual stores, scheduled
            materialization, and continuous skew monitoring — doesn&rsquo;t require
            production-scale spend to build or to run.
          </p>
          <div className="about-stack">
            {[
              "Python",
              "FastAPI",
              "MotherDuck / DuckDB",
              "Aiven Valkey",
              "LightGBM",
              "Pandera",
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
