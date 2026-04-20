-- =====================================================
-- 005_int_id_mapping.sql
-- Integer surrogate IDs for ML pipeline compatibility
--
-- snapshot (30Music) uses BIGINT IDs.
-- Online system uses TEXT UUIDs.
-- These columns bridge the two namespaces for delta export.
--
-- Offsets chosen above snapshot maxima:
--   user_int_id    starts at 100,000  (snapshot max: 45,175)
--   session_int_id starts at 3,000,000 (snapshot max: 2,764,469)
-- =====================================================

BEGIN;

-- ── users ─────────────────────────────────────────────────────────────────

ALTER TABLE app.users ADD COLUMN IF NOT EXISTS user_int_id BIGINT;

CREATE SEQUENCE IF NOT EXISTS app_user_int_seq START 100000;

UPDATE app.users
SET user_int_id = nextval('app_user_int_seq')
WHERE user_int_id IS NULL;

ALTER TABLE app.users
    ALTER COLUMN user_int_id SET DEFAULT nextval('app_user_int_seq');

CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_user_int_id
    ON app.users (user_int_id);

-- ── sessions ──────────────────────────────────────────────────────────────

ALTER TABLE app.sessions ADD COLUMN IF NOT EXISTS session_int_id BIGINT;

CREATE SEQUENCE IF NOT EXISTS app_session_int_seq START 3000000;

UPDATE app.sessions
SET session_int_id = nextval('app_session_int_seq')
WHERE session_int_id IS NULL;

ALTER TABLE app.sessions
    ALTER COLUMN session_int_id SET DEFAULT nextval('app_session_int_seq');

CREATE UNIQUE INDEX IF NOT EXISTS idx_app_sessions_session_int_id
    ON app.sessions (session_int_id);

-- ── delta checkpoint ──────────────────────────────────────────────────────

ALTER TABLE app.delta_export_metadata
    ADD COLUMN IF NOT EXISTS last_exported_session_int_id BIGINT;

COMMIT;
