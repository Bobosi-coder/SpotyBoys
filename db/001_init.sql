-- =====================================================
-- 001_init.sql — SpotyBoys service schema
-- Single `app` schema, 9 tables
-- =====================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS app;

-- All users: 30Music pre-seeded (user_int_id 1–45175) + online signups (>=100000)
CREATE TABLE IF NOT EXISTS app.users (
    user_id       TEXT PRIMARY KEY,        -- "user_40305" or "user_{uuid}"
    user_int_id   BIGINT NOT NULL UNIQUE,  -- 30Music ID, or nextval from 100000
    email         TEXT NOT NULL UNIQUE,    -- "{uid}@navidrome.local" or user-provided
    password_hash TEXT NOT NULL,           -- PBKDF2-SHA256
    display_name  TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS app.online_user_int_id_seq START 100000;

-- Auth tokens — Bearer token in Authorization header (no cookies)
CREATE TABLE IF NOT EXISTS app.auth_sessions (
    token_hash  TEXT PRIMARY KEY,          -- SHA-256(raw_token), never store raw
    user_id     TEXT NOT NULL REFERENCES app.users(user_id),
    expires_at  TIMESTAMPTZ NOT NULL,      -- 14 days from login
    revoked_at  TIMESTAMPTZ               -- set on logout
);

-- Recommendation sessions (one per page load / bootstrap call)
CREATE TABLE IF NOT EXISTS app.rec_sessions (
    session_id      TEXT PRIMARY KEY,      -- "sess_{uuid}"
    user_int_id     BIGINT NOT NULL,
    session_int_id  BIGINT NOT NULL UNIQUE, -- nextval from 3000000 (above 30Music max 2,764,469)
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS app.session_int_id_seq START 3000000;

-- Playback events (INSERT on playback_start, UPDATE on skip/complete using same event_id)
CREATE TABLE IF NOT EXISTS app.playback_events (
    event_id        TEXT PRIMARY KEY,      -- playback_id from frontend (reused across start/skip/complete)
    session_int_id  BIGINT NOT NULL,
    user_int_id     BIGINT NOT NULL,
    track_id        BIGINT NOT NULL,       -- 30Music track_id from manifest.csv
    position        INT NOT NULL,          -- position in queue (0-based)
    playratio       FLOAT,                 -- NULL on playback_start, set on skip/complete
    event_type      TEXT NOT NULL,         -- "playback_start" | "skip" | "complete"
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE app.playback_events
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_playback_events_session ON app.playback_events (session_int_id);
CREATE INDEX IF NOT EXISTS idx_playback_events_playratio ON app.playback_events (session_int_id) WHERE playratio IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_playback_events_updated_at ON app.playback_events (updated_at);

-- Navidrome stars → 30Music love (synced by delta-export-worker)
CREATE TABLE IF NOT EXISTS app.loved_tracks (
    user_int_id  BIGINT NOT NULL,
    track_id     BIGINT NOT NULL,
    loved_at     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY  (user_int_id, track_id)
);

-- Navidrome playlists (synced by delta-export-worker)
CREATE TABLE IF NOT EXISTS app.user_playlists (
    playlist_int_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_int_id      BIGINT NOT NULL,
    nav_playlist_id  TEXT NOT NULL UNIQUE,  -- Navidrome's internal playlist ID
    name             TEXT,
    synced_at        TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.playlist_tracks (
    playlist_int_id  BIGINT NOT NULL REFERENCES app.user_playlists(playlist_int_id),
    position         INT NOT NULL,
    track_id         BIGINT NOT NULL,
    PRIMARY KEY (playlist_int_id, position)
);

-- Catalog (written by catalog-sync-worker, read by recommendation-api)
CREATE TABLE IF NOT EXISTS app.playable_tracks (
    track_id            TEXT PRIMARY KEY,  -- "1977186" (30Music integer as string)
    title               TEXT NOT NULL,
    artist              TEXT NOT NULL,
    album               TEXT DEFAULT '',
    duration_sec        INT DEFAULT 30,
    cover_art_url       TEXT,              -- "/covers/{track_id}"
    is_playable         BOOLEAN DEFAULT TRUE,
    navidrome_track_id  TEXT UNIQUE,       -- Navidrome's internal ID for stream proxy
    availability_status TEXT DEFAULT 'available',
    quarantine_reason   TEXT
);

-- Model health flag (written by delta-export-worker, read by recommendation-api)
CREATE TABLE IF NOT EXISTS app.model_status (
    id          INT DEFAULT 1 PRIMARY KEY CHECK (id = 1),  -- single-row table
    degraded    BOOLEAN NOT NULL DEFAULT false,
    reason      TEXT,
    action      TEXT NOT NULL DEFAULT 'normal',
    updated_at  TIMESTAMPTZ DEFAULT now()
);

INSERT INTO app.model_status (degraded) VALUES (false)
ON CONFLICT (id) DO NOTHING;

ALTER TABLE app.model_status
    ADD COLUMN IF NOT EXISTS action TEXT NOT NULL DEFAULT 'normal';

CREATE TABLE IF NOT EXISTS app.serving_request_metrics (
    metric_id           TEXT PRIMARY KEY,
    request_id          TEXT,
    session_id          TEXT,
    user_id             TEXT,
    model_version       TEXT,
    fallback_level      TEXT NOT NULL DEFAULT 'none',
    fallback_state      TEXT NOT NULL DEFAULT 'healthy',
    candidate_count     INT NOT NULL DEFAULT 0,
    playable_count      INT NOT NULL DEFAULT 0,
    returned_count      INT NOT NULL DEFAULT 0,
    pipeline_latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    total_latency_ms    DOUBLE PRECISION NOT NULL DEFAULT 0,
    pipeline_error      BOOLEAN NOT NULL DEFAULT false,
    error_code          TEXT,
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_serving_request_metrics_created_at
    ON app.serving_request_metrics (created_at);
CREATE INDEX IF NOT EXISTS idx_serving_request_metrics_model_version
    ON app.serving_request_metrics (model_version, created_at);
CREATE INDEX IF NOT EXISTS idx_serving_request_metrics_fallback_state
    ON app.serving_request_metrics (fallback_state, created_at);

CREATE TABLE IF NOT EXISTS app.serving_metric_rollups (
    rollup_id               TEXT PRIMARY KEY,
    window_name             TEXT NOT NULL,
    window_start            TIMESTAMPTZ NOT NULL,
    window_end              TIMESTAMPTZ NOT NULL,
    model_version           TEXT,
    request_count           INT NOT NULL DEFAULT 0,
    error_rate              DOUBLE PRECISION NOT NULL DEFAULT 0,
    fallback_rate           DOUBLE PRECISION NOT NULL DEFAULT 0,
    p50_latency_ms          DOUBLE PRECISION NOT NULL DEFAULT 0,
    p95_latency_ms          DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_returned_count      DOUBLE PRECISION NOT NULL DEFAULT 0,
    catalog_failure_count   INT NOT NULL DEFAULT 0,
    stream_failure_count    INT NOT NULL DEFAULT 0,
    completion_rate         DOUBLE PRECISION NOT NULL DEFAULT 0,
    skip_rate               DOUBLE PRECISION NOT NULL DEFAULT 0,
    dislike_rate            DOUBLE PRECISION NOT NULL DEFAULT 0,
    top_artist_share        DOUBLE PRECISION NOT NULL DEFAULT 0,
    repeat_violation_count  INT NOT NULL DEFAULT 0,
    sample_status           TEXT NOT NULL DEFAULT 'insufficient',
    metrics_json            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_serving_metric_rollups_window
    ON app.serving_metric_rollups (window_name, window_end DESC);
CREATE INDEX IF NOT EXISTS idx_serving_metric_rollups_model_version
    ON app.serving_metric_rollups (model_version, window_end DESC);

CREATE TABLE IF NOT EXISTS app.model_trigger_decisions (
    decision_id      TEXT PRIMARY KEY,
    decision_type    TEXT NOT NULL,
    model_version    TEXT,
    decision         TEXT NOT NULL,
    reason           TEXT,
    metrics_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    thresholds_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ DEFAULT now(),
    executed         BOOLEAN NOT NULL DEFAULT false,
    execution_note   TEXT
);

CREATE INDEX IF NOT EXISTS idx_model_trigger_decisions_type_created
    ON app.model_trigger_decisions (decision_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_trigger_decisions_model_version
    ON app.model_trigger_decisions (model_version, created_at DESC);

CREATE TABLE IF NOT EXISTS app.model_versions (
    model_version           TEXT PRIMARY KEY,
    serving_bundle_version  TEXT,
    manifest_uri            TEXT,
    status                  TEXT NOT NULL DEFAULT 'candidate',
    is_active               BOOLEAN NOT NULL DEFAULT false,
    activated_at            TIMESTAMPTZ,
    deactivated_at          TIMESTAMPTZ,
    rollback_parent_version TEXT,
    created_at              TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_model_versions_single_active
    ON app.model_versions (is_active)
    WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_model_versions_status
    ON app.model_versions (status, created_at DESC);

-- Delta export watermarks
CREATE TABLE IF NOT EXISTS app.delta_checkpoint (
    id                       SERIAL PRIMARY KEY,
    version                  TEXT NOT NULL,         -- e.g. "20260424_120000"
    exported_at              TIMESTAMPTZ DEFAULT now(),
    session_int_id_watermark BIGINT NOT NULL,       -- highest session_int_id exported
    rows_exported            JSONB                  -- {"sessions": N, "events": M, ...}
);

COMMIT;
