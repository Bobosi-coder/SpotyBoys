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

    def get_playable_track_by_navidrome_id(self, navidrome_track_id: str) -> Optional[PlayableTrackRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT track_id, title, artist, COALESCE(album, ''), duration_sec,
                           COALESCE(cover_art_url, ''), is_playable, navidrome_track_id,
                           availability_status, quarantine_reason
                    FROM app.playable_tracks
                    WHERE navidrome_track_id = %s
                      AND is_playable = TRUE
                      AND availability_status = 'available'
                      AND quarantine_reason IS NULL
                    """,
                    (str(navidrome_track_id),),
                )
                row = cur.fetchone()
                return self._track_from_row(row) if row else None

    def get_mapped_track_ids(self) -> set:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT track_id FROM app.playable_tracks WHERE navidrome_track_id IS NOT NULL"
                )
                return {row[0] for row in cur.fetchall()}

    def get_mapped_track_map(self) -> Dict[str, str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT track_id, navidrome_track_id
                    FROM app.playable_tracks
                    WHERE navidrome_track_id IS NOT NULL
                    """
                )
                return {str(row[0]): str(row[1]) for row in cur.fetchall()}

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

    def bulk_upsert_playable_mappings(
        self,
        mappings: List[tuple[str, str, str, Optional[str]]],
    ) -> int:
        deduped: Dict[str, tuple[str, str, str, Optional[str]]] = {}
        for track_id, navidrome_track_id, availability_status, quarantine_reason in mappings:
            if not track_id or not navidrome_track_id:
                continue
            track_key = str(track_id)
            deduped[track_key] = (
                track_key,
                str(navidrome_track_id),
                availability_status,
                quarantine_reason,
            )
        rows = list(deduped.values())
        if not rows:
            return 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TEMP TABLE tmp_playable_mappings (
                        track_id TEXT PRIMARY KEY,
                        navidrome_track_id TEXT NOT NULL,
                        availability_status TEXT NOT NULL,
                        quarantine_reason TEXT
                    ) ON COMMIT DROP
                    """
                )
                from psycopg2.extras import execute_values

                execute_values(
                    cur,
                    """
                    INSERT INTO tmp_playable_mappings
                        (track_id, navidrome_track_id, availability_status, quarantine_reason)
                    VALUES %s
                    ON CONFLICT (track_id) DO UPDATE SET
                        navidrome_track_id = EXCLUDED.navidrome_track_id,
                        availability_status = EXCLUDED.availability_status,
                        quarantine_reason = EXCLUDED.quarantine_reason
                    """,
                    rows,
                    page_size=10000,
                )
                cur.execute(
                    """
                    UPDATE app.playable_tracks p
                    SET navidrome_track_id = m.navidrome_track_id,
                        is_playable = (m.availability_status = 'available' AND m.quarantine_reason IS NULL),
                        availability_status = m.availability_status,
                        quarantine_reason = m.quarantine_reason
                    FROM tmp_playable_mappings m
                    WHERE p.track_id = m.track_id
                      AND (
                        p.navidrome_track_id IS DISTINCT FROM m.navidrome_track_id
                        OR p.availability_status IS DISTINCT FROM m.availability_status
                        OR p.quarantine_reason IS DISTINCT FROM m.quarantine_reason
                        OR p.is_playable IS DISTINCT FROM (
                            m.availability_status = 'available' AND m.quarantine_reason IS NULL
                        )
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM app.playable_tracks p2
                        WHERE p2.navidrome_track_id = m.navidrome_track_id
                          AND p2.track_id != p.track_id
                          AND NOT EXISTS (
                            SELECT 1
                            FROM tmp_playable_mappings m2
                            WHERE m2.track_id = p2.track_id
                              AND m2.navidrome_track_id IS DISTINCT FROM m.navidrome_track_id
                          )
                      )
                    """
                )
                return int(cur.rowcount or 0)

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

    def get_rec_session_int_id(self, session_id: str) -> Optional[int]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT session_int_id FROM app.rec_sessions WHERE session_id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
                return int(row[0]) if row else None

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
                    SET event_type = %s, playratio = %s, updated_at = NOW()
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
                cur.execute("SELECT degraded, reason, action, updated_at FROM app.model_status WHERE id = 1")
                row = cur.fetchone()
                if not row:
                    return {"degraded": False, "reason": None, "action": "normal"}
                return {"degraded": row[0], "reason": row[1], "action": row[2] or "normal", "updated_at": row[3]}

    def upsert_model_status(self, degraded: bool, reason: Optional[str] = None, action: str = "normal") -> None:
        action = action if action in {"normal", "fallback_only"} else "normal"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.model_status (id, degraded, reason, action, updated_at)
                    VALUES (1, %s, %s, %s, NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        degraded = EXCLUDED.degraded,
                        reason = EXCLUDED.reason,
                        action = EXCLUDED.action,
                        updated_at = NOW()
                    """,
                    (degraded, reason, action),
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

                cur.execute("SELECT degraded, reason, action, updated_at FROM app.model_status WHERE id = 1")
                row = cur.fetchone()
                model_status = {"degraded": False, "reason": None, "action": "normal"}
                if row:
                    model_status = {
                        "degraded": row[0],
                        "reason": row[1],
                        "action": row[2] or "normal",
                        "updated_at": row[3].isoformat() if row[3] else None,
                    }

                cur.execute("SELECT COUNT(*) FROM app.users")
                user_count = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM app.playback_events WHERE playratio IS NOT NULL")
                finalized_events = cur.fetchone()[0]

                return {
                    "last_delta_export": last_export,
                    "model_status": model_status,
                    "user_count": user_count,
                    "finalized_playback_events": finalized_events,
                    "active_model_version": self.get_active_model_version(),
                    "previous_good_model_version": self.get_previous_good_model_version(),
                    "latest_rollup_5m": self.latest_serving_metric_rollup("5m"),
                    "latest_rollup_1h": self.latest_serving_metric_rollup("1h"),
                    "latest_rollback_decision": self.latest_model_trigger_decision("rollback_check"),
                    "latest_promotion_decision": self.latest_model_trigger_decision("promotion_gate"),
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

    def persist_ingestion_rejection(self, source: str, reason: str, raw_ref: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO app.ingestion_rejections (source, reason, raw_ref) VALUES (%s, %s, %s)",
                    (source, reason, raw_ref),
                )

    def record_serving_request_metric(self, payload: Dict[str, Any]) -> None:
        import uuid

        metric = dict(payload)
        metric.setdefault("metric_id", f"metric_{uuid.uuid4().hex}")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.serving_request_metrics (
                        metric_id, request_id, session_id, user_id, model_version,
                        fallback_level, fallback_state, candidate_count, playable_count,
                        returned_count, pipeline_latency_ms, total_latency_ms,
                        pipeline_error, error_code, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()))
                    ON CONFLICT (metric_id) DO NOTHING
                    """,
                    (
                        str(metric["metric_id"]),
                        metric.get("request_id"),
                        metric.get("session_id"),
                        metric.get("user_id"),
                        metric.get("model_version"),
                        metric.get("fallback_level", "none"),
                        metric.get("fallback_state", "healthy"),
                        int(metric.get("candidate_count") or 0),
                        int(metric.get("playable_count") or 0),
                        int(metric.get("returned_count") or 0),
                        float(metric.get("pipeline_latency_ms") or 0),
                        float(metric.get("total_latency_ms") or 0),
                        bool(metric.get("pipeline_error", False)),
                        metric.get("error_code"),
                        metric.get("created_at"),
                    ),
                )

    def get_monitoring_inputs(self, window_start: datetime, window_end: datetime) -> Dict[str, List[Dict[str, Any]]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT metric_id, request_id, session_id, user_id, model_version,
                           fallback_level, fallback_state, candidate_count, playable_count,
                           returned_count, pipeline_latency_ms, total_latency_ms,
                           pipeline_error, error_code, created_at
                    FROM app.serving_request_metrics
                    WHERE created_at >= %s AND created_at <= %s
                    ORDER BY created_at
                    """,
                    (window_start, window_end),
                )
                request_metrics = [
                    {
                        "metric_id": row[0],
                        "request_id": row[1],
                        "session_id": row[2],
                        "user_id": row[3],
                        "model_version": row[4],
                        "fallback_level": row[5],
                        "fallback_state": row[6],
                        "candidate_count": row[7],
                        "playable_count": row[8],
                        "returned_count": row[9],
                        "pipeline_latency_ms": row[10],
                        "total_latency_ms": row[11],
                        "pipeline_error": row[12],
                        "error_code": row[13],
                        "created_at": row[14],
                    }
                    for row in cur.fetchall()
                ]
                cur.execute(
                    """
                    SELECT event_id, session_int_id, user_int_id, track_id, position,
                           playratio, event_type,
                           CASE
                             WHEN event_type IN ('skip', 'complete') OR playratio IS NOT NULL
                             THEN COALESCE(updated_at, created_at)
                             ELSE created_at
                           END AS event_time
                    FROM app.playback_events
                    WHERE (
                        CASE
                          WHEN event_type IN ('skip', 'complete') OR playratio IS NOT NULL
                          THEN COALESCE(updated_at, created_at)
                          ELSE created_at
                        END
                    ) >= %s
                      AND (
                        CASE
                          WHEN event_type IN ('skip', 'complete') OR playratio IS NOT NULL
                          THEN COALESCE(updated_at, created_at)
                          ELSE created_at
                        END
                      ) <= %s
                    ORDER BY event_time
                    """,
                    (window_start, window_end),
                )
                playback_events = [
                    {
                        "event_id": row[0],
                        "session_int_id": row[1],
                        "user_int_id": row[2],
                        "track_id": row[3],
                        "position": row[4],
                        "playratio": row[5],
                        "event_type": row[6],
                        "created_at": row[7],
                    }
                    for row in cur.fetchall()
                ]
        return {
            "request_metrics": request_metrics,
            "recommendation_impressions": [],
            "rendered_impressions": [],
            "playback_events": playback_events,
            "feedback_events": [],
        }

    def write_serving_metric_rollup(self, payload: Dict[str, Any]) -> None:
        from psycopg2.extras import Json

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.serving_metric_rollups (
                        rollup_id, window_name, window_start, window_end, model_version,
                        request_count, error_rate, fallback_rate, p50_latency_ms,
                        p95_latency_ms, avg_returned_count, catalog_failure_count,
                        stream_failure_count, completion_rate, skip_rate, dislike_rate,
                        top_artist_share, repeat_violation_count, sample_status,
                        metrics_json, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()))
                    ON CONFLICT (rollup_id) DO NOTHING
                    """,
                    (
                        payload["rollup_id"],
                        payload["window_name"],
                        payload["window_start"],
                        payload["window_end"],
                        payload.get("model_version"),
                        int(payload.get("request_count") or 0),
                        float(payload.get("error_rate") or 0),
                        float(payload.get("fallback_rate") or 0),
                        float(payload.get("p50_latency_ms") or 0),
                        float(payload.get("p95_latency_ms") or 0),
                        float(payload.get("avg_returned_count") or 0),
                        int(payload.get("catalog_failure_count") or 0),
                        int(payload.get("stream_failure_count") or 0),
                        float(payload.get("completion_rate") or 0),
                        float(payload.get("skip_rate") or 0),
                        float(payload.get("dislike_rate") or 0),
                        float(payload.get("top_artist_share") or 0),
                        int(payload.get("repeat_violation_count") or 0),
                        payload.get("sample_status", "insufficient"),
                        Json(payload.get("metrics_json") or {}),
                        payload.get("created_at"),
                    ),
                )

    def get_active_model_version(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT model_version, serving_bundle_version, manifest_uri, status,
                           is_active, activated_at, deactivated_at, rollback_parent_version
                    FROM app.model_versions
                    WHERE is_active = TRUE
                    ORDER BY activated_at DESC NULLS LAST LIMIT 1
                    """
                )
                row = cur.fetchone()
                return self._model_version_row(row) if row else None

    def get_previous_good_model_version(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT model_version, serving_bundle_version, manifest_uri, status,
                           is_active, activated_at, deactivated_at, rollback_parent_version
                    FROM app.model_versions
                    WHERE status = 'previous_good'
                    ORDER BY deactivated_at DESC NULLS LAST, created_at DESC LIMIT 1
                    """
                )
                row = cur.fetchone()
                return self._model_version_row(row) if row else None

    def latest_serving_metric_rollup(self, window_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        where = "WHERE window_name = %s" if window_name else ""
        params = (window_name,) if window_name else ()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT rollup_id, window_name, window_start, window_end, model_version,
                           request_count, error_rate, fallback_rate, p50_latency_ms,
                           p95_latency_ms, avg_returned_count, catalog_failure_count,
                           stream_failure_count, completion_rate, skip_rate, dislike_rate,
                           top_artist_share, repeat_violation_count, sample_status,
                           metrics_json, created_at
                    FROM app.serving_metric_rollups
                    {where}
                    ORDER BY window_end DESC, created_at DESC LIMIT 1
                    """,
                    params,
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "rollup_id": row[0],
                    "window_name": row[1],
                    "window_start": row[2].isoformat() if row[2] else None,
                    "window_end": row[3].isoformat() if row[3] else None,
                    "model_version": row[4],
                    "request_count": row[5],
                    "error_rate": row[6],
                    "fallback_rate": row[7],
                    "p50_latency_ms": row[8],
                    "p95_latency_ms": row[9],
                    "avg_returned_count": row[10],
                    "catalog_failure_count": row[11],
                    "stream_failure_count": row[12],
                    "completion_rate": row[13],
                    "skip_rate": row[14],
                    "dislike_rate": row[15],
                    "top_artist_share": row[16],
                    "repeat_violation_count": row[17],
                    "sample_status": row[18],
                    "metrics_json": row[19] or {},
                    "created_at": row[20].isoformat() if row[20] else None,
                }

    def latest_model_trigger_decision(self, decision_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        where = "WHERE decision_type = %s" if decision_type else ""
        params = (decision_type,) if decision_type else ()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT decision_id, decision_type, model_version, decision, reason,
                           metrics_json, thresholds_json, created_at, executed, execution_note
                    FROM app.model_trigger_decisions
                    {where}
                    ORDER BY created_at DESC LIMIT 1
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
                    "decision": row[3],
                    "reason": row[4],
                    "metrics_json": row[5] or {},
                    "thresholds_json": row[6] or {},
                    "created_at": row[7].isoformat() if row[7] else None,
                    "executed": row[8],
                    "execution_note": row[9],
                }

    def record_model_trigger_decision(self, payload: Dict[str, Any]) -> None:
        from psycopg2.extras import Json
        import uuid

        decision = dict(payload)
        decision.setdefault("decision_id", f"decision_{uuid.uuid4().hex}")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.model_trigger_decisions (
                        decision_id, decision_type, model_version, decision, reason,
                        metrics_json, thresholds_json, created_at, executed, execution_note
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()), %s, %s)
                    ON CONFLICT (decision_id) DO NOTHING
                    """,
                    (
                        decision["decision_id"],
                        decision["decision_type"],
                        decision.get("model_version"),
                        decision["decision"],
                        decision.get("reason"),
                        Json(decision.get("metrics_json") or {}),
                        Json(decision.get("thresholds_json") or {}),
                        decision.get("created_at"),
                        bool(decision.get("executed", False)),
                        decision.get("execution_note"),
                    ),
                )

    def register_active_model_version(self, model_version: str, serving_bundle_version: str, manifest_uri: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT model_version FROM app.model_versions WHERE is_active = TRUE LIMIT 1")
                current = cur.fetchone()
                parent = current[0] if current and current[0] != model_version else None
                if parent:
                    cur.execute(
                        """
                        UPDATE app.model_versions
                        SET is_active = FALSE, status = 'previous_good', deactivated_at = NOW()
                        WHERE is_active = TRUE
                        """
                    )
                cur.execute(
                    """
                    INSERT INTO app.model_versions (
                        model_version, serving_bundle_version, manifest_uri, status,
                        is_active, activated_at, rollback_parent_version
                    )
                    VALUES (%s, %s, %s, 'active', TRUE, NOW(), %s)
                    ON CONFLICT (model_version) DO UPDATE SET
                        serving_bundle_version = EXCLUDED.serving_bundle_version,
                        manifest_uri = EXCLUDED.manifest_uri,
                        status = 'active',
                        is_active = TRUE,
                        activated_at = NOW(),
                        deactivated_at = NULL,
                        rollback_parent_version = EXCLUDED.rollback_parent_version
                    """,
                    (model_version, serving_bundle_version, manifest_uri, parent),
                )

    def seed_demo_state(self, user_id: str, session_id: str) -> None:
        pass

    @staticmethod
    def _model_version_row(row) -> Dict[str, Any]:
        return {
            "model_version": row[0],
            "serving_bundle_version": row[1],
            "manifest_uri": row[2],
            "status": row[3],
            "is_active": row[4],
            "activated_at": row[5].isoformat() if row[5] else None,
            "deactivated_at": row[6].isoformat() if row[6] else None,
            "rollback_parent_version": row[7],
        }
