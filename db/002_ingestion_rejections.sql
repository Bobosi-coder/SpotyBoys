BEGIN;

CREATE TABLE IF NOT EXISTS app.ingestion_rejections (
    id         SERIAL PRIMARY KEY,
    source     TEXT NOT NULL,        -- 'loved_track_sync' | 'playlist_sync'
    reason     TEXT NOT NULL,        -- 'track_not_mapped'
    raw_ref    TEXT,                 -- the navidrome_track_id that failed
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_rejections_created
    ON app.ingestion_rejections (created_at);

COMMIT;
