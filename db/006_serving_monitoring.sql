-- =====================================================
-- 006_serving_monitoring.sql
-- Derived serving monitoring rollups and trigger decisions
-- =====================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS app;

ALTER TABLE app.model_versions
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'inactive';
ALTER TABLE app.model_versions
    ADD COLUMN IF NOT EXISTS rollback_parent_version TEXT;
ALTER TABLE app.model_versions
    ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS app.serving_request_metrics (
    metric_id TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    request_id TEXT,
    session_id TEXT,
    user_id TEXT,
    model_version TEXT,
    serving_bundle_version TEXT,
    status TEXT NOT NULL,
    status_code INTEGER,
    latency_ms REAL NOT NULL CHECK (latency_ms >= 0),
    latency_c2_ms REAL,
    latency_c3_ms REAL,
    latency_c4_ms REAL,
    candidate_count INTEGER,
    final_count INTEGER,
    playable_drop_count INTEGER,
    fallback_level TEXT,
    error_type TEXT,
    metrics_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.serving_metric_rollups (
    rollup_id TEXT PRIMARY KEY,
    window_name TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    model_version TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    recommendation_request_count INTEGER NOT NULL DEFAULT 0,
    stream_request_count INTEGER NOT NULL DEFAULT 0,
    error_rate REAL NOT NULL DEFAULT 0,
    fallback_rate REAL NOT NULL DEFAULT 0,
    p50_latency_ms REAL,
    p95_latency_ms REAL,
    stream_failure_rate REAL NOT NULL DEFAULT 0,
    event_ingestion_count INTEGER NOT NULL DEFAULT 0,
    impression_count INTEGER NOT NULL DEFAULT 0,
    playback_start_count INTEGER NOT NULL DEFAULT 0,
    skip_rate REAL NOT NULL DEFAULT 0,
    completion_rate REAL NOT NULL DEFAULT 0,
    dislike_rate REAL NOT NULL DEFAULT 0,
    unique_track_count INTEGER NOT NULL DEFAULT 0,
    unique_artist_count INTEGER NOT NULL DEFAULT 0,
    top_artist_share REAL NOT NULL DEFAULT 0,
    repeat_violation_count INTEGER NOT NULL DEFAULT 0,
    sample_status TEXT NOT NULL DEFAULT 'insufficient_sample',
    metrics_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.model_trigger_decisions (
    decision_id TEXT PRIMARY KEY,
    decision_type TEXT NOT NULL CHECK (decision_type IN ('promotion', 'rollback')),
    model_version TEXT,
    candidate_version TEXT,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    metrics_json JSONB NOT NULL DEFAULT '{}',
    artifact_uri TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_serving_request_metrics_created
    ON app.serving_request_metrics(created_at);
CREATE INDEX IF NOT EXISTS idx_app_serving_request_metrics_endpoint
    ON app.serving_request_metrics(endpoint, created_at);
CREATE INDEX IF NOT EXISTS idx_app_serving_request_metrics_model
    ON app.serving_request_metrics(model_version, created_at);
CREATE INDEX IF NOT EXISTS idx_app_serving_metric_rollups_created
    ON app.serving_metric_rollups(window_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_model_trigger_decisions_created
    ON app.model_trigger_decisions(decision_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_model_versions_status
    ON app.model_versions(status, activated_at DESC);

COMMIT;
