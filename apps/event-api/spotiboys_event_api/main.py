from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from packages.config import load_config
from packages.db_access.factory import build_repository_and_runtime
from packages.shared_contracts.schemas import (
    EventAck,
    FeedbackEventRequest,
    ImpressionEventRequest,
    PlaybackEventRequest,
)

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
def impression_event(request: ImpressionEventRequest) -> EventAck:
    accepted = runtime_state.remember_once("idem:impression", request.impression_id)
    if accepted:
        repository.persist_rendered_impression(request.impression_id, request.dict())
    return EventAck(status="ok", duplicate=not accepted, impression_id=request.impression_id)


@app.post("/events/playback", response_model=EventAck)
def playback_event(request: PlaybackEventRequest) -> EventAck:
    accepted = runtime_state.remember_once("idem:playback", request.event_id)
    if accepted:
        repository.persist_playback_event(request.event_id, request.dict())
    return EventAck(status="ok", duplicate=not accepted, event_id=request.event_id)


@app.post("/events/feedback", response_model=EventAck)
def feedback_event(request: FeedbackEventRequest) -> EventAck:
    accepted = runtime_state.remember_once("idem:feedback", request.event_id)
    if accepted:
        repository.persist_feedback_event(request.event_id, request.dict())
    return EventAck(status="ok", duplicate=not accepted, event_id=request.event_id)
