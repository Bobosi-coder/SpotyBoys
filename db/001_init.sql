-- =====================================================
-- 001_init.sql
-- Spotiboys Recommender System - Initial Implementation
-- Current design based on 30Music + Item2Vec pipeline
-- =====================================================

-- Optional: stop on first error when run in psql with:
-- psql -v ON_ERROR_STOP=1 -f db/001_init.sql

BEGIN;

-- =====================================================
-- Schemas
-- =====================================================

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS processed;
CREATE SCHEMA IF NOT EXISTS ml;
CREATE SCHEMA IF NOT EXISTS serving;

-- =====================================================
-- RAW SCHEMA
-- =====================================================

CREATE TABLE IF NOT EXISTS raw.tracks (
    id BIGINT PRIMARY KEY,
    mbid VARCHAR,
    name TEXT,
    title TEXT,
    artist_hint TEXT,
    duration_ms INTEGER,
    playcount INTEGER,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw.artists (
    id BIGINT PRIMARY KEY,
    mbid VARCHAR,
    name VARCHAR NOT NULL,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw.albums (
    id BIGINT PRIMARY KEY,
    mbid VARCHAR,
    title VARCHAR NOT NULL,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw.tags (
    id BIGINT PRIMARY KEY,
    value VARCHAR NOT NULL,
    url TEXT
);

CREATE TABLE IF NOT EXISTS raw.users (
    id BIGINT PRIMARY KEY,
    username VARCHAR,
    gender VARCHAR,
    age INTEGER,
    country VARCHAR,
    playcount INTEGER,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw.sessions (
    id BIGINT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    session_ts BIGINT,
    num_tracks INTEGER,
    total_playtime INTEGER,
    created_at TIMESTAMP,
    CONSTRAINT fk_raw_sessions_user
        FOREIGN KEY (user_id) REFERENCES raw.users(id)
);

CREATE TABLE IF NOT EXISTS raw.session_tracks (
    session_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    position INTEGER NOT NULL,
    track_id BIGINT NOT NULL,
    playstart BIGINT,
    playtime INTEGER,
    playratio REAL,
    action VARCHAR,
    label VARCHAR,
    PRIMARY KEY (session_id, position),
    CONSTRAINT fk_raw_session_tracks_session
        FOREIGN KEY (session_id) REFERENCES raw.sessions(id),
    CONSTRAINT fk_raw_session_tracks_user
        FOREIGN KEY (user_id) REFERENCES raw.users(id),
    CONSTRAINT fk_raw_session_tracks_track
        FOREIGN KEY (track_id) REFERENCES raw.tracks(id)
);

CREATE TABLE IF NOT EXISTS raw.events (
    id BIGINT PRIMARY KEY,
    track_id BIGINT NOT NULL,
    user_id BIGINT,
    event_ts TIMESTAMP,
    event_type VARCHAR,
    CONSTRAINT fk_raw_events_track
        FOREIGN KEY (track_id) REFERENCES raw.tracks(id),
    CONSTRAINT fk_raw_events_user
        FOREIGN KEY (user_id) REFERENCES raw.users(id)
);

CREATE TABLE IF NOT EXISTS raw.track_loves (
    user_id BIGINT NOT NULL,
    track_id BIGINT NOT NULL,
    loved_at TIMESTAMP,
    PRIMARY KEY (user_id, track_id),
    CONSTRAINT fk_raw_track_loves_user
        FOREIGN KEY (user_id) REFERENCES raw.users(id),
    CONSTRAINT fk_raw_track_loves_track
        FOREIGN KEY (track_id) REFERENCES raw.tracks(id)
);

CREATE TABLE IF NOT EXISTS raw.playlists (
    id BIGINT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    created_at TIMESTAMP,
    CONSTRAINT fk_raw_playlists_user
        FOREIGN KEY (user_id) REFERENCES raw.users(id)
);

CREATE TABLE IF NOT EXISTS raw.playlist_tracks (
    playlist_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    position INTEGER,
    track_id BIGINT NOT NULL,
    PRIMARY KEY (playlist_id, track_id, position),
    CONSTRAINT fk_raw_playlist_tracks_playlist
        FOREIGN KEY (playlist_id) REFERENCES raw.playlists(id),
    CONSTRAINT fk_raw_playlist_tracks_user
        FOREIGN KEY (user_id) REFERENCES raw.users(id),
    CONSTRAINT fk_raw_playlist_tracks_track
        FOREIGN KEY (track_id) REFERENCES raw.tracks(id)
);

CREATE TABLE IF NOT EXISTS raw.track_artists (
    track_id BIGINT NOT NULL,
    artist_id BIGINT NOT NULL,
    PRIMARY KEY (track_id, artist_id),
    CONSTRAINT fk_raw_track_artists_track
        FOREIGN KEY (track_id) REFERENCES raw.tracks(id),
    CONSTRAINT fk_raw_track_artists_artist
        FOREIGN KEY (artist_id) REFERENCES raw.artists(id)
);

CREATE TABLE IF NOT EXISTS raw.track_albums (
    track_id BIGINT NOT NULL,
    album_id BIGINT NOT NULL,
    PRIMARY KEY (track_id, album_id),
    CONSTRAINT fk_raw_track_albums_track
        FOREIGN KEY (track_id) REFERENCES raw.tracks(id),
    CONSTRAINT fk_raw_track_albums_album
        FOREIGN KEY (album_id) REFERENCES raw.albums(id)
);

CREATE TABLE IF NOT EXISTS raw.track_tags (
    track_id BIGINT NOT NULL,
    tag_id BIGINT NOT NULL,
    PRIMARY KEY (track_id, tag_id),
    CONSTRAINT fk_raw_track_tags_track
        FOREIGN KEY (track_id) REFERENCES raw.tracks(id),
    CONSTRAINT fk_raw_track_tags_tag
        FOREIGN KEY (tag_id) REFERENCES raw.tags(id)
);

-- =====================================================
-- PROCESSED SCHEMA
-- =====================================================

CREATE TABLE IF NOT EXISTS processed.dataset_artifacts (
    id BIGINT PRIMARY KEY,
    artifact_name VARCHAR NOT NULL,
    artifact_version VARCHAR NOT NULL,
    artifact_type VARCHAR NOT NULL,
    storage_bucket VARCHAR NOT NULL,
    object_key TEXT NOT NULL,
    source_description TEXT,
    created_at TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    CONSTRAINT uq_processed_dataset_artifacts
        UNIQUE (artifact_name, artifact_version)
);

CREATE TABLE IF NOT EXISTS processed.data_splits (
    id BIGINT PRIMARY KEY,
    split_version VARCHAR NOT NULL,
    split_name VARCHAR NOT NULL,
    storage_bucket VARCHAR NOT NULL,
    object_key TEXT NOT NULL,
    session_count BIGINT,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_processed_data_splits
        UNIQUE (split_version, split_name)
);

-- =====================================================
-- ML SCHEMA
-- =====================================================

CREATE TABLE IF NOT EXISTS ml.track_embeddings (
    id BIGINT PRIMARY KEY,
    track_id BIGINT NOT NULL,
    model_name VARCHAR NOT NULL,
    model_version VARCHAR NOT NULL,
    hyperparam_tag VARCHAR,
    run_id VARCHAR,
    storage_bucket VARCHAR NOT NULL,
    object_key TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL,
    dtype VARCHAR,
    row_index INTEGER,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP,
    CONSTRAINT uq_ml_track_embeddings
        UNIQUE (track_id, model_name, model_version, hyperparam_tag),
    CONSTRAINT fk_ml_track_embeddings_track
        FOREIGN KEY (track_id) REFERENCES raw.tracks(id)
);

CREATE TABLE IF NOT EXISTS ml.model_artifacts (
    id BIGINT PRIMARY KEY,
    artifact_name VARCHAR NOT NULL,
    artifact_version VARCHAR NOT NULL,
    artifact_type VARCHAR NOT NULL,
    model_name VARCHAR,
    run_id VARCHAR,
    storage_bucket VARCHAR NOT NULL,
    object_key TEXT NOT NULL,
    metadata_json TEXT,
    created_at TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    CONSTRAINT uq_ml_model_artifacts
        UNIQUE (artifact_name, artifact_version)
);

CREATE TABLE IF NOT EXISTS ml.dataset_versions (
    id BIGINT PRIMARY KEY,
    dataset_name VARCHAR NOT NULL,
    dataset_version VARCHAR NOT NULL,
    storage_bucket VARCHAR NOT NULL,
    object_key TEXT NOT NULL,
    source_description TEXT,
    created_at TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    CONSTRAINT uq_ml_dataset_versions
        UNIQUE (dataset_name, dataset_version)
);

-- =====================================================
-- SERVING SCHEMA
-- =====================================================

CREATE TABLE IF NOT EXISTS serving.impression_logs (
    request_id UUID PRIMARY KEY,
    user_id BIGINT,
    session_id BIGINT,
    requested_at TIMESTAMP NOT NULL,
    trigger_type VARCHAR NOT NULL,
    context_track_ids TEXT,
    candidate_pool_ids TEXT,
    candidate_scores TEXT,
    top5_ids TEXT,
    top5_scores TEXT,
    exploration_pos TEXT,
    model_version VARCHAR,
    fallback_level INTEGER,
    latency_ms REAL,
    CONSTRAINT fk_serving_impression_logs_user
        FOREIGN KEY (user_id) REFERENCES raw.users(id),
    CONSTRAINT fk_serving_impression_logs_session
        FOREIGN KEY (session_id) REFERENCES raw.sessions(id)
);

CREATE TABLE IF NOT EXISTS serving.outcome_logs (
    outcome_id UUID PRIMARY KEY,
    request_id UUID NOT NULL,
    chosen_track_id BIGINT,
    chosen_from_rec BOOLEAN,
    chosen_position INTEGER,
    play_duration_sec REAL,
    play_ratio REAL,
    explicit_feedback VARCHAR,
    derived_label VARCHAR,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT fk_serving_outcome_logs_request
        FOREIGN KEY (request_id) REFERENCES serving.impression_logs(request_id),
    CONSTRAINT fk_serving_outcome_logs_track
        FOREIGN KEY (chosen_track_id) REFERENCES raw.tracks(id)
);

COMMIT;