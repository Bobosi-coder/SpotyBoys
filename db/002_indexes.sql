-- =====================================================
-- 002_indexes.sql
-- Spotiboys Recommender System - Initial Implementation
-- Secondary indexes for preprocessing, retrieval prep,
-- and serving log lookup
-- =====================================================

BEGIN;

-- =====================================================
-- RAW SCHEMA INDEXES
-- =====================================================

-- raw.sessions
-- Common lookups by user
CREATE INDEX IF NOT EXISTS idx_raw_sessions_user_id
    ON raw.sessions(user_id);

-- Optional if session_ts ever becomes useful for diagnostics
CREATE INDEX IF NOT EXISTS idx_raw_sessions_session_ts
    ON raw.sessions(session_ts);


-- raw.session_tracks
-- Most important table in the pipeline:
-- used for session grouping, ordering, label filtering, user history,
-- track lookup, Item2Vec corpus construction, and downstream filtering.

CREATE INDEX IF NOT EXISTS idx_raw_session_tracks_session_id
    ON raw.session_tracks(session_id);

CREATE INDEX IF NOT EXISTS idx_raw_session_tracks_user_id
    ON raw.session_tracks(user_id);

CREATE INDEX IF NOT EXISTS idx_raw_session_tracks_track_id
    ON raw.session_tracks(track_id);

CREATE INDEX IF NOT EXISTS idx_raw_session_tracks_label
    ON raw.session_tracks(label);

-- Useful for sorted session reconstruction
CREATE INDEX IF NOT EXISTS idx_raw_session_tracks_session_position
    ON raw.session_tracks(session_id, position);

-- Useful for queries like:
-- WHERE session_id IN (...) AND label IN (...)
CREATE INDEX IF NOT EXISTS idx_raw_session_tracks_session_label
    ON raw.session_tracks(session_id, label);

-- Useful for user-specific recent/positive filtering
CREATE INDEX IF NOT EXISTS idx_raw_session_tracks_user_label
    ON raw.session_tracks(user_id, label);

-- Useful for joins or analytics by track + label
CREATE INDEX IF NOT EXISTS idx_raw_session_tracks_track_label
    ON raw.session_tracks(track_id, label);


-- raw.events
-- Used for per-track play count aggregation
CREATE INDEX IF NOT EXISTS idx_raw_events_track_id
    ON raw.events(track_id);

CREATE INDEX IF NOT EXISTS idx_raw_events_user_id
    ON raw.events(user_id);

CREATE INDEX IF NOT EXISTS idx_raw_events_event_ts
    ON raw.events(event_ts);


-- raw.track_loves
-- Used for positive signal / centroid computation
CREATE INDEX IF NOT EXISTS idx_raw_track_loves_track_id
    ON raw.track_loves(track_id);

CREATE INDEX IF NOT EXISTS idx_raw_track_loves_user_id
    ON raw.track_loves(user_id);


-- raw.playlists
CREATE INDEX IF NOT EXISTS idx_raw_playlists_user_id
    ON raw.playlists(user_id);


-- raw.playlist_tracks
-- Used for playlist co-occurrence construction
CREATE INDEX IF NOT EXISTS idx_raw_playlist_tracks_playlist_id
    ON raw.playlist_tracks(playlist_id);

CREATE INDEX IF NOT EXISTS idx_raw_playlist_tracks_track_id
    ON raw.playlist_tracks(track_id);

CREATE INDEX IF NOT EXISTS idx_raw_playlist_tracks_user_id
    ON raw.playlist_tracks(user_id);

CREATE INDEX IF NOT EXISTS idx_raw_playlist_tracks_playlist_position
    ON raw.playlist_tracks(playlist_id, position);


-- Bridge tables kept for future extensibility
CREATE INDEX IF NOT EXISTS idx_raw_track_artists_artist_id
    ON raw.track_artists(artist_id);

CREATE INDEX IF NOT EXISTS idx_raw_track_albums_album_id
    ON raw.track_albums(album_id);

CREATE INDEX IF NOT EXISTS idx_raw_track_tags_tag_id
    ON raw.track_tags(tag_id);


-- Optional metadata lookup support
CREATE INDEX IF NOT EXISTS idx_raw_tracks_mbid
    ON raw.tracks(mbid);

CREATE INDEX IF NOT EXISTS idx_raw_artists_mbid
    ON raw.artists(mbid);

CREATE INDEX IF NOT EXISTS idx_raw_albums_mbid
    ON raw.albums(mbid);

CREATE INDEX IF NOT EXISTS idx_raw_tags_value
    ON raw.tags(value);


-- =====================================================
-- PROCESSED SCHEMA INDEXES
-- =====================================================

CREATE INDEX IF NOT EXISTS idx_processed_dataset_artifacts_artifact_name
    ON processed.dataset_artifacts(artifact_name);

CREATE INDEX IF NOT EXISTS idx_processed_dataset_artifacts_is_active
    ON processed.dataset_artifacts(is_active);

CREATE INDEX IF NOT EXISTS idx_processed_data_splits_split_name
    ON processed.data_splits(split_name);


-- =====================================================
-- ML SCHEMA INDEXES
-- =====================================================

CREATE INDEX IF NOT EXISTS idx_ml_track_embeddings_track_id
    ON ml.track_embeddings(track_id);

CREATE INDEX IF NOT EXISTS idx_ml_track_embeddings_run_id
    ON ml.track_embeddings(run_id);

CREATE INDEX IF NOT EXISTS idx_ml_track_embeddings_is_active
    ON ml.track_embeddings(is_active);

CREATE INDEX IF NOT EXISTS idx_ml_track_embeddings_model_name_version
    ON ml.track_embeddings(model_name, model_version);

CREATE INDEX IF NOT EXISTS idx_ml_model_artifacts_run_id
    ON ml.model_artifacts(run_id);

CREATE INDEX IF NOT EXISTS idx_ml_model_artifacts_model_name
    ON ml.model_artifacts(model_name);

CREATE INDEX IF NOT EXISTS idx_ml_model_artifacts_is_active
    ON ml.model_artifacts(is_active);

CREATE INDEX IF NOT EXISTS idx_ml_dataset_versions_dataset_name
    ON ml.dataset_versions(dataset_name);

CREATE INDEX IF NOT EXISTS idx_ml_dataset_versions_is_active
    ON ml.dataset_versions(is_active);


-- =====================================================
-- SERVING SCHEMA INDEXES
-- =====================================================

CREATE INDEX IF NOT EXISTS idx_serving_impression_logs_user_id
    ON serving.impression_logs(user_id);

CREATE INDEX IF NOT EXISTS idx_serving_impression_logs_session_id
    ON serving.impression_logs(session_id);

CREATE INDEX IF NOT EXISTS idx_serving_impression_logs_requested_at
    ON serving.impression_logs(requested_at);

CREATE INDEX IF NOT EXISTS idx_serving_impression_logs_model_version
    ON serving.impression_logs(model_version);

CREATE INDEX IF NOT EXISTS idx_serving_impression_logs_fallback_level
    ON serving.impression_logs(fallback_level);

CREATE INDEX IF NOT EXISTS idx_serving_outcome_logs_request_id
    ON serving.outcome_logs(request_id);

CREATE INDEX IF NOT EXISTS idx_serving_outcome_logs_chosen_track_id
    ON serving.outcome_logs(chosen_track_id);

CREATE INDEX IF NOT EXISTS idx_serving_outcome_logs_created_at
    ON serving.outcome_logs(created_at);

CREATE INDEX IF NOT EXISTS idx_serving_outcome_logs_derived_label
    ON serving.outcome_logs(derived_label);

COMMIT;