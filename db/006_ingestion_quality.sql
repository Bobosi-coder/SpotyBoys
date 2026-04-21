-- 006_ingestion_quality.sql
-- Tracks events rejected at ingestion for data-quality auditing.
-- Rejected events are never written to the main event tables;
-- this table is the audit trail for the ingestion quality gate.

BEGIN;

CREATE TABLE IF NOT EXISTS app.ingestion_rejections (
    id            BIGSERIAL PRIMARY KEY,
    event_type    TEXT        NOT NULL,   -- 'impression' | 'playback' | 'feedback'
    reasons       TEXT[]      NOT NULL,   -- list of human-readable rejection reasons
    raw_payload   JSONB,                  -- original request body for forensics
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_rejections_event_type
    ON app.ingestion_rejections (event_type);

CREATE INDEX IF NOT EXISTS idx_ingestion_rejections_created_at
    ON app.ingestion_rejections (created_at DESC);

COMMIT;
