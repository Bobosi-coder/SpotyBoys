-- =====================================================
-- 003_option_b_online_contracts.sql
-- SpotiBoys Option B online serving/event contracts
-- =====================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS app.users (
    user_id TEXT PRIMARY KEY,
    email TEXT UNIQUE,
    password_hash TEXT,
    display_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES app.users(user_id),
    auth_state TEXT NOT NULL DEFAULT 'authenticated',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.auth_sessions (
    session_token_hash TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES app.sessions(session_id),
    user_id TEXT NOT NULL REFERENCES app.users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS app.playable_tracks (
    track_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT,
    duration_sec INTEGER NOT NULL CHECK (duration_sec >= 0),
    cover_art_url TEXT,
    is_playable BOOLEAN NOT NULL DEFAULT FALSE,
    availability_status TEXT NOT NULL DEFAULT 'unknown',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.navidrome_track_mapping (
    track_id TEXT PRIMARY KEY REFERENCES app.playable_tracks(track_id),
    navidrome_track_id TEXT UNIQUE,
    library_id TEXT,
    path_hash TEXT,
    mapping_confidence REAL,
    availability_status TEXT NOT NULL DEFAULT 'unknown',
    quarantine_reason TEXT,
    last_seen_in_navidrome_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.recommendation_impressions (
    impression_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES app.sessions(session_id),
    user_id TEXT NOT NULL REFERENCES app.users(user_id),
    model_version TEXT NOT NULL,
    fallback_level TEXT NOT NULL,
    browse_surface_json JSONB NOT NULL,
    queue_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.rendered_impressions (
    impression_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES app.sessions(session_id),
    user_id TEXT NOT NULL REFERENCES app.users(user_id),
    surface TEXT NOT NULL,
    visible_items_json JSONB NOT NULL,
    rendered_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.playback_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES app.sessions(session_id),
    user_id TEXT NOT NULL REFERENCES app.users(user_id),
    track_id TEXT NOT NULL REFERENCES app.playable_tracks(track_id),
    request_id TEXT NOT NULL,
    impression_id TEXT NOT NULL,
    position_ms INTEGER NOT NULL CHECK (position_ms >= 0),
    playback_ms INTEGER NOT NULL CHECK (playback_ms >= 0),
    occurred_at TIMESTAMPTZ NOT NULL,
    client_event_seq INTEGER NOT NULL CHECK (client_event_seq >= 1),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.feedback_events (
    event_id TEXT PRIMARY KEY,
    feedback_type TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES app.sessions(session_id),
    user_id TEXT NOT NULL REFERENCES app.users(user_id),
    track_id TEXT NOT NULL REFERENCES app.playable_tracks(track_id),
    request_id TEXT NOT NULL,
    impression_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.recommendation_outcomes (
    outcome_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    impression_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES app.sessions(session_id),
    user_id TEXT NOT NULL REFERENCES app.users(user_id),
    track_id TEXT REFERENCES app.playable_tracks(track_id),
    derived_label TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.model_versions (
    model_version TEXT PRIMARY KEY,
    serving_bundle_version TEXT NOT NULL,
    manifest_uri TEXT,
    activated_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.retraining_runs (
    run_id TEXT PRIMARY KEY,
    source_snapshot_version TEXT NOT NULL,
    max_delta_version TEXT,
    mlflow_run_id TEXT,
    status TEXT NOT NULL,
    metrics_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_app_playable_tracks_is_playable
    ON app.playable_tracks(is_playable);
CREATE INDEX IF NOT EXISTS idx_app_recommendation_impressions_session
    ON app.recommendation_impressions(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_app_playback_events_session
    ON app.playback_events(session_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_app_feedback_events_session
    ON app.feedback_events(session_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_app_auth_sessions_user
    ON app.auth_sessions(user_id, created_at DESC);

COMMIT;
