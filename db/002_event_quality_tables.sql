-- =====================================================
-- 002_event_quality_tables.sql
-- Event storage and ingestion quality audit tables
-- =====================================================

BEGIN;

-- Ingestion rejections: events that failed validation before hitting main tables
CREATE TABLE IF NOT EXISTS app.ingestion_rejections (
    id          BIGSERIAL   PRIMARY KEY,
    event_type  TEXT        NOT NULL,   -- 'playback' | 'navidrome_playback' | 'feedback'
    reasons     TEXT[]      NOT NULL,
    raw_payload JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_rejections_event_type
    ON app.ingestion_rejections (event_type);
CREATE INDEX IF NOT EXISTS idx_ingestion_rejections_created_at
    ON app.ingestion_rejections (created_at DESC);

-- Recommendation impressions: what the model returned per request
CREATE TABLE IF NOT EXISTS app.recommendation_impressions (
    impression_id       TEXT        PRIMARY KEY,
    request_id          TEXT        NOT NULL,
    session_id          TEXT        NOT NULL,
    user_id             TEXT        NOT NULL,
    model_version       TEXT        NOT NULL,
    fallback_level      TEXT        NOT NULL,
    browse_surface_json JSONB       NOT NULL,
    queue_json          JSONB       NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_recommendation_impressions_session
    ON app.recommendation_impressions (session_id, created_at);

-- Rendered impressions: what the UI actually showed (for live-data-monitor-worker)
CREATE TABLE IF NOT EXISTS app.rendered_impressions (
    impression_id       TEXT        PRIMARY KEY,
    request_id          TEXT        NOT NULL,
    session_id          TEXT        NOT NULL,
    user_id             TEXT        NOT NULL,
    surface             TEXT        NOT NULL,
    visible_items_json  JSONB       NOT NULL,
    rendered_at         TIMESTAMPTZ NOT NULL,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rendered_impressions_received_at
    ON app.rendered_impressions (received_at DESC);

-- Feedback events: like / dislike signals from users
CREATE TABLE IF NOT EXISTS app.feedback_events (
    event_id        TEXT        PRIMARY KEY,
    feedback_type   TEXT        NOT NULL,   -- 'like' | 'dislike'
    session_id      TEXT        NOT NULL,
    user_id         TEXT        NOT NULL,
    track_id        TEXT        NOT NULL,
    impression_id   TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_feedback_events_occurred_at
    ON app.feedback_events (occurred_at DESC);

COMMIT;
