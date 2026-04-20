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

    def create_user(self, user_id: str, email: str, password_hash: str, display_name: str) -> Dict[str, Any]:
        normalized = email.strip().lower()
        if normalized in self.users_by_email:
            raise ValueError("email already registered")
        record = {
            "user_id": user_id,
            "email": normalized,
            "password_hash": password_hash,
            "display_name": display_name or normalized.split("@")[0],
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
        return [
            str(event["track_id"])
            for event in self.feedback_events.values()
            if event.get("user_id") == user_id and event.get("feedback_type") == "dislike"
        ]

    def seed_demo_state(self, user_id: str, session_id: str) -> None:
        return None

    def register_active_model_version(self, model_version: str, serving_bundle_version: str, manifest_uri: str) -> None:
        return None

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
