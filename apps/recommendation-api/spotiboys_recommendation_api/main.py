from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from packages.config import load_config
from packages.db_access.factory import build_repository_and_runtime
from packages.navidrome_adapter import MediaAccessService
from packages.recommendation_engine import RecommendationService
from packages.shared_contracts.schemas import (
    BootstrapResponse,
    DegradedState,
    QueueState,
    RecommendationRequest,
)

config = load_config()
SESSION_ID = config.session_id
USER_ID = config.user_id

repository, runtime_state = build_repository_and_runtime(config)
recommendation_service = RecommendationService(repository, runtime_state)
media_service = MediaAccessService(repository)

app = FastAPI(title="SpotiBoys Recommendation API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "recommendation-api", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/ready")
def ready() -> dict:
    return {
        "status": "ready",
        "playable_tracks": len(repository.list_playable_tracks()),
        "runtime_state": config.runtime_mode,
    }


@app.get("/session/bootstrap", response_model=BootstrapResponse)
def bootstrap() -> BootstrapResponse:
    queue = runtime_state.get_queue(SESSION_ID)
    if not queue.items:
        browse_surface, queue_items = recommendation_service.build_bootstrap_surfaces(SESSION_ID, USER_ID)
        queue = runtime_state.set_queue(SESSION_ID, queue_items)
    else:
        browse_surface, _ = recommendation_service.build_bootstrap_surfaces(SESSION_ID, USER_ID)
    return BootstrapResponse(
        session_id=SESSION_ID,
        user_id=USER_ID,
        auth_state="authenticated",
        browse_surface=browse_surface,
        queue=QueueState(
            items=queue.items,
            fallback_level=queue.fallback_level,
            generated_at=queue.generated_at,
            drawer_default_open=False,
            revision=queue.revision,
        ),
        current_track=None,
        degraded=DegradedState(logging=False, recommendations=False),
    )


@app.post("/recommendations/next")
def recommendations_next(request: RecommendationRequest) -> dict:
    return recommendation_service.recommend_next(request).dict()


@app.get("/playable-track/{track_id}")
def playable_track(track_id: str) -> dict:
    try:
        return media_service.resolve_playable_track(track_id).dict()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/stream/{track_id}")
def stream(track_id: str) -> Response:
    try:
        payload, media_type = media_service.stream_bytes(track_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(content=payload, media_type=media_type)
