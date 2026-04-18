from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from packages.artifact_runtime import ServingBundle
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
serving_bundle = ServingBundle.load(config.serving_bundle_path)
repository.register_active_model_version(
    serving_bundle.model_version,
    serving_bundle.version,
    str(config.serving_bundle_path / "manifest.json"),
)
recommendation_service = RecommendationService(repository, runtime_state, serving_bundle)
media_service = MediaAccessService(repository, config=config)

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
        "model_version": serving_bundle.model_version,
        "serving_bundle_version": serving_bundle.version,
        "media_mode": config.media_mode,
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


@app.get("/covers/{track_id}")
def cover_art(track_id: str) -> Response:
    track = repository.get_track(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="track not found")
    initials = "".join(part[:1] for part in track.title.split()[:2]).upper() or "SB"
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600" viewBox="0 0 600 600">
  <rect width="600" height="600" fill="#141414"/>
  <circle cx="470" cy="128" r="96" fill="#1db954" opacity="0.86"/>
  <circle cx="112" cy="468" r="132" fill="#7dd3fc" opacity="0.42"/>
  <text x="48" y="332" font-family="Arial, sans-serif" font-size="138" font-weight="700" fill="#f8fafc">{initials}</text>
  <text x="52" y="390" font-family="Arial, sans-serif" font-size="30" fill="#d1d5db">{track.artist}</text>
</svg>"""
    return Response(content=svg, media_type="image/svg+xml")
