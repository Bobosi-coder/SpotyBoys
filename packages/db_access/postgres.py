from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from packages.auth import AuthenticatedSession
from packages.db_access.repositories import PlayableTrackRecord
from packages.shared_contracts.jsonutil import dumps, to_jsonable


class PostgresRepository:
    """PostgreSQL-backed durable repository for the demo stack."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.ensure_auth_schema()
        self.ensure_monitoring_schema()

    def _connect(self):
        import psycopg2

        return psycopg2.connect(self.database_url)

    def seed_from_fixture(self, fixture_path: str | Path, user_id: str, session_id: str) -> None:
        payload = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.users (user_id, display_name)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET display_name = EXCLUDED.display_name
                    """,
                    (user_id, "Demo Listener"),
                )
                cur.execute(
                    """
                    INSERT INTO app.sessions (session_id, user_id, auth_state)
                    VALUES (%s, %s, 'authenticated')
                    ON CONFLICT (session_id) DO UPDATE
                    SET user_id = EXCLUDED.user_id, last_seen_at = NOW()
                    """,
                    (session_id, user_id),
                )
                for row in payload["tracks"]:
                    cur.execute(
                        """
                        INSERT INTO app.playable_tracks
                            (track_id, title, artist, album, duration_sec, cover_art_url, is_playable, availability_status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (track_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            artist = EXCLUDED.artist,
                            album = EXCLUDED.album,
                            duration_sec = EXCLUDED.duration_sec,
                            cover_art_url = EXCLUDED.cover_art_url,
                            is_playable = EXCLUDED.is_playable,
                            availability_status = EXCLUDED.availability_status,
                            updated_at = NOW()
                        """,
                        (
                            str(row["track_id"]),
                            str(row["title"]),
                            str(row["artist"]),
                            str(row.get("album", "")),
                            int(row.get("duration_sec", 0)),
                            str(row.get("cover_art_url", "")),
                            bool(row.get("is_playable", False)),
                            str(row.get("availability_status", "available")),
                        ),
                    )
                    cur.execute(
                        """
                        INSERT INTO app.navidrome_track_mapping
                            (track_id, navidrome_track_id, mapping_confidence, availability_status, quarantine_reason, last_seen_in_navidrome_at)
                        VALUES (%s, %s, %s, %s, %s, CASE WHEN %s IS NULL THEN NULL ELSE NOW() END)
                        ON CONFLICT (track_id) DO NOTHING
                        """,
                        (
                            str(row["track_id"]),
                            row.get("navidrome_track_id"),
                            float(row.get("mapping_confidence", 1.0)),
                            str(row.get("availability_status", "available")),
                            row.get("quarantine_reason"),
                            row.get("navidrome_track_id"),
                        ),
                    )

    def ensure_auth_schema(self) -> None:
        """Apply additive guards for existing demo volumes (idempotent)."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE app.users ADD COLUMN IF NOT EXISTS email TEXT")
                cur.execute("ALTER TABLE app.users ADD COLUMN IF NOT EXISTS password_hash TEXT")
                cur.execute("ALTER TABLE app.users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_email_unique
                    ON app.users (LOWER(email))
                    WHERE email IS NOT NULL
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.auth_sessions (
                        session_token_hash TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL REFERENCES app.sessions(session_id),
                        user_id TEXT NOT NULL REFERENCES app.users(user_id),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        expires_at TIMESTAMPTZ NOT NULL,
                        revoked_at TIMESTAMPTZ
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_app_auth_sessions_user
                    ON app.auth_sessions(user_id, created_at DESC)
                    """
                )

                # Integer surrogate IDs for ML pipeline delta export (005_int_id_mapping.sql)
                cur.execute("ALTER TABLE app.users ADD COLUMN IF NOT EXISTS user_int_id BIGINT")
                cur.execute("CREATE SEQUENCE IF NOT EXISTS app_user_int_seq START 100000")
                cur.execute(
                    "UPDATE app.users SET user_int_id = nextval('app_user_int_seq') WHERE user_int_id IS NULL"
                )
                cur.execute(
                    "ALTER TABLE app.users ALTER COLUMN user_int_id SET DEFAULT nextval('app_user_int_seq')"
                )
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_user_int_id ON app.users (user_int_id)"
                )

                cur.execute("ALTER TABLE app.sessions ADD COLUMN IF NOT EXISTS session_int_id BIGINT")
                cur.execute("CREATE SEQUENCE IF NOT EXISTS app_session_int_seq START 3000000")
                cur.execute(
                    "UPDATE app.sessions SET session_int_id = nextval('app_session_int_seq') WHERE session_int_id IS NULL"
                )
                cur.execute(
                    "ALTER TABLE app.sessions ALTER COLUMN session_int_id SET DEFAULT nextval('app_session_int_seq')"
                )
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_app_sessions_session_int_id ON app.sessions (session_int_id)"
                )

                cur.execute(
                    """
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM information_schema.tables
                            WHERE table_schema = 'app'
                              AND table_name = 'delta_export_metadata'
                        ) THEN
                            ALTER TABLE app.delta_export_metadata
                                ADD COLUMN IF NOT EXISTS last_exported_session_int_id BIGINT;
                        END IF;
                    END $$
                    """
                )

    def ensure_monitoring_schema(self) -> None:
        """Apply additive monitoring/trigger tables for existing VM volumes."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE app.model_versions ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'inactive'")
                cur.execute("ALTER TABLE app.model_versions ADD COLUMN IF NOT EXISTS rollback_parent_version TEXT")
                cur.execute("ALTER TABLE app.model_versions ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.serving_request_metrics (
                        metric_id TEXT PRIMARY KEY,
                        endpoint TEXT NOT NULL,
                        request_id TEXT,
                        session_id TEXT,
                        user_id TEXT,
                        model_version TEXT,
                        serving_bundle_version TEXT,
                        status TEXT NOT NULL,
                        status_code INTEGER,
                        latency_ms REAL NOT NULL CHECK (latency_ms >= 0),
                        latency_c2_ms REAL,
                        latency_c3_ms REAL,
                        latency_c4_ms REAL,
                        candidate_count INTEGER,
                        final_count INTEGER,
                        playable_drop_count INTEGER,
                        fallback_level TEXT,
                        error_type TEXT,
                        metrics_json JSONB NOT NULL DEFAULT '{}',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.serving_metric_rollups (
                        rollup_id TEXT PRIMARY KEY,
                        window_name TEXT NOT NULL,
                        window_start TIMESTAMPTZ NOT NULL,
                        window_end TIMESTAMPTZ NOT NULL,
                        model_version TEXT NOT NULL,
                        request_count INTEGER NOT NULL DEFAULT 0,
                        recommendation_request_count INTEGER NOT NULL DEFAULT 0,
                        stream_request_count INTEGER NOT NULL DEFAULT 0,
                        error_rate REAL NOT NULL DEFAULT 0,
                        fallback_rate REAL NOT NULL DEFAULT 0,
                        p50_latency_ms REAL,
                        p95_latency_ms REAL,
                        stream_failure_rate REAL NOT NULL DEFAULT 0,
                        event_ingestion_count INTEGER NOT NULL DEFAULT 0,
                        impression_count INTEGER NOT NULL DEFAULT 0,
                        playback_start_count INTEGER NOT NULL DEFAULT 0,
                        skip_rate REAL NOT NULL DEFAULT 0,
                        completion_rate REAL NOT NULL DEFAULT 0,
                        dislike_rate REAL NOT NULL DEFAULT 0,
                        unique_track_count INTEGER NOT NULL DEFAULT 0,
                        unique_artist_count INTEGER NOT NULL DEFAULT 0,
                        top_artist_share REAL NOT NULL DEFAULT 0,
                        repeat_violation_count INTEGER NOT NULL DEFAULT 0,
                        sample_status TEXT NOT NULL DEFAULT 'insufficient_sample',
                        metrics_json JSONB NOT NULL DEFAULT '{}',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.model_trigger_decisions (
                        decision_id TEXT PRIMARY KEY,
                        decision_type TEXT NOT NULL CHECK (decision_type IN ('promotion', 'rollback')),
                        model_version TEXT,
                        candidate_version TEXT,
                        decision TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        metrics_json JSONB NOT NULL DEFAULT '{}',
                        artifact_uri TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_app_serving_request_metrics_created ON app.serving_request_metrics(created_at)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_app_serving_request_metrics_endpoint ON app.serving_request_metrics(endpoint, created_at)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_app_serving_metric_rollups_created ON app.serving_metric_rollups(window_name, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_app_model_trigger_decisions_created ON app.model_trigger_decisions(decision_type, created_at DESC)")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.ingestion_rejections (
                        id            BIGSERIAL PRIMARY KEY,
                        event_type    TEXT        NOT NULL,
                        reasons       TEXT[]      NOT NULL,
                        raw_payload   JSONB,
                        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ingestion_rejections_event_type ON app.ingestion_rejections (event_type)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ingestion_rejections_created_at ON app.ingestion_rejections (created_at DESC)"
                )

    def list_playable_tracks(self) -> List[PlayableTrackRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.track_id, t.title, t.artist, COALESCE(t.album, ''), t.duration_sec,
                           COALESCE(t.cover_art_url, ''), t.is_playable, m.navidrome_track_id,
                           t.availability_status, m.quarantine_reason
                    FROM app.playable_tracks t
                    JOIN app.navidrome_track_mapping m ON m.track_id = t.track_id
                    WHERE t.is_playable = TRUE
                      AND t.availability_status = 'available'
                      AND m.availability_status = 'available'
                      AND m.navidrome_track_id IS NOT NULL
                      AND m.quarantine_reason IS NULL
                    ORDER BY t.track_id
                    """
                )
                return [self._record_from_row(row) for row in cur.fetchall()]

    def get_track(self, track_id: str) -> Optional[PlayableTrackRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.track_id, t.title, t.artist, COALESCE(t.album, ''), t.duration_sec,
                           COALESCE(t.cover_art_url, ''), t.is_playable, m.navidrome_track_id,
                           t.availability_status, m.quarantine_reason
                    FROM app.playable_tracks t
                    LEFT JOIN app.navidrome_track_mapping m ON m.track_id = t.track_id
                    WHERE t.track_id = %s
                    """,
                    (track_id,),
                )
                row = cur.fetchone()
                return self._record_from_row(row) if row else None

    def get_playable_track(self, track_id: str) -> Optional[PlayableTrackRecord]:
        track = self.get_track(track_id)
        if not track:
            return None
        if not track.is_playable or not track.navidrome_track_id:
            return None
        if track.availability_status != "available" or track.quarantine_reason:
            return None
        return track

    def register_active_model_version(self, model_version: str, serving_bundle_version: str, manifest_uri: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT model_version FROM app.model_versions WHERE is_active = TRUE LIMIT 1")
                row = cur.fetchone()
                previous_model_version = str(row[0]) if row and row[0] != model_version else None
                if previous_model_version:
                    cur.execute(
                        """
                        UPDATE app.model_versions
                        SET is_active = FALSE,
                            status = 'previous_good',
                            deactivated_at = NOW()
                        WHERE is_active = TRUE
                        """,
                    )
                else:
                    cur.execute(
                        """
                        UPDATE app.model_versions
                        SET is_active = FALSE,
                            status = CASE WHEN status = 'active' THEN 'inactive' ELSE status END,
                            deactivated_at = COALESCE(deactivated_at, NOW())
                        WHERE is_active = TRUE
                          AND model_version <> %s
                        """,
                        (model_version,),
                    )
                cur.execute(
                    """
                    INSERT INTO app.model_versions
                        (model_version, serving_bundle_version, manifest_uri, activated_at, is_active, status, rollback_parent_version)
                    VALUES (%s, %s, %s, NOW(), TRUE, 'active', %s)
                    ON CONFLICT (model_version) DO UPDATE SET
                        serving_bundle_version = EXCLUDED.serving_bundle_version,
                        manifest_uri = EXCLUDED.manifest_uri,
                        activated_at = NOW(),
                        is_active = TRUE,
                        status = 'active',
                        rollback_parent_version = COALESCE(app.model_versions.rollback_parent_version, EXCLUDED.rollback_parent_version),
                        deactivated_at = NULL
                    """,
                    (model_version, serving_bundle_version, manifest_uri, previous_model_version),
                )

    def get_active_model_version(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT model_version, serving_bundle_version, manifest_uri, activated_at,
                           is_active, status, rollback_parent_version, deactivated_at
                    FROM app.model_versions
                    WHERE is_active = TRUE
                    ORDER BY activated_at DESC NULLS LAST
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
        return self._model_version_row(row) if row else None

    def get_previous_good_model_version(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT model_version, serving_bundle_version, manifest_uri, activated_at,
                           is_active, status, rollback_parent_version, deactivated_at
                    FROM app.model_versions
                    WHERE status = 'previous_good'
                    ORDER BY deactivated_at DESC NULLS LAST, activated_at DESC NULLS LAST
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
        return self._model_version_row(row) if row else None

    def get_mapped_track_ids(self) -> set:
        """Return the set of track_ids that already have a navidrome mapping."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT track_id FROM app.navidrome_track_mapping WHERE navidrome_track_id IS NOT NULL"
                )
                return {row[0] for row in cur.fetchall()}

    def upsert_playable_mapping(
        self,
        track_id: str,
        navidrome_track_id: str,
        *,
        mapping_confidence: float = 1.0,
        availability_status: str = "available",
        quarantine_reason: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM app.navidrome_track_mapping
                    WHERE navidrome_track_id = %s
                      AND track_id <> %s
                    """,
                    (navidrome_track_id, track_id),
                )
                cur.execute(
                    """
                    UPDATE app.playable_tracks
                    SET is_playable = %s,
                        availability_status = %s,
                        updated_at = NOW()
                    WHERE track_id = %s
                    """,
                    (availability_status == "available" and quarantine_reason is None, availability_status, track_id),
                )
                cur.execute(
                    """
                    INSERT INTO app.navidrome_track_mapping
                        (track_id, navidrome_track_id, mapping_confidence, availability_status, quarantine_reason, last_seen_in_navidrome_at)
                    VALUES (%s, %s, %s, %s, %s, CASE WHEN %s = 'available' THEN NOW() ELSE NULL END)
                    ON CONFLICT (track_id) DO UPDATE SET
                        navidrome_track_id = EXCLUDED.navidrome_track_id,
                        mapping_confidence = EXCLUDED.mapping_confidence,
                        availability_status = EXCLUDED.availability_status,
                        quarantine_reason = EXCLUDED.quarantine_reason,
                        last_seen_in_navidrome_at = EXCLUDED.last_seen_in_navidrome_at,
                        updated_at = NOW()
                    """,
                    (
                        track_id,
                        navidrome_track_id,
                        mapping_confidence,
                        availability_status,
                        quarantine_reason,
                        availability_status,
                    ),
                )

    def upsert_playable_track(self, track: PlayableTrackRecord) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.playable_tracks
                        (track_id, title, artist, album, duration_sec, cover_art_url, is_playable, availability_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (track_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        artist = EXCLUDED.artist,
                        album = EXCLUDED.album,
                        duration_sec = EXCLUDED.duration_sec,
                        cover_art_url = EXCLUDED.cover_art_url,
                        is_playable = EXCLUDED.is_playable,
                        availability_status = EXCLUDED.availability_status,
                        updated_at = NOW()
                    """,
                    (
                        track.track_id,
                        track.title,
                        track.artist,
                        track.album,
                        track.duration_sec,
                        track.cover_art_url,
                        track.is_playable,
                        track.availability_status,
                    ),
                )

    def persist_recommendation_impression(self, impression_id: str, payload: Dict[str, Any]) -> bool:
        data = to_jsonable(payload)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.recommendation_impressions
                        (impression_id, request_id, session_id, user_id, model_version, fallback_level,
                         browse_surface_json, queue_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    ON CONFLICT (impression_id) DO NOTHING
                    """,
                    (
                        impression_id,
                        data["request_id"],
                        data["session_id"],
                        data["user_id"],
                        data["model_version"],
                        data["fallback_level"],
                        dumps(data["browse_surface"]),
                        dumps(data["queue"]),
                    ),
                )
                return cur.rowcount == 1

    def persist_rendered_impression(self, impression_id: str, payload: Dict[str, Any]) -> bool:
        data = to_jsonable(payload)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.rendered_impressions
                        (impression_id, request_id, session_id, user_id, surface, visible_items_json, rendered_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (impression_id) DO NOTHING
                    """,
                    (
                        impression_id,
                        data["request_id"],
                        data["session_id"],
                        data["user_id"],
                        data["surface"],
                        dumps(data["visible_items"]),
                        data["rendered_at"],
                    ),
                )
                return cur.rowcount == 1

    def persist_playback_event(self, event_id: str, payload: Dict[str, Any]) -> bool:
        data = to_jsonable(payload)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.playback_events
                        (event_id, event_type, session_id, user_id, track_id, request_id, impression_id,
                         position_ms, playback_ms, occurred_at, client_event_seq)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (
                        event_id,
                        data["event_type"],
                        data["session_id"],
                        data["user_id"],
                        data["track_id"],
                        data["request_id"],
                        data["impression_id"],
                        data["position_ms"],
                        data["playback_ms"],
                        data["occurred_at"],
                        data["client_event_seq"],
                    ),
                )
                return cur.rowcount == 1

    def persist_feedback_event(self, event_id: str, payload: Dict[str, Any]) -> bool:
        data = to_jsonable(payload)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.feedback_events
                        (event_id, feedback_type, session_id, user_id, track_id, request_id, impression_id, occurred_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (
                        event_id,
                        data["feedback_type"],
                        data["session_id"],
                        data["user_id"],
                        data["track_id"],
                        data["request_id"],
                        data["impression_id"],
                        data["occurred_at"],
                    ),
                )
                return cur.rowcount == 1

    def persist_ingestion_rejection(
        self, event_type: str, reasons: List[str], raw_payload: Dict[str, Any]
    ) -> None:
        safe_payload = to_jsonable(raw_payload)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.ingestion_rejections (event_type, reasons, raw_payload)
                    VALUES (%s, %s, %s::jsonb)
                    """,
                    (event_type, reasons, dumps(safe_payload)),
                )

    def create_user(self, user_id: str, email: str, password_hash: str, display_name: str) -> Dict[str, Any]:
        normalized = email.strip().lower()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.users (user_id, email, password_hash, display_name)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    (user_id, normalized, password_hash, display_name or normalized.split("@")[0]),
                )
                if cur.rowcount != 1:
                    raise ValueError("user_id already exists")
                return {
                    "user_id": user_id,
                    "email": normalized,
                    "password_hash": password_hash,
                    "display_name": display_name or normalized.split("@")[0],
                }

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        normalized = email.strip().lower()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, COALESCE(email, ''), COALESCE(password_hash, ''), COALESCE(display_name, '')
                    FROM app.users
                    WHERE LOWER(email) = LOWER(%s)
                    """,
                    (normalized,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {"user_id": row[0], "email": row[1], "password_hash": row[2], "display_name": row[3]}

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, COALESCE(email, ''), COALESCE(password_hash, ''), COALESCE(display_name, '')
                    FROM app.users
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {"user_id": row[0], "email": row[1], "password_hash": row[2], "display_name": row[3]}

    def create_auth_session(
        self,
        *,
        token_hash: str,
        user_id: str,
        session_id: str,
        expires_at,
    ) -> AuthenticatedSession:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError("unknown user")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.sessions (session_id, user_id, auth_state)
                    VALUES (%s, %s, 'authenticated')
                    ON CONFLICT (session_id) DO UPDATE
                    SET user_id = EXCLUDED.user_id,
                        auth_state = 'authenticated',
                        last_seen_at = NOW()
                    """,
                    (session_id, user_id),
                )
                cur.execute(
                    """
                    INSERT INTO app.auth_sessions
                        (session_token_hash, session_id, user_id, expires_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (session_token_hash) DO UPDATE SET
                        revoked_at = NULL,
                        expires_at = EXCLUDED.expires_at
                    """,
                    (token_hash, session_id, user_id, expires_at),
                )
        return AuthenticatedSession(
            user_id=user_id,
            session_id=session_id,
            email=user["email"],
            display_name=user.get("display_name", ""),
        )

    def get_auth_session(self, token_hash: str) -> Optional[AuthenticatedSession]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.user_id, s.session_id, COALESCE(u.email, ''), COALESCE(u.display_name, '')
                    FROM app.auth_sessions s
                    JOIN app.users u ON u.user_id = s.user_id
                    WHERE s.session_token_hash = %s
                      AND s.revoked_at IS NULL
                      AND s.expires_at > NOW()
                    """,
                    (token_hash,),
                )
                row = cur.fetchone()
                if row:
                    cur.execute("UPDATE app.sessions SET last_seen_at = NOW() WHERE session_id = %s", (row[1],))
        if not row:
            return None
        return AuthenticatedSession(user_id=row[0], session_id=row[1], email=row[2], display_name=row[3])

    def revoke_auth_session(self, token_hash: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.auth_sessions
                    SET revoked_at = NOW()
                    WHERE session_token_hash = %s AND revoked_at IS NULL
                    """,
                    (token_hash,),
                )
                return cur.rowcount == 1

    def list_disliked_track_ids(self, user_id: str) -> List[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT track_id
                    FROM app.feedback_events
                    WHERE user_id = %s AND feedback_type = 'dislike'
                    """,
                    (user_id,),
                )
                return [str(row[0]) for row in cur.fetchall()]

    def count_table(self, table_name: str) -> int:
        allowed = {
            "recommendation_impressions",
            "rendered_impressions",
            "playback_events",
            "feedback_events",
        }
        if table_name not in allowed:
            raise ValueError(f"Unsupported count table: {table_name}")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM app.{table_name}")
                return int(cur.fetchone()[0])

    def count_new_sessions_since_checkpoint(self) -> int:
        """Count sessions accumulated since the last completed export checkpoint."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT last_exported_session_int_id
                    FROM app.delta_export_metadata
                    WHERE status = 'completed'
                    ORDER BY completed_at DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
                last_int_id = row[0] if row else None

                if last_int_id is not None:
                    cur.execute(
                        "SELECT COUNT(*) FROM app.sessions WHERE session_int_id > %s",
                        (last_int_id,),
                    )
                else:
                    cur.execute("SELECT COUNT(*) FROM app.sessions WHERE session_int_id IS NOT NULL")

                return int(cur.fetchone()[0])

    def get_checkpoint_session_int_id(self) -> Optional[int]:
        """Return the session_int_id high-water mark from the last completed export."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT last_exported_session_int_id
                    FROM app.delta_export_metadata
                    WHERE status = 'completed'
                    ORDER BY completed_at DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def get_max_session_int_id(self) -> Optional[int]:
        """Return the current maximum session_int_id across all sessions."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(session_int_id) FROM app.sessions")
                row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def record_delta_export_start(self, delta_version: str) -> None:
        """Record delta export start (status='in_progress')."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.delta_export_metadata
                        (delta_version, status, row_counts)
                    VALUES (%s, 'in_progress', '{}')
                    ON CONFLICT (delta_version) DO UPDATE SET
                        status = 'in_progress',
                        created_at = NOW()
                    """,
                    (delta_version,)
                )

    def record_delta_export_success(
        self,
        delta_version: str,
        last_exported_session_int_id: Optional[int],
        row_counts: Dict[str, int],
    ) -> None:
        """Record delta export success (status='completed')."""
        import json
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.delta_export_metadata
                    SET status = 'completed',
                        last_exported_session_int_id = %s,
                        row_counts = %s,
                        completed_at = NOW()
                    WHERE delta_version = %s
                    """,
                    (last_exported_session_int_id, json.dumps(row_counts), delta_version),
                )

    def record_delta_export_failure(self, delta_version: str, error: str) -> None:
        """Record delta export failure (status='failed')."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.delta_export_metadata
                    SET status = 'failed',
                        error_message = %s,
                        completed_at = NOW()
                    WHERE delta_version = %s
                    """,
                    (error, delta_version)
                )

    def record_serving_request_metric(self, payload: Dict[str, Any]) -> None:
        data = to_jsonable(payload)
        metric_id = str(data.get("metric_id") or f"metric_{uuid.uuid4().hex}")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.serving_request_metrics
                        (metric_id, endpoint, request_id, session_id, user_id, model_version,
                         serving_bundle_version, status, status_code, latency_ms, latency_c2_ms,
                         latency_c3_ms, latency_c4_ms, candidate_count, final_count,
                         playable_drop_count, fallback_level, error_type, metrics_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (metric_id) DO NOTHING
                    """,
                    (
                        metric_id,
                        data["endpoint"],
                        data.get("request_id"),
                        data.get("session_id"),
                        data.get("user_id"),
                        data.get("model_version"),
                        data.get("serving_bundle_version"),
                        data.get("status", "success"),
                        data.get("status_code"),
                        float(data.get("latency_ms") or 0),
                        data.get("latency_c2_ms"),
                        data.get("latency_c3_ms"),
                        data.get("latency_c4_ms"),
                        data.get("candidate_count"),
                        data.get("final_count"),
                        data.get("playable_drop_count"),
                        data.get("fallback_level"),
                        data.get("error_type"),
                        dumps(data.get("metrics", data.get("metrics_json", {}))),
                        data.get("created_at") or datetime.now(timezone.utc).isoformat(),
                    ),
                )

    def get_monitoring_inputs(self, window_start: datetime, window_end: datetime) -> Dict[str, List[Dict[str, Any]]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT metric_id, endpoint, request_id, session_id, user_id, model_version,
                           serving_bundle_version, status, status_code, latency_ms, candidate_count,
                           final_count, playable_drop_count, fallback_level, error_type, metrics_json, created_at
                    FROM app.serving_request_metrics
                    WHERE created_at >= %s AND created_at < %s
                    """,
                    (window_start, window_end),
                )
                request_metrics = [
                    {
                        "metric_id": row[0],
                        "endpoint": row[1],
                        "request_id": row[2],
                        "session_id": row[3],
                        "user_id": row[4],
                        "model_version": row[5],
                        "serving_bundle_version": row[6],
                        "status": row[7],
                        "status_code": row[8],
                        "latency_ms": row[9],
                        "candidate_count": row[10],
                        "final_count": row[11],
                        "playable_drop_count": row[12],
                        "fallback_level": row[13],
                        "error_type": row[14],
                        "metrics": row[15] or {},
                        "created_at": row[16],
                    }
                    for row in cur.fetchall()
                ]
                cur.execute(
                    """
                    SELECT impression_id, request_id, session_id, user_id, model_version,
                           fallback_level, browse_surface_json, queue_json, created_at
                    FROM app.recommendation_impressions
                    WHERE created_at >= %s AND created_at < %s
                    """,
                    (window_start, window_end),
                )
                recommendation_impressions = [
                    {
                        "impression_id": row[0],
                        "request_id": row[1],
                        "session_id": row[2],
                        "user_id": row[3],
                        "model_version": row[4],
                        "fallback_level": row[5],
                        "browse_surface": row[6] or {},
                        "queue": row[7] or {},
                        "created_at": row[8],
                    }
                    for row in cur.fetchall()
                ]
                cur.execute(
                    """
                    SELECT impression_id, request_id, session_id, user_id, surface,
                           visible_items_json, rendered_at, received_at
                    FROM app.rendered_impressions
                    WHERE received_at >= %s AND received_at < %s
                    """,
                    (window_start, window_end),
                )
                rendered_impressions = [
                    {
                        "impression_id": row[0],
                        "request_id": row[1],
                        "session_id": row[2],
                        "user_id": row[3],
                        "surface": row[4],
                        "visible_items": row[5] or [],
                        "rendered_at": row[6],
                        "received_at": row[7],
                    }
                    for row in cur.fetchall()
                ]
                cur.execute(
                    """
                    SELECT event_id, event_type, session_id, user_id, track_id, request_id,
                           impression_id, position_ms, playback_ms, occurred_at, client_event_seq, received_at
                    FROM app.playback_events
                    WHERE received_at >= %s AND received_at < %s
                    """,
                    (window_start, window_end),
                )
                playback_events = [
                    {
                        "event_id": row[0],
                        "event_type": row[1],
                        "session_id": row[2],
                        "user_id": row[3],
                        "track_id": row[4],
                        "request_id": row[5],
                        "impression_id": row[6],
                        "position_ms": row[7],
                        "playback_ms": row[8],
                        "occurred_at": row[9],
                        "client_event_seq": row[10],
                        "received_at": row[11],
                    }
                    for row in cur.fetchall()
                ]
                cur.execute(
                    """
                    SELECT event_id, feedback_type, session_id, user_id, track_id, request_id,
                           impression_id, occurred_at, received_at
                    FROM app.feedback_events
                    WHERE received_at >= %s AND received_at < %s
                    """,
                    (window_start, window_end),
                )
                feedback_events = [
                    {
                        "event_id": row[0],
                        "feedback_type": row[1],
                        "session_id": row[2],
                        "user_id": row[3],
                        "track_id": row[4],
                        "request_id": row[5],
                        "impression_id": row[6],
                        "occurred_at": row[7],
                        "received_at": row[8],
                    }
                    for row in cur.fetchall()
                ]
        return {
            "request_metrics": request_metrics,
            "recommendation_impressions": recommendation_impressions,
            "rendered_impressions": rendered_impressions,
            "playback_events": playback_events,
            "feedback_events": feedback_events,
        }

    def write_serving_metric_rollup(self, payload: Dict[str, Any]) -> None:
        data = to_jsonable(payload)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.serving_metric_rollups
                        (rollup_id, window_name, window_start, window_end, model_version,
                         request_count, recommendation_request_count, stream_request_count,
                         error_rate, fallback_rate, p50_latency_ms, p95_latency_ms,
                         stream_failure_rate, event_ingestion_count, impression_count,
                         playback_start_count, skip_rate, completion_rate, dislike_rate,
                         unique_track_count, unique_artist_count, top_artist_share,
                         repeat_violation_count, sample_status, metrics_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (rollup_id) DO UPDATE SET
                        metrics_json = EXCLUDED.metrics_json,
                        created_at = NOW()
                    """,
                    (
                        data["rollup_id"],
                        data["window_name"],
                        data["window_start"],
                        data["window_end"],
                        data["model_version"],
                        data["request_count"],
                        data["recommendation_request_count"],
                        data["stream_request_count"],
                        data["error_rate"],
                        data["fallback_rate"],
                        data.get("p50_latency_ms"),
                        data.get("p95_latency_ms"),
                        data["stream_failure_rate"],
                        data["event_ingestion_count"],
                        data["impression_count"],
                        data["playback_start_count"],
                        data["skip_rate"],
                        data["completion_rate"],
                        data["dislike_rate"],
                        data["unique_track_count"],
                        data["unique_artist_count"],
                        data["top_artist_share"],
                        data["repeat_violation_count"],
                        data["sample_status"],
                        dumps(data.get("metrics", data.get("metrics_json", {}))),
                    ),
                )

    def latest_serving_metric_rollup(self, window_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                where = "WHERE window_name = %s" if window_name else ""
                params = (window_name,) if window_name else ()
                cur.execute(
                    f"""
                    SELECT rollup_id, window_name, window_start, window_end, model_version,
                           request_count, recommendation_request_count, stream_request_count,
                           error_rate, fallback_rate, p50_latency_ms, p95_latency_ms,
                           stream_failure_rate, event_ingestion_count, impression_count,
                           playback_start_count, skip_rate, completion_rate, dislike_rate,
                           unique_track_count, unique_artist_count, top_artist_share,
                           repeat_violation_count, sample_status, metrics_json, created_at
                    FROM app.serving_metric_rollups
                    {where}
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    params,
                )
                row = cur.fetchone()
        return self._rollup_row(row) if row else None

    def record_model_trigger_decision(self, payload: Dict[str, Any]) -> None:
        data = to_jsonable(payload)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.model_trigger_decisions
                        (decision_id, decision_type, model_version, candidate_version,
                         decision, reason, metrics_json, artifact_uri, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (decision_id) DO UPDATE SET
                        decision = EXCLUDED.decision,
                        reason = EXCLUDED.reason,
                        metrics_json = EXCLUDED.metrics_json,
                        artifact_uri = EXCLUDED.artifact_uri
                    """,
                    (
                        data["decision_id"],
                        data["decision_type"],
                        data.get("model_version"),
                        data.get("candidate_version"),
                        data["decision"],
                        data["reason"],
                        dumps(data.get("metrics", data.get("metrics_json", {}))),
                        data.get("artifact_uri"),
                        data.get("created_at") or datetime.now(timezone.utc).isoformat(),
                    ),
                )

    def latest_model_trigger_decision(self, decision_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                where = "WHERE decision_type = %s" if decision_type else ""
                params = (decision_type,) if decision_type else ()
                cur.execute(
                    f"""
                    SELECT decision_id, decision_type, model_version, candidate_version,
                           decision, reason, metrics_json, artifact_uri, created_at
                    FROM app.model_trigger_decisions
                    {where}
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    params,
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            "decision_id": row[0],
            "decision_type": row[1],
            "model_version": row[2],
            "candidate_version": row[3],
            "decision": row[4],
            "reason": row[5],
            "metrics": row[6] or {},
            "artifact_uri": row[7],
            "created_at": row[8].isoformat() if hasattr(row[8], "isoformat") else row[8],
        }

    @staticmethod
    def _record_from_row(row: Iterable[Any]) -> PlayableTrackRecord:
        (
            track_id,
            title,
            artist,
            album,
            duration_sec,
            cover_art_url,
            is_playable,
            navidrome_track_id,
            availability_status,
            quarantine_reason,
        ) = row
        return PlayableTrackRecord(
            track_id=str(track_id),
            title=str(title),
            artist=str(artist),
            album=str(album or ""),
            duration_sec=int(duration_sec),
            cover_art_url=str(cover_art_url or ""),
            is_playable=bool(is_playable),
            navidrome_track_id=navidrome_track_id,
            availability_status=str(availability_status or "unknown"),
            quarantine_reason=quarantine_reason,
        )

    @staticmethod
    def _model_version_row(row: Iterable[Any]) -> Dict[str, Any]:
        (
            model_version,
            serving_bundle_version,
            manifest_uri,
            activated_at,
            is_active,
            status,
            rollback_parent_version,
            deactivated_at,
        ) = row
        return {
            "model_version": model_version,
            "serving_bundle_version": serving_bundle_version,
            "manifest_uri": manifest_uri,
            "activated_at": activated_at.isoformat() if hasattr(activated_at, "isoformat") else activated_at,
            "is_active": bool(is_active),
            "status": status,
            "rollback_parent_version": rollback_parent_version,
            "deactivated_at": deactivated_at.isoformat() if hasattr(deactivated_at, "isoformat") else deactivated_at,
        }

    @staticmethod
    def _rollup_row(row: Iterable[Any]) -> Dict[str, Any]:
        (
            rollup_id,
            window_name,
            window_start,
            window_end,
            model_version,
            request_count,
            recommendation_request_count,
            stream_request_count,
            error_rate,
            fallback_rate,
            p50_latency_ms,
            p95_latency_ms,
            stream_failure_rate,
            event_ingestion_count,
            impression_count,
            playback_start_count,
            skip_rate,
            completion_rate,
            dislike_rate,
            unique_track_count,
            unique_artist_count,
            top_artist_share,
            repeat_violation_count,
            sample_status,
            metrics_json,
            created_at,
        ) = row
        return {
            "rollup_id": rollup_id,
            "window_name": window_name,
            "window_start": window_start.isoformat() if hasattr(window_start, "isoformat") else window_start,
            "window_end": window_end.isoformat() if hasattr(window_end, "isoformat") else window_end,
            "model_version": model_version,
            "request_count": request_count,
            "recommendation_request_count": recommendation_request_count,
            "stream_request_count": stream_request_count,
            "error_rate": float(error_rate),
            "fallback_rate": float(fallback_rate),
            "p50_latency_ms": float(p50_latency_ms) if p50_latency_ms is not None else None,
            "p95_latency_ms": float(p95_latency_ms) if p95_latency_ms is not None else None,
            "stream_failure_rate": float(stream_failure_rate),
            "event_ingestion_count": event_ingestion_count,
            "impression_count": impression_count,
            "playback_start_count": playback_start_count,
            "skip_rate": float(skip_rate),
            "completion_rate": float(completion_rate),
            "dislike_rate": float(dislike_rate),
            "unique_track_count": unique_track_count,
            "unique_artist_count": unique_artist_count,
            "top_artist_share": float(top_artist_share),
            "repeat_violation_count": repeat_violation_count,
            "sample_status": sample_status,
            "metrics": metrics_json or {},
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        }
