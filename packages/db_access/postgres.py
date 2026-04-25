from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from packages.auth import AuthenticatedSession
from packages.db_access.repositories import PlayableTrackRecord


class PostgresRepository:
    """PostgreSQL-backed repository for the service stack (new app schema)."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg2
        return psycopg2.connect(self.database_url)

    # ------------------------------------------------------------------ #
    # Track catalog
    # ------------------------------------------------------------------ #

    def list_playable_tracks(self) -> List[PlayableTrackRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT track_id, title, artist, COALESCE(album, ''), duration_sec,
                           COALESCE(cover_art_url, ''), is_playable, navidrome_track_id,
                           availability_status, quarantine_reason
                    FROM app.playable_tracks
                    WHERE is_playable = TRUE
                      AND availability_status = 'available'
                      AND navidrome_track_id IS NOT NULL
                      AND quarantine_reason IS NULL
                    ORDER BY track_id
                    """
                )
                return [self._track_from_row(row) for row in cur.fetchall()]

    def get_track(self, track_id: str) -> Optional[PlayableTrackRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT track_id, title, artist, COALESCE(album, ''), duration_sec,
                           COALESCE(cover_art_url, ''), is_playable, navidrome_track_id,
                           availability_status, quarantine_reason
                    FROM app.playable_tracks
                    WHERE track_id = %s
                    """,
                    (str(track_id),),
                )
                row = cur.fetchone()
                return self._track_from_row(row) if row else None

    def get_playable_track(self, track_id: str) -> Optional[PlayableTrackRecord]:
        track = self.get_track(track_id)
        if not track:
            return None
        if not track.is_playable or not track.navidrome_track_id:
            return None
        if track.availability_status != "available" or track.quarantine_reason:
            return None
        return track

    def get_mapped_track_ids(self) -> set:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT track_id FROM app.playable_tracks WHERE navidrome_track_id IS NOT NULL"
                )
                return {row[0] for row in cur.fetchall()}

    def upsert_playable_track(self, track: PlayableTrackRecord) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.playable_tracks
                        (track_id, title, artist, album, duration_sec, cover_art_url,
                         is_playable, navidrome_track_id, availability_status, quarantine_reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (track_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        artist = EXCLUDED.artist,
                        album = EXCLUDED.album,
                        duration_sec = EXCLUDED.duration_sec,
                        cover_art_url = EXCLUDED.cover_art_url,
                        is_playable = EXCLUDED.is_playable,
                        navidrome_track_id = COALESCE(EXCLUDED.navidrome_track_id, app.playable_tracks.navidrome_track_id),
                        availability_status = EXCLUDED.availability_status,
                        quarantine_reason = EXCLUDED.quarantine_reason
                    """,
                    (
                        str(track.track_id),
                        track.title,
                        track.artist,
                        track.album or "",
                        track.duration_sec or 30,
                        track.cover_art_url or f"/covers/{track.track_id}",
                        track.is_playable,
                        track.navidrome_track_id,
                        track.availability_status or "available",
                        track.quarantine_reason,
                    ),
                )

    def upsert_playable_mapping(
        self,
        track_id: str,
        navidrome_track_id: str,
        *,
        availability_status: str = "available",
        quarantine_reason: Optional[str] = None,
        **_kwargs: Any,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.playable_tracks
                    SET navidrome_track_id = %s,
                        is_playable = (%s = 'available' AND %s IS NULL),
                        availability_status = %s,
                        quarantine_reason = %s
                    WHERE track_id = %s
                      AND NOT EXISTS (
                        SELECT 1 FROM app.playable_tracks p2
                        WHERE p2.navidrome_track_id = %s
                          AND p2.track_id != %s
                      )
                    """,
                    (
                        navidrome_track_id,
                        availability_status,
                        quarantine_reason,
                        availability_status,
                        quarantine_reason,
                        str(track_id),
                        navidrome_track_id,
                        str(track_id),
                    ),
                )

    @staticmethod
    def _track_from_row(row) -> PlayableTrackRecord:
        return PlayableTrackRecord(
            track_id=row[0],
            title=row[1],
            artist=row[2],
            album=row[3],
            duration_sec=row[4],
            cover_art_url=row[5],
            is_playable=row[6],
            navidrome_track_id=row[7],
            availability_status=row[8],
            quarantine_reason=row[9],
        )

    # ------------------------------------------------------------------ #
    # Users
    # ------------------------------------------------------------------ #

    def create_user(
        self,
        user_id: str,
        email: str,
        password_hash: str,
        display_name: str,
        *,
        user_int_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized = email.strip().lower()
        with self._connect() as conn:
            with conn.cursor() as cur:
                if user_int_id is not None:
                    cur.execute(
                        """
                        INSERT INTO app.users (user_id, user_int_id, email, password_hash, display_name)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (user_id, user_int_id, normalized, password_hash, display_name),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO app.users (user_id, user_int_id, email, password_hash, display_name)
                        VALUES (%s, nextval('app.online_user_int_id_seq'), %s, %s, %s)
                        """,
                        (user_id, normalized, password_hash, display_name),
                    )
                cur.execute(
                    "SELECT user_id, user_int_id, email, display_name FROM app.users WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
                return {"user_id": row[0], "user_int_id": row[1], "email": row[2], "display_name": row[3], "password_hash": password_hash}

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        normalized = email.strip().lower()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, user_int_id, email, password_hash, display_name FROM app.users WHERE email = %s",
                    (normalized,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {"user_id": row[0], "user_int_id": row[1], "email": row[2], "password_hash": row[3], "display_name": row[4]}

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, user_int_id, email, password_hash, display_name FROM app.users WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {"user_id": row[0], "user_int_id": row[1], "email": row[2], "password_hash": row[3], "display_name": row[4]}

    # ------------------------------------------------------------------ #
    # Auth sessions (Bearer token)
    # ------------------------------------------------------------------ #

    def create_auth_session(
        self,
        *,
        token_hash: str,
        user_id: str,
        expires_at: datetime,
        # session_id kept for interface compatibility but not stored
        session_id: str = "",
    ) -> AuthenticatedSession:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.auth_sessions (token_hash, user_id, expires_at)
                    VALUES (%s, %s, %s)
                    """,
                    (token_hash, user_id, expires_at),
                )
                cur.execute(
                    "SELECT email, display_name FROM app.users WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
                email = row[0] if row else ""
                display_name = row[1] if row else ""
        return AuthenticatedSession(
            user_id=user_id,
            session_id=session_id,
            email=email,
            display_name=display_name or "",
        )

    def get_auth_session(self, token_hash: str) -> Optional[AuthenticatedSession]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT a.user_id, u.email, u.display_name
                    FROM app.auth_sessions a
                    JOIN app.users u ON u.user_id = a.user_id
                    WHERE a.token_hash = %s
                      AND a.expires_at > NOW()
                      AND a.revoked_at IS NULL
                    """,
                    (token_hash,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return AuthenticatedSession(
                    user_id=row[0],
                    session_id="",
                    email=row[1],
                    display_name=row[2] or "",
                )

    def revoke_auth_session(self, token_hash: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE app.auth_sessions SET revoked_at = NOW() WHERE token_hash = %s AND revoked_at IS NULL",
                    (token_hash,),
                )
                return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # Recommendation sessions
    # ------------------------------------------------------------------ #

    def create_rec_session(self, session_id: str, user_int_id: int) -> int:
        """Create a rec_session row and return the assigned session_int_id."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.rec_sessions (session_id, user_int_id, session_int_id)
                    VALUES (%s, %s, nextval('app.session_int_id_seq'))
                    RETURNING session_int_id
                    """,
                    (session_id, user_int_id),
                )
                return cur.fetchone()[0]

    # ------------------------------------------------------------------ #
    # Playback events
    # ------------------------------------------------------------------ #

    def insert_playback_event(
        self,
        *,
        event_id: str,
        session_int_id: int,
        user_int_id: int,
        track_id: str,
        position: int,
        event_type: str,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.playback_events
                        (event_id, session_int_id, user_int_id, track_id, position, event_type)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (event_id, session_int_id, user_int_id, int(track_id), position, event_type),
                )

    def update_playback_event(self, event_id: str, event_type: str, playratio: float) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.playback_events
                    SET event_type = %s, playratio = %s
                    WHERE event_id = %s
                    """,
                    (event_type, playratio, event_id),
                )

    # ------------------------------------------------------------------ #
    # Model status (soft rollback flag)
    # ------------------------------------------------------------------ #

    def get_model_status(self) -> Dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT degraded, reason, updated_at FROM app.model_status WHERE id = 1")
                row = cur.fetchone()
                if not row:
                    return {"degraded": False, "reason": None}
                return {"degraded": row[0], "reason": row[1], "updated_at": row[2]}

    def upsert_model_status(self, degraded: bool, reason: Optional[str] = None) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.model_status (id, degraded, reason, updated_at)
                    VALUES (1, %s, %s, NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        degraded = EXCLUDED.degraded,
                        reason = EXCLUDED.reason,
                        updated_at = NOW()
                    """,
                    (degraded, reason),
                )

    # ------------------------------------------------------------------ #
    # Delta checkpoint watermarks
    # ------------------------------------------------------------------ #

    def get_last_delta_checkpoint(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT version, exported_at, session_int_id_watermark, rows_exported
                    FROM app.delta_checkpoint
                    ORDER BY id DESC LIMIT 1
                    """
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "version": row[0],
                    "exported_at": row[1],
                    "session_int_id_watermark": row[2],
                    "rows_exported": row[3],
                }

    def insert_delta_checkpoint(
        self, version: str, session_int_id_watermark: int, rows_exported: Dict[str, Any]
    ) -> None:
        import json as _json
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.delta_checkpoint (version, session_int_id_watermark, rows_exported)
                    VALUES (%s, %s, %s)
                    """,
                    (version, session_int_id_watermark, _json.dumps(rows_exported)),
                )

    # ------------------------------------------------------------------ #
    # Monitoring summary (used by /monitoring/summary endpoint)
    # ------------------------------------------------------------------ #

    def get_monitoring_summary(self) -> Dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT version, exported_at, session_int_id_watermark, rows_exported
                    FROM app.delta_checkpoint ORDER BY id DESC LIMIT 1
                    """
                )
                row = cur.fetchone()
                last_export = None
                if row:
                    last_export = {
                        "version": row[0],
                        "exported_at": row[1].isoformat() if row[1] else None,
                        "session_int_id_watermark": row[2],
                        "rows_exported": row[3],
                    }

                cur.execute("SELECT degraded, reason, updated_at FROM app.model_status WHERE id = 1")
                row = cur.fetchone()
                model_status = {"degraded": False, "reason": None}
                if row:
                    model_status = {"degraded": row[0], "reason": row[1]}

                cur.execute("SELECT COUNT(*) FROM app.users")
                user_count = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM app.playback_events WHERE playratio IS NOT NULL")
                finalized_events = cur.fetchone()[0]

                return {
                    "last_delta_export": last_export,
                    "model_status": model_status,
                    "user_count": user_count,
                    "finalized_playback_events": finalized_events,
                }

    # ------------------------------------------------------------------ #
    # Loved tracks & playlists (written by delta-export-worker)
    # ------------------------------------------------------------------ #

    def upsert_loved_track(self, user_int_id: int, track_id: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.loved_tracks (user_int_id, track_id)
                    VALUES (%s, %s)
                    ON CONFLICT (user_int_id, track_id) DO NOTHING
                    """,
                    (user_int_id, track_id),
                )

    def upsert_user_playlist(self, user_int_id: int, nav_playlist_id: str, name: str) -> int:
        """Returns playlist_int_id."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.user_playlists (user_int_id, nav_playlist_id, name, synced_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (nav_playlist_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        synced_at = NOW()
                    RETURNING playlist_int_id
                    """,
                    (user_int_id, nav_playlist_id, name),
                )
                return cur.fetchone()[0]

    def replace_playlist_tracks(self, playlist_int_id: int, tracks: List[Dict[str, Any]]) -> None:
        """Replace all tracks for a playlist."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM app.playlist_tracks WHERE playlist_int_id = %s", (playlist_int_id,))
                for item in tracks:
                    cur.execute(
                        "INSERT INTO app.playlist_tracks (playlist_int_id, position, track_id) VALUES (%s, %s, %s)",
                        (playlist_int_id, item["position"], item["track_id"]),
                    )

    # ------------------------------------------------------------------ #
    # Legacy interface stubs (kept so existing callers don't crash)
    # ------------------------------------------------------------------ #

    def list_disliked_track_ids(self, user_id: str) -> List[str]:
        return []

    def persist_recommendation_impression(self, impression_id: str, payload: Dict[str, Any]) -> bool:
        return True

    def persist_rendered_impression(self, impression_id: str, payload: Dict[str, Any]) -> bool:
        return True

    def persist_playback_event(self, event_id: str, payload: Dict[str, Any]) -> bool:
        return True

    def persist_feedback_event(self, event_id: str, payload: Dict[str, Any]) -> bool:
        return True

    def persist_ingestion_rejection(self, event_type: str, reasons: List[str], raw_payload: Dict[str, Any]) -> None:
        pass

    def record_serving_request_metric(self, payload: Dict[str, Any]) -> None:
        pass

    def get_active_model_version(self) -> Optional[Dict[str, Any]]:
        return None

    def latest_serving_metric_rollup(self, window_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return None

    def latest_model_trigger_decision(self, decision_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return None

    def register_active_model_version(self, model_version: str, serving_bundle_version: str, manifest_uri: str) -> None:
        pass

    def seed_demo_state(self, user_id: str, session_id: str) -> None:
        pass
