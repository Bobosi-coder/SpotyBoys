from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


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

    def seed_demo_state(self, user_id: str, session_id: str) -> None:
        return None
