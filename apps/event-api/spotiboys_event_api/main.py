from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from packages.auth import require_authenticated_session
from packages.config import load_config
from packages.db_access.factory import build_repository_and_runtime
from packages.ingestion_quality import (
    IngestionRejection,
    validate_feedback,
    validate_impression,
    validate_playback,
)
from packages.shared_contracts.schemas import (
    EventAck,
    FeedbackEventRequest,
    ImpressionEventRequest,
    PlaybackEventRequest,
)
from packages.shared_contracts.enums import PlaybackEventType

logger = logging.getLogger(__name__)

config = load_config()
repository, runtime_state = build_repository_and_runtime(config)

app = FastAPI(title="SpotiBoys Event API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "event-api",
        "runtime_state": config.runtime_mode,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/events/impression", response_model=EventAck)
def impression_event(payload: ImpressionEventRequest, request: Request) -> EventAck:
    auth_session = require_authenticated_session(request, repository)
    authenticated_payload = payload.copy(
        update={"session_id": auth_session.session_id, "user_id": auth_session.user_id}
    )
    try:
        validate_impression(authenticated_payload.dict())
    except IngestionRejection as exc:
        logger.warning("impression rejected: %s", exc)
        repository.persist_ingestion_rejection(exc.event_type, exc.reasons, exc.raw_payload)
        raise HTTPException(status_code=422, detail={"rejection_reasons": exc.reasons})

    accepted = runtime_state.remember_once("idem:impression", authenticated_payload.impression_id)
    if accepted:
        repository.persist_rendered_impression(authenticated_payload.impression_id, authenticated_payload.dict())
    return EventAck(status="ok", duplicate=not accepted, impression_id=authenticated_payload.impression_id)


@app.post("/events/playback", response_model=EventAck)
def playback_event(payload: PlaybackEventRequest, request: Request) -> EventAck:
    auth_session = require_authenticated_session(request, repository)
    authenticated_payload = payload.copy(
        update={"session_id": auth_session.session_id, "user_id": auth_session.user_id}
    )
    try:
        validate_playback(authenticated_payload.dict())
    except IngestionRejection as exc:
        logger.warning("playback event rejected: %s", exc)
        repository.persist_ingestion_rejection(exc.event_type, exc.reasons, exc.raw_payload)
        raise HTTPException(status_code=422, detail={"rejection_reasons": exc.reasons})

    accepted = runtime_state.remember_once("idem:playback", authenticated_payload.event_id)
    if accepted:
        repository.persist_playback_event(authenticated_payload.event_id, authenticated_payload.dict())
        if authenticated_payload.event_type in {PlaybackEventType.PLAYBACK_START, PlaybackEventType.COMPLETE}:
            runtime_state.append_recent_track(authenticated_payload.session_id, authenticated_payload.track_id)
    return EventAck(status="ok", duplicate=not accepted, event_id=authenticated_payload.event_id)


@app.post("/events/feedback", response_model=EventAck)
def feedback_event(payload: FeedbackEventRequest, request: Request) -> EventAck:
    auth_session = require_authenticated_session(request, repository)
    authenticated_payload = payload.copy(
        update={"session_id": auth_session.session_id, "user_id": auth_session.user_id}
    )
    try:
        validate_feedback(authenticated_payload.dict())
    except IngestionRejection as exc:
        logger.warning("feedback event rejected: %s", exc)
        repository.persist_ingestion_rejection(exc.event_type, exc.reasons, exc.raw_payload)
        raise HTTPException(status_code=422, detail={"rejection_reasons": exc.reasons})

    accepted = runtime_state.remember_once("idem:feedback", authenticated_payload.event_id)
    if accepted:
        repository.persist_feedback_event(authenticated_payload.event_id, authenticated_payload.dict())
    return EventAck(status="ok", duplicate=not accepted, event_id=authenticated_payload.event_id)
