from __future__ import annotations

import json
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
        """Apply additive auth/session guards for existing demo volumes."""
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
                cur.execute("UPDATE app.model_versions SET is_active = FALSE WHERE is_active = TRUE")
                cur.execute(
                    """
                    INSERT INTO app.model_versions
                        (model_version, serving_bundle_version, manifest_uri, activated_at, is_active)
                    VALUES (%s, %s, %s, NOW(), TRUE)
                    ON CONFLICT (model_version) DO UPDATE SET
                        serving_bundle_version = EXCLUDED.serving_bundle_version,
                        manifest_uri = EXCLUDED.manifest_uri,
                        activated_at = NOW(),
                        is_active = TRUE
                    """,
                    (model_version, serving_bundle_version, manifest_uri),
                )

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
