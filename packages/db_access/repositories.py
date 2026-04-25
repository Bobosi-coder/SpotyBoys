from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from packages.auth import AuthenticatedSession


@dataclass(frozen=True)
class PlayableTrackRecord:
    track_id: str
    title: str
    artist: str
    album: str
    duration_sec: int
    cover_art_url: str
    is_playable: bool
    navidrome_track_id: Optional[str]
    availability_status: str = "available"
    quarantine_reason: Optional[str] = None


class DemoRepository:
    """In-memory repository with Postgres-shaped operations for tests and demo mode."""

    def __init__(self, tracks: Iterable[PlayableTrackRecord]) -> None:
        self._tracks: Dict[str, PlayableTrackRecord] = {track.track_id: track for track in tracks}
        self.users: Dict[str, Dict[str, Any]] = {}
        self.users_by_email: Dict[str, str] = {}
        self.auth_sessions: Dict[str, Dict[str, Any]] = {}
        self.recommendation_impressions: Dict[str, Dict[str, Any]] = {}
        self.rendered_impressions: Dict[str, Dict[str, Any]] = {}
        self.playback_events: Dict[str, Dict[str, Any]] = {}
        self.feedback_events: Dict[str, Dict[str, Any]] = {}
        self.serving_request_metrics: Dict[str, Dict[str, Any]] = {}
        self.serving_metric_rollups: Dict[str, Dict[str, Any]] = {}
        self.model_trigger_decisions: Dict[str, Dict[str, Any]] = {}
        self.model_versions: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def from_fixture(cls, path: str | Path) -> "DemoRepository":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            PlayableTrackRecord(
                track_id=str(row["track_id"]),
                title=str(row["title"]),
                artist=str(row["artist"]),
                album=str(row.get("album", "")),
                duration_sec=int(row.get("duration_sec", 0)),
                cover_art_url=str(row.get("cover_art_url", "")),
                is_playable=bool(row.get("is_playable", False)),
                navidrome_track_id=row.get("navidrome_track_id"),
                availability_status=str(row.get("availability_status", "available")),
                quarantine_reason=row.get("quarantine_reason"),
            )
            for row in payload["tracks"]
        )

    def list_playable_tracks(self) -> List[PlayableTrackRecord]:
        return [
            track
            for track in self._tracks.values()
            if track.is_playable
            and track.navidrome_track_id
            and track.availability_status == "available"
            and not track.quarantine_reason
        ]

    def get_track(self, track_id: str) -> Optional[PlayableTrackRecord]:
        return self._tracks.get(str(track_id))

    def get_playable_track(self, track_id: str) -> Optional[PlayableTrackRecord]:
        track = self.get_track(track_id)
        if not track:
            return None
        if not track.is_playable or not track.navidrome_track_id:
            return None
        if track.availability_status != "available" or track.quarantine_reason:
            return None
        return track

    def persist_recommendation_impression(self, impression_id: str, payload: Dict[str, Any]) -> bool:
        if impression_id in self.recommendation_impressions:
            return False
        self.recommendation_impressions[impression_id] = dict(payload)
        return True

    def persist_rendered_impression(self, impression_id: str, payload: Dict[str, Any]) -> bool:
        if impression_id in self.rendered_impressions:
            return False
        self.rendered_impressions[impression_id] = dict(payload)
        return True

    def persist_playback_event(self, event_id: str, payload: Dict[str, Any]) -> bool:
        if event_id in self.playback_events:
            return False
        self.playback_events[event_id] = dict(payload)
        return True

    def persist_feedback_event(self, event_id: str, payload: Dict[str, Any]) -> bool:
        if event_id in self.feedback_events:
            return False
        self.feedback_events[event_id] = dict(payload)
        return True

    def persist_ingestion_rejection(
        self, event_type: str, reasons: List[str], raw_payload: Dict[str, Any]
    ) -> None:
        # In-memory demo mode: log only, no persistent store needed.
        pass

    def create_user(self, user_id: str, email: str, password_hash: str, display_name: str) -> Dict[str, Any]:
        normalized = email.strip().lower()
        if normalized in self.users_by_email:
            raise ValueError("email already registered")
        user_int_id = getattr(self, "_next_user_int_id", 100000)
        self._next_user_int_id = user_int_id + 1
        record = {
            "user_id": user_id,
            "email": normalized,
            "password_hash": password_hash,
            "display_name": display_name or normalized.split("@")[0],
            "user_int_id": user_int_id,
        }
        self.users[user_id] = record
        self.users_by_email[normalized] = user_id
        return dict(record)

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        user_id = self.users_by_email.get(email.strip().lower())
        return dict(self.users[user_id]) if user_id else None

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        record = self.users.get(user_id)
        return dict(record) if record else None

    def create_auth_session(
        self,
        *,
        token_hash: str,
        user_id: str,
        session_id: str,
        expires_at: datetime,
    ) -> AuthenticatedSession:
        user = self.users[user_id]
        self.auth_sessions[token_hash] = {
            "user_id": user_id,
            "session_id": session_id,
            "expires_at": expires_at,
            "revoked": False,
        }
        return AuthenticatedSession(
            user_id=user_id,
            session_id=session_id,
            email=user["email"],
            display_name=user.get("display_name", ""),
        )

    def get_auth_session(self, token_hash: str) -> Optional[AuthenticatedSession]:
        record = self.auth_sessions.get(token_hash)
        if not record or record.get("revoked"):
            return None
        expires_at = record["expires_at"]
        if expires_at < datetime.now(timezone.utc):
            return None
        user = self.users.get(record["user_id"])
        if not user:
            return None
        return AuthenticatedSession(
            user_id=record["user_id"],
            session_id=record["session_id"],
            email=user["email"],
            display_name=user.get("display_name", ""),
        )

    def revoke_auth_session(self, token_hash: str) -> bool:
        record = self.auth_sessions.get(token_hash)
        if not record:
            return False
        record["revoked"] = True
        return True

    def list_disliked_track_ids(self, user_id: str) -> List[str]:
        return []

    def seed_demo_state(self, user_id: str, session_id: str) -> None:
        return None

    def create_rec_session(self, session_id: str, user_int_id: int) -> int:
        self._next_session_int_id = getattr(self, "_next_session_int_id", 3000000) + 1
        return self._next_session_int_id

    def insert_playback_event(self, *, event_id: str, session_int_id: int, user_int_id: int,
                               track_id: str, position: int, event_type: str) -> None:
        self.playback_events[event_id] = {
            "event_id": event_id, "session_int_id": session_int_id,
            "user_int_id": user_int_id, "track_id": track_id,
            "position": position, "event_type": event_type, "playratio": None,
        }

    def update_playback_event(self, event_id: str, event_type: str, playratio: float) -> None:
        if event_id in self.playback_events:
            self.playback_events[event_id]["event_type"] = event_type
            self.playback_events[event_id]["playratio"] = playratio

    def get_model_status(self) -> Dict[str, Any]:
        return {"degraded": False, "reason": None}

    def upsert_model_status(self, degraded: bool, reason: Any = None) -> None:
        pass

    def get_last_delta_checkpoint(self) -> None:
        return None

    def insert_delta_checkpoint(self, version: str, session_int_id_watermark: int, rows_exported: Dict[str, Any]) -> None:
        pass

    def get_monitoring_summary(self) -> Dict[str, Any]:
        return {"last_delta_export": None, "model_status": {"degraded": False}, "user_count": 0, "finalized_playback_events": 0}

    def upsert_loved_track(self, user_int_id: int, track_id: int) -> None:
        pass

    def upsert_user_playlist(self, user_int_id: int, nav_playlist_id: str, name: str) -> int:
        return 0

    def replace_playlist_tracks(self, playlist_int_id: int, tracks: List[Dict[str, Any]]) -> None:
        pass

    def register_active_model_version(self, model_version: str, serving_bundle_version: str, manifest_uri: str) -> None:
        current = self.get_active_model_version()
        if current and current["model_version"] != model_version:
            self.model_versions[current["model_version"]]["is_active"] = False
            self.model_versions[current["model_version"]]["status"] = "previous_good"
            self.model_versions[current["model_version"]]["deactivated_at"] = datetime.now(timezone.utc).isoformat()
        self.model_versions[model_version] = {
            "model_version": model_version,
            "serving_bundle_version": serving_bundle_version,
            "manifest_uri": manifest_uri,
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "is_active": True,
            "status": "active",
            "rollback_parent_version": current["model_version"] if current and current["model_version"] != model_version else None,
        }
        return None

    def get_active_model_version(self) -> Optional[Dict[str, Any]]:
        for record in self.model_versions.values():
            if record.get("is_active"):
                return dict(record)
        return None

    def get_previous_good_model_version(self) -> Optional[Dict[str, Any]]:
        candidates = [record for record in self.model_versions.values() if record.get("status") == "previous_good"]
        if not candidates:
            return None
        return dict(sorted(candidates, key=lambda item: str(item.get("deactivated_at") or ""), reverse=True)[0])

    def record_serving_request_metric(self, payload: Dict[str, Any]) -> None:
        metric = dict(payload)
        metric.setdefault("metric_id", f"metric_{len(self.serving_request_metrics) + 1}")
        metric.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        self.serving_request_metrics[str(metric["metric_id"])] = metric

    def get_monitoring_inputs(self, window_start: datetime, window_end: datetime) -> Dict[str, List[Dict[str, Any]]]:
        return {
            "request_metrics": list(self.serving_request_metrics.values()),
            "recommendation_impressions": list(self.recommendation_impressions.values()),
            "rendered_impressions": list(self.rendered_impressions.values()),
            "playback_events": list(self.playback_events.values()),
            "feedback_events": list(self.feedback_events.values()),
        }

    def write_serving_metric_rollup(self, payload: Dict[str, Any]) -> None:
        self.serving_metric_rollups[str(payload["rollup_id"])] = dict(payload)

    def latest_serving_metric_rollup(self, window_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        rows = list(self.serving_metric_rollups.values())
        if window_name:
            rows = [row for row in rows if row.get("window_name") == window_name]
        if not rows:
            return None
        return dict(sorted(rows, key=lambda item: str(item.get("created_at") or item.get("window_end") or ""), reverse=True)[0])

    def record_model_trigger_decision(self, payload: Dict[str, Any]) -> None:
        self.model_trigger_decisions[str(payload["decision_id"])] = dict(payload)

    def latest_model_trigger_decision(self, decision_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        rows = list(self.model_trigger_decisions.values())
        if decision_type:
            rows = [row for row in rows if row.get("decision_type") == decision_type]
        if not rows:
            return None
        return dict(sorted(rows, key=lambda item: str(item.get("created_at") or ""), reverse=True)[0])

    def get_mapped_track_ids(self) -> set:
        return {tid for tid, t in self._tracks.items() if t.navidrome_track_id}

    def upsert_playable_mapping(
        self,
        track_id: str,
        navidrome_track_id: str,
        *,
        mapping_confidence: float = 1.0,
        availability_status: str = "available",
        quarantine_reason: Optional[str] = None,
    ) -> None:
        track = self._tracks.get(track_id)
        if not track:
            return
        self._tracks[track_id] = PlayableTrackRecord(
            track_id=track.track_id,
            title=track.title,
            artist=track.artist,
            album=track.album,
            duration_sec=track.duration_sec,
            cover_art_url=track.cover_art_url,
            is_playable=availability_status == "available" and quarantine_reason is None,
            navidrome_track_id=navidrome_track_id,
            availability_status=availability_status,
            quarantine_reason=quarantine_reason,
        )

    def upsert_playable_track(self, track: PlayableTrackRecord) -> None:
        self._tracks[track.track_id] = track
