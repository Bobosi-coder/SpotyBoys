from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator

from .enums import BrowseSurfaceSlot, FallbackLevel, FeedbackType, PlaybackEventType


FEATURED_LIMIT = 4
RANDOM_LIMIT = 10


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class TrackItem(BaseModel):
    track_id: str
    navidrome_track_id: Optional[str] = None
    title: str
    artist: str
    album: str = ""
    duration_sec: int = Field(..., ge=0)
    cover_art_url: str = ""
    surface_slot: Optional[BrowseSurfaceSlot] = None


class QueueItem(BaseModel):
    track_id: str
    navidrome_track_id: Optional[str] = None
    title: str
    artist: str
    album: str = ""
    duration_sec: int = Field(..., ge=0)
    cover_art_url: str = ""
    queue_position: int = Field(..., ge=1)
    request_id: str
    impression_id: str


class BrowseSurface(BaseModel):
    featured_items: List[TrackItem] = Field(default_factory=list)
    random_carousel_items: List[TrackItem] = Field(default_factory=list)

    @validator("featured_items", pre=True, always=True)
    def cap_featured(cls, value: Any) -> List[Any]:
        return list(value or [])[:FEATURED_LIMIT]

    @validator("random_carousel_items", pre=True, always=True)
    def cap_random(cls, value: Any) -> List[Any]:
        return list(value or [])[:RANDOM_LIMIT]


class QueueState(BaseModel):
    items: List[QueueItem] = Field(default_factory=list)
    fallback_level: FallbackLevel = FallbackLevel.NONE
    generated_at: datetime = Field(default_factory=utc_now)
    drawer_default_open: bool = False
    revision: int = Field(1, ge=1)

    @validator("drawer_default_open", pre=True, always=True)
    def drawer_defaults_closed(cls, value: Any) -> bool:
        return False if value is None else bool(value)


class QueueUpdate(BaseModel):
    items: List[QueueItem] = Field(default_factory=list)
    revision: int = Field(1, ge=1)


class DegradedState(BaseModel):
    logging: bool = False
    recommendations: bool = False


class AuthUser(BaseModel):
    user_id: str
    email: str
    display_name: str = ""


class SignupRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=1)
    display_name: str = ""

    @validator("email")
    def normalize_email(cls, value: str) -> str:
        email = value.strip().lower()
        if "@" not in email:
            raise ValueError("valid email is required")
        return email


class LoginRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=1)

    @validator("email")
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class AuthResponse(BaseModel):
    user: AuthUser
    session_id: str
    auth_state: str = "authenticated"


class LogoutResponse(BaseModel):
    status: str = "ok"


class BootstrapResponse(BaseModel):
    session_id: str
    user_id: str
    auth_state: str = "authenticated"
    browse_surface: BrowseSurface
    queue: QueueState
    current_track: Optional[TrackItem] = None
    degraded: DegradedState = Field(default_factory=DegradedState)


class RecommendationRequest(BaseModel):
    session_id: str
    user_id: str
    request_id: Optional[str] = None
    seed_track_ids: List[str] = Field(default_factory=list)
    queue_revision: Optional[int] = None


class RecommendationResponse(BaseModel):
    request_id: str
    impression_id: str
    model_version: str
    fallback_level: FallbackLevel
    fallback_state: str = "healthy"
    degraded: bool = False
    degraded_reason: Optional[str] = None
    degraded_action: str = "normal"
    browse_surface: BrowseSurface
    queue: QueueUpdate


class VisibleItem(BaseModel):
    track_id: str
    surface_slot: BrowseSurfaceSlot


class ImpressionEventRequest(BaseModel):
    impression_id: str
    request_id: str
    session_id: str
    user_id: str
    visible_items: List[VisibleItem]
    surface: str = "browse_surface"
    rendered_at: datetime


class PlaybackEventRequest(BaseModel):
    event_id: str
    event_type: PlaybackEventType
    session_id: str
    user_id: str
    track_id: str
    request_id: str
    impression_id: str
    position_ms: int = Field(0, ge=0)
    playback_ms: int = Field(0, ge=0)
    occurred_at: datetime
    client_event_seq: int = Field(..., ge=1)


class FeedbackEventRequest(BaseModel):
    event_id: str
    feedback_type: FeedbackType
    session_id: str
    user_id: str
    track_id: str
    request_id: str
    impression_id: str
    occurred_at: datetime


class EventAck(BaseModel):
    status: str
    duplicate: bool = False
    event_id: Optional[str] = None
    impression_id: Optional[str] = None


class PlayableTrackResponse(BaseModel):
    track_id: str
    is_playable: bool
    stream_policy: str = "proxy"
    stream_path: str
    expires_at: Optional[datetime] = None


class DeltaManifest(BaseModel):
    version: str
    created_at: datetime
    output_files: List[str]
    row_counts: Dict[str, int] = Field(default_factory=dict)
    source: str = "vm1-postgres-parser"


class ServingBundleManifest(BaseModel):
    version: str
    created_at: datetime
    artifacts: List[str]
    model_version: str
    checksums: Dict[str, str] = Field(default_factory=dict)
