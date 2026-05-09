-- =============================================================================
-- ClickHouse schema — ML Feature Store
-- =============================================================================
-- Tables:
--   raw_users            raw user profile data (source of truth)
--   raw_transactions     raw transaction events
--   raw_support_tickets  raw support event data
--   feature_history      immutable append-only feature log (offline store)
--   feature_registry     feature definitions + lineage metadata
--   lineage_edges        directed edges for lineage DAG
--   materialization_log  audit trail for every materialization run
--   skew_snapshots       training vs serving distribution snapshots
-- =============================================================================

CREATE DATABASE IF NOT EXISTS feature_store;

-- ---------------------------------------------------------------------------
-- Raw source tables — simulate upstream data warehouse tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feature_store.raw_users
(
    user_id         UInt32,
    signup_date     Date,
    country         LowCardinality(String),
    plan_type       LowCardinality(String),   -- free | basic | pro | enterprise
    age_bucket      LowCardinality(String),   -- 18-24 | 25-34 | 35-44 | 45+
    created_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(created_at)
ORDER BY user_id;

CREATE TABLE IF NOT EXISTS feature_store.raw_transactions
(
    transaction_id  UInt64,
    user_id         UInt32,
    amount          Float32,
    category        LowCardinality(String),
    status          LowCardinality(String),   -- success | failed | refunded
    event_time      DateTime
)
ENGINE = MergeTree()
ORDER BY (user_id, event_time)
PARTITION BY toYYYYMM(event_time);

CREATE TABLE IF NOT EXISTS feature_store.raw_support_tickets
(
    ticket_id       UInt64,
    user_id         UInt32,
    severity        LowCardinality(String),   -- low | medium | high | critical
    resolved        UInt8,                     -- 0 | 1
    event_time      DateTime
)
ENGINE = MergeTree()
ORDER BY (user_id, event_time)
PARTITION BY toYYYYMM(event_time);

-- ---------------------------------------------------------------------------
-- Offline feature store — immutable, append-only, versioned
-- Every feature computation writes a new row with its feature_version.
-- Point-in-time joins query: WHERE event_time <= label_timestamp
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feature_store.feature_history
(
    entity_id           UInt32,
    entity_type         LowCardinality(String),  -- user | item
    feature_version     LowCardinality(String),  -- v1 | v2 | ...
    event_time          DateTime,
    -- User behavioral features
    txn_count_7d        Float32,
    txn_count_30d       Float32,
    txn_count_90d       Float32,
    total_spend_7d      Float32,
    total_spend_30d     Float32,
    total_spend_90d     Float32,
    avg_txn_amount_30d  Float32,
    failed_txn_rate_30d Float32,
    days_since_last_txn Float32,
    -- Support features
    open_tickets        Float32,
    ticket_rate_30d     Float32,
    -- Profile features
    account_age_days    Float32,
    plan_encoded        Float32,   -- ordinal: free=0 basic=1 pro=2 enterprise=3
    -- Metadata
    computed_at         DateTime DEFAULT now()
)
ENGINE = MergeTree()
ORDER BY (entity_id, feature_version, event_time)
PARTITION BY toYYYYMM(event_time)
TTL event_time + INTERVAL 2 YEAR;

-- ---------------------------------------------------------------------------
-- Feature registry — single source of truth for feature definitions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feature_store.feature_registry
(
    feature_name        String,
    feature_version     LowCardinality(String),
    entity_type         LowCardinality(String),
    dtype               LowCardinality(String),   -- float32 | int32 | string
    description         String,
    source_table        String,
    transformation      String,   -- SQL expression used to compute the feature
    owner               String,
    tags                Array(String),
    is_active           UInt8 DEFAULT 1,
    created_at          DateTime DEFAULT now(),
    deprecated_at       Nullable(DateTime)
)
ENGINE = ReplacingMergeTree(created_at)
ORDER BY (feature_name, feature_version);

-- ---------------------------------------------------------------------------
-- Lineage edges — directed acyclic graph of feature provenance
-- source_node → target_node with edge_type label
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feature_store.lineage_edges
(
    source_node     String,   -- raw table name or upstream feature name
    target_node     String,   -- feature name this edge feeds into
    edge_type       LowCardinality(String),  -- source | transform | model_input
    feature_version LowCardinality(String),
    created_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(created_at)
ORDER BY (source_node, target_node, feature_version);

-- ---------------------------------------------------------------------------
-- Materialization audit log
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feature_store.materialization_log
(
    run_id              String,
    feature_version     LowCardinality(String),
    entity_type         LowCardinality(String),
    entities_processed  UInt32,
    entities_failed     UInt32,
    duration_ms         UInt32,
    status              LowCardinality(String),  -- success | partial | failed
    error_message       Nullable(String),
    started_at          DateTime,
    completed_at        DateTime DEFAULT now()
)
ENGINE = MergeTree()
ORDER BY completed_at;

-- ---------------------------------------------------------------------------
-- Skew snapshots — statistical snapshots of feature distributions
-- Captured at training time and at serving time for KS-test comparison
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feature_store.skew_snapshots
(
    snapshot_id     String,
    feature_name    String,
    feature_version LowCardinality(String),
    context         LowCardinality(String),  -- training | serving
    mean            Float64,
    std             Float64,
    p25             Float64,
    p50             Float64,
    p75             Float64,
    p95             Float64,
    null_rate       Float64,
    sample_count    UInt32,
    captured_at     DateTime DEFAULT now()
)
ENGINE = MergeTree()
ORDER BY (feature_name, feature_version, context, captured_at);
