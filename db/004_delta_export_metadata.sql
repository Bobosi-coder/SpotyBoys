-- =====================================================
-- 004_delta_export_metadata.sql
-- Delta export checkpoint tracking
-- =====================================================

BEGIN;

CREATE TABLE IF NOT EXISTS app.delta_export_metadata (
    metadata_id SERIAL PRIMARY KEY,
    delta_version VARCHAR NOT NULL UNIQUE,
    status VARCHAR NOT NULL CHECK (status IN ('in_progress', 'completed', 'failed')),
    last_exported_session_id TEXT,
    row_counts JSONB NOT NULL DEFAULT '{}',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_delta_export_metadata_status
    ON app.delta_export_metadata(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_delta_export_metadata_version
    ON app.delta_export_metadata(delta_version DESC);

CREATE INDEX IF NOT EXISTS idx_delta_export_metadata_completed
    ON app.delta_export_metadata(status, completed_at DESC)
    WHERE status = 'completed';

COMMIT;
