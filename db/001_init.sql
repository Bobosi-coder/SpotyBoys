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
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_playback_events_session ON app.playback_events (session_int_id);
CREATE INDEX IF NOT EXISTS idx_playback_events_playratio ON app.playback_events (session_int_id) WHERE playratio IS NOT NULL;

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
    updated_at  TIMESTAMPTZ DEFAULT now()
);

INSERT INTO app.model_status (degraded) VALUES (false)
ON CONFLICT (id) DO NOTHING;

-- Delta export watermarks
CREATE TABLE IF NOT EXISTS app.delta_checkpoint (
    id                       SERIAL PRIMARY KEY,
    version                  TEXT NOT NULL,         -- e.g. "20260424_120000"
    exported_at              TIMESTAMPTZ DEFAULT now(),
    session_int_id_watermark BIGINT NOT NULL,       -- highest session_int_id exported
    rows_exported            JSONB                  -- {"sessions": N, "events": M, ...}
);

COMMIT;
