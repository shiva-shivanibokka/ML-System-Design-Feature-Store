-- DuckDB / MotherDuck schema for the ML Feature Store.

CREATE TABLE IF NOT EXISTS raw_users (
    user_id     BIGINT,
    signup_date DATE,
    country     VARCHAR,
    plan_type   VARCHAR,
    age_bucket  VARCHAR,
    created_at  TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_transactions (
    transaction_id BIGINT,
    user_id        BIGINT,
    amount         DOUBLE,
    category       VARCHAR,
    status         VARCHAR,
    event_time     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_support_tickets (
    ticket_id  BIGINT,
    user_id    BIGINT,
    severity   VARCHAR,
    resolved   INTEGER,
    event_time TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feature_history (
    entity_id           BIGINT,
    entity_type         VARCHAR,
    feature_version     VARCHAR,
    event_time          TIMESTAMP,
    txn_count_7d        DOUBLE,
    txn_count_30d       DOUBLE,
    txn_count_90d       DOUBLE,
    total_spend_7d      DOUBLE,
    total_spend_30d     DOUBLE,
    total_spend_90d     DOUBLE,
    avg_txn_amount_30d  DOUBLE,
    failed_txn_rate_30d DOUBLE,
    days_since_last_txn DOUBLE,
    open_tickets        DOUBLE,
    ticket_rate_30d     DOUBLE,
    account_age_days    DOUBLE,
    plan_encoded        DOUBLE,
    computed_at         TIMESTAMP DEFAULT now(),
    PRIMARY KEY (entity_id, feature_version, event_time)
);

CREATE TABLE IF NOT EXISTS feature_registry (
    feature_name    VARCHAR,
    feature_version VARCHAR,
    entity_type     VARCHAR,
    dtype           VARCHAR,
    description     VARCHAR,
    source_table    VARCHAR,
    transformation  VARCHAR,
    owner           VARCHAR,
    tags            VARCHAR[],
    is_active       INTEGER DEFAULT 1,
    created_at      TIMESTAMP DEFAULT now(),
    deprecated_at   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lineage_edges (
    source_node     VARCHAR,
    target_node     VARCHAR,
    edge_type       VARCHAR,
    feature_version VARCHAR,
    created_at      TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS materialization_log (
    run_id             VARCHAR,
    feature_version    VARCHAR,
    entity_type        VARCHAR,
    entities_processed BIGINT,
    entities_failed    BIGINT,
    duration_ms        BIGINT,
    status             VARCHAR,
    error_message      VARCHAR,
    started_at         TIMESTAMP,
    completed_at       TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS skew_snapshots (
    snapshot_id     VARCHAR,
    feature_name    VARCHAR,
    feature_version VARCHAR,
    context         VARCHAR,
    mean            DOUBLE,
    std             DOUBLE,
    p25             DOUBLE,
    p50             DOUBLE,
    p75             DOUBLE,
    p95             DOUBLE,
    null_rate       DOUBLE,
    sample_count    BIGINT,
    captured_at     TIMESTAMP DEFAULT now()
);
