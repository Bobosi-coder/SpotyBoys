from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from packages.airflow_trigger import trigger_airflow_retrain
from packages.artifact_runtime import ServingBundle
from packages.auth import (
    hash_password,
    hash_session_token,
    new_session_id,
    new_session_token,
    new_user_id,
    require_authenticated_session,
    session_expires_at,
    verify_password,
)
from packages.config import load_config
from packages.db_access.factory import build_repository_and_runtime
from packages.navidrome_adapter import MediaAccessService
from packages.recommendation_engine import RecommendationService
from packages.shared_contracts.schemas import (
    LoginRequest,
    LogoutResponse,
    QueueState,
    RecommendationRequest,
    SignupRequest,
)

config = load_config()

repository, runtime_state = build_repository_and_runtime(config)
serving_bundle = ServingBundle.load(config.serving_bundle_path)
recommendation_service = RecommendationService(
    repository,
    runtime_state,
    serving_bundle,
    require_full_ml_pipeline=config.require_full_ml_pipeline,
)
media_service = MediaAccessService(repository, config=config)

app = FastAPI(title="SpotiBoys Recommendation API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ #
# Health
# ------------------------------------------------------------------ #

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "recommendation-api", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/ready")
def ready() -> dict:
    return {
        "status": "ready",
        "playable_tracks": len(repository.list_playable_tracks()),
        "model_version": serving_bundle.model_version,
        "media_mode": config.media_mode,
    }


# ------------------------------------------------------------------ #
# Auth — Bearer token (no cookies)
# ------------------------------------------------------------------ #

def _make_auth_response(token: str, user_id: str, display_name: str) -> dict:
    return {"token": token, "user_id": user_id, "display_name": display_name or ""}


@app.post("/auth/signup")
def signup(payload: SignupRequest) -> dict:
    existing = repository.get_user_by_email(payload.email)
    if existing:
        raise HTTPException(status_code=409, detail="email already registered")
    user_id = new_user_id()
    repository.create_user(user_id, payload.email, hash_password(payload.password), payload.display_name)
    token = new_session_token()
    expires_at = session_expires_at()
    repository.create_auth_session(
        token_hash=hash_session_token(token),
        user_id=user_id,
        expires_at=expires_at,
    )
    return _make_auth_response(token, user_id, payload.display_name)


@app.post("/auth/login")
def login(payload: LoginRequest) -> dict:
    user = repository.get_user_by_email(payload.email)
    if not user or not user.get("password_hash") or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid email or password")
    token = new_session_token()
    expires_at = session_expires_at()
    repository.create_auth_session(
        token_hash=hash_session_token(token),
        user_id=user["user_id"],
        expires_at=expires_at,
    )
    return _make_auth_response(token, user["user_id"], user.get("display_name", ""))


@app.post("/auth/logout", response_model=LogoutResponse)
def logout(request: Request) -> LogoutResponse:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
        if token:
            repository.revoke_auth_session(hash_session_token(token))
    return LogoutResponse()


@app.get("/auth/me")
def auth_me(request: Request) -> dict:
    auth_session = require_authenticated_session(request, repository)
    return {"user_id": auth_session.user_id, "email": auth_session.email, "display_name": auth_session.display_name}


# ------------------------------------------------------------------ #
# Session bootstrap (creates rec_session, runs recommendations)
# ------------------------------------------------------------------ #

@app.get("/session/bootstrap")
def bootstrap(request: Request) -> dict:
    auth_session = require_authenticated_session(request, repository)
    user = repository.get_user_by_id(auth_session.user_id)
    user_int_id: int = user["user_int_id"] if user else 0

    # Check if we already have a live queue for this user in Redis
    # (keyed by a stable user-scoped session key so page refresh reuses it)
    stable_key = f"user_session:{auth_session.user_id}"
    existing_session_id = runtime_state.get_string(stable_key)

    if existing_session_id:
        session_id = existing_session_id
        queue = runtime_state.get_queue(session_id)
    else:
        session_id = None
        queue = None

    if not session_id or not queue or not queue.items:
        session_id = new_session_id()
        repository.create_rec_session(session_id, user_int_id)
        runtime_state.set_string(stable_key, session_id, ttl_seconds=60 * 60 * 24 * 14)

        _, queue_items = recommendation_service.build_bootstrap_surfaces(
            session_id,
            auth_session.user_id,
        )
        queue = runtime_state.set_queue(session_id, queue_items)

    # Check model degraded flag (Redis-cached)
    model_degraded = _check_model_degraded()

    items = queue.items
    up_next = [_queue_item_dict(item) for item in items[:4]]
    remaining = [_queue_item_dict(item) for item in items[4:]]

    return {
        "session_id": session_id,
        "user_id": auth_session.user_id,
        "queue": {
            "up_next": up_next,
            "remaining": remaining,
            "revision": queue.revision,
            "fallback_level": queue.fallback_level.value if hasattr(queue.fallback_level, "value") else str(queue.fallback_level),
        },
        "model_version": serving_bundle.model_version,
        "fallback_level": "degraded" if model_degraded else (queue.fallback_level.value if hasattr(queue.fallback_level, "value") else str(queue.fallback_level)),
    }


# ------------------------------------------------------------------ #
# Recommendations refresh
# ------------------------------------------------------------------ #

@app.post("/recommendations/next")
def recommendations_next(payload: RecommendationRequest, request: Request) -> dict:
    auth_session = require_authenticated_session(request, repository)
    authenticated_payload = RecommendationRequest(
        session_id=payload.session_id,
        user_id=auth_session.user_id,
        request_id=payload.request_id,
        seed_track_ids=payload.seed_track_ids,
        queue_revision=payload.queue_revision,
    )
    response = recommendation_service.recommend_next(authenticated_payload)
    items = response.queue.items
    return {
        "session_id": payload.session_id,
        "queue": {
            "up_next": [_queue_item_dict(item) for item in items[:4]],
            "remaining": [_queue_item_dict(item) for item in items[4:]],
            "revision": response.queue.revision,
            "fallback_level": response.fallback_level.value if hasattr(response.fallback_level, "value") else str(response.fallback_level),
        },
        "model_version": serving_bundle.model_version,
    }


def _queue_item_dict(item) -> dict:
    return {
        "track_id": item.track_id,
        "title": item.title,
        "artist": item.artist,
        "album": getattr(item, "album", ""),
        "duration_sec": item.duration_sec,
        "cover_art_url": item.cover_art_url,
        "queue_position": item.queue_position,
        "request_id": item.request_id,
        "impression_id": item.impression_id,
    }


# ------------------------------------------------------------------ #
# Playback events — merged from event-api
# INSERT on playback_start; UPDATE (same event_id) on skip/complete
# ------------------------------------------------------------------ #

class PlaybackEventBody(BaseModel):
    playback_id: str                     # UUID generated by frontend, reused across start/skip/complete
    event_type: str                      # "playback_start" | "skip" | "complete"
    track_id: str
    session_id: str
    position: int = Field(0, ge=0)       # position in queue (0-based)
    playratio: Optional[float] = None    # NULL on playback_start, set on skip/complete
    position_ms: Optional[int] = None   # informational


@app.post("/events/playback")
def playback_event(payload: PlaybackEventBody, request: Request) -> dict:
    auth_session = require_authenticated_session(request, repository)

    # Idempotency: skip duplicate start events
    idem_key = f"idem:playback:{payload.playback_id}:{payload.event_type}"
    if runtime_state.get_string(idem_key):
        return {"status": "duplicate"}

    user = repository.get_user_by_id(auth_session.user_id)
    user_int_id: int = user["user_int_id"] if user else 0

    # Resolve session_int_id from rec_sessions via Redis (set at bootstrap)
    session_int_id_key = f"sess_int:{payload.session_id}"
    session_int_id_str = runtime_state.get_string(session_int_id_key)
    session_int_id = int(session_int_id_str) if session_int_id_str else 3000000

    if payload.event_type == "playback_start":
        repository.insert_playback_event(
            event_id=payload.playback_id,
            session_int_id=session_int_id,
            user_int_id=user_int_id,
            track_id=payload.track_id,
            position=payload.position,
            event_type="playback_start",
        )
        # Track in recent plays for C4 recency filter
        runtime_state.append_recent_track(payload.session_id, payload.track_id)
    else:
        # skip or complete — UPDATE existing row
        playratio = payload.playratio if payload.playratio is not None else 0.0
        repository.update_playback_event(payload.playback_id, payload.event_type, playratio)

    runtime_state.set_string(idem_key, "1", ttl_seconds=86400)
    return {"status": "ok", "event_type": payload.event_type}


# ------------------------------------------------------------------ #
# Stream proxy
# ------------------------------------------------------------------ #

@app.get("/playable-track/{track_id}")
def playable_track(track_id: str, request: Request) -> dict:
    require_authenticated_session(request, repository)
    try:
        return media_service.resolve_playable_track(track_id).dict()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/stream/{track_id}")
def stream(track_id: str, request: Request) -> Response:
    require_authenticated_session(request, repository)
    try:
        payload, media_type = media_service.stream_bytes(track_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(content=payload, media_type=media_type)


# ------------------------------------------------------------------ #
# Cover art — unique gradient SVG per track (no embedded art in MP3s)
# ------------------------------------------------------------------ #

@app.get("/covers/{track_id}")
def cover_art(track_id: str, request: Request) -> Response:
    require_authenticated_session(request, repository)
    track = repository.get_track(track_id)
    artist = track.artist if track else ""
    title = track.title if track else track_id
    h = int(hashlib.md5(f"{track_id}:{artist}".encode()).hexdigest(), 16)
    hue1 = h % 360
    hue2 = (hue1 + 137) % 360
    c1 = f"hsl({hue1},65%,32%)"
    c2 = f"hsl({hue2},55%,22%)"
    initial = (artist[:1] or "♪").upper()
    title_short = (title[:22] + "…") if len(title) > 22 else title
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300" viewBox="0 0 300 300">'
        f'<defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">'
        f'<stop offset="0%" stop-color="{c1}"/>'
        f'<stop offset="100%" stop-color="{c2}"/>'
        f'</linearGradient></defs>'
        f'<rect width="300" height="300" fill="url(#g)"/>'
        f'<text x="150" y="175" text-anchor="middle" font-family="Arial,sans-serif" '
        f'font-size="110" font-weight="700" fill="rgba(255,255,255,0.85)">{initial}</text>'
        f'<text x="150" y="268" text-anchor="middle" font-family="Arial,sans-serif" '
        f'font-size="18" fill="rgba(255,255,255,0.55)">{title_short}</text>'
        f'</svg>'
    )
    return Response(content=svg, media_type="image/svg+xml")


# ------------------------------------------------------------------ #
# Monitoring
# ------------------------------------------------------------------ #

@app.get("/monitoring/summary")
def monitoring_summary(request: Request) -> dict:
    require_authenticated_session(request, repository)
    summary = repository.get_monitoring_summary()
    summary["model_version"] = serving_bundle.model_version
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    return summary


# ------------------------------------------------------------------ #
# Admin — manual Airflow retrain trigger
# ------------------------------------------------------------------ #

@app.api_route("/admin/trigger-retrain", methods=["GET", "POST"])
def admin_trigger_retrain(request: Request) -> dict:
    require_authenticated_session(request, repository)
    try:
        dag_run = trigger_airflow_retrain()
        return {
            "status": "triggered",
            "dag_run_id": dag_run.get("dag_run_id"),
            "dag_state": dag_run.get("state"),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Airflow trigger failed: {exc}")


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

_model_degraded_cache: dict = {"value": False, "expires": 0.0}


def _check_model_degraded() -> bool:
    import time
    now = time.monotonic()
    if now < _model_degraded_cache["expires"]:
        return _model_degraded_cache["value"]
    try:
        status = repository.get_model_status()
        degraded = bool(status.get("degraded", False))
    except Exception:
        degraded = False
    _model_degraded_cache["value"] = degraded
    _model_degraded_cache["expires"] = now + 300  # 5 min TTL
    return degraded
