from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from packages.airflow_trigger import trigger_airflow_retrain
from packages.artifact_runtime import ServingBundle
from packages.auth import (
    clear_session_cookie,
    hash_password,
    hash_session_token,
    new_session_id,
    new_session_token,
    new_user_id,
    require_authenticated_session,
    session_expires_at,
    set_session_cookie,
    verify_password,
)
from packages.config import load_config
from packages.db_access.factory import build_repository_and_runtime
from packages.navidrome_adapter import MediaAccessService
from packages.recommendation_engine import RecommendationService
from packages.shared_contracts.schemas import (
    AuthResponse,
    AuthUser,
    BootstrapResponse,
    DegradedState,
    LoginRequest,
    LogoutResponse,
    QueueState,
    RecommendationRequest,
    SignupRequest,
)

config = load_config()

repository, runtime_state = build_repository_and_runtime(config)
serving_bundle = ServingBundle.load(config.serving_bundle_path)
repository.register_active_model_version(
    serving_bundle.model_version,
    serving_bundle.version,
    str(config.serving_bundle_path / "manifest.json"),
)
recommendation_service = RecommendationService(
    repository,
    runtime_state,
    serving_bundle,
    require_full_ml_pipeline=config.require_full_ml_pipeline,
)
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
        "full_ml_pipeline_required": config.require_full_ml_pipeline,
        "pipeline_trace": recommendation_service.last_pipeline_trace.dict()
        if hasattr(recommendation_service.last_pipeline_trace, "dict")
        else recommendation_service.last_pipeline_trace.__dict__
        if recommendation_service.last_pipeline_trace
        else None,
    }


def _record_metric(
    *,
    endpoint: str,
    started_at: float,
    status: str,
    status_code: int,
    request_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    fallback_level: str | None = None,
    candidate_count: int | None = None,
    final_count: int | None = None,
    playable_drop_count: int | None = None,
    error_type: str | None = None,
    metrics: dict | None = None,
) -> None:
    try:
        repository.record_serving_request_metric(
            {
                "metric_id": f"metric_{uuid.uuid4().hex}",
                "endpoint": endpoint,
                "request_id": request_id,
                "session_id": session_id,
                "user_id": user_id,
                "model_version": serving_bundle.model_version,
                "serving_bundle_version": serving_bundle.version,
                "status": status,
                "status_code": status_code,
                "latency_ms": round((time.monotonic() - started_at) * 1000, 3),
                "candidate_count": candidate_count,
                "final_count": final_count,
                "playable_drop_count": playable_drop_count,
                "fallback_level": fallback_level,
                "error_type": error_type,
                "metrics": metrics or {},
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception:
        # Monitoring must never break playback or recommendation serving.
        return


def _auth_response(auth_session) -> AuthResponse:
    return AuthResponse(
        user=AuthUser(
            user_id=auth_session.user_id,
            email=auth_session.email,
            display_name=auth_session.display_name,
        ),
        session_id=auth_session.session_id,
    )


@app.post("/auth/signup", response_model=AuthResponse)
def signup(payload: SignupRequest, response: Response) -> AuthResponse:
    existing = repository.get_user_by_email(payload.email)
    if existing:
        raise HTTPException(status_code=409, detail="email already registered")
    user_id = new_user_id()
    repository.create_user(user_id, payload.email, hash_password(payload.password), payload.display_name)
    token = new_session_token()
    expires_at = session_expires_at()
    auth_session = repository.create_auth_session(
        token_hash=hash_session_token(token),
        user_id=user_id,
        session_id=new_session_id(),
        expires_at=expires_at,
    )
    set_session_cookie(response, token, expires_at)
    return _auth_response(auth_session)


@app.post("/auth/login", response_model=AuthResponse)
def login(payload: LoginRequest, response: Response) -> AuthResponse:
    user = repository.get_user_by_email(payload.email)
    if not user or not user.get("password_hash") or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid email or password")
    token = new_session_token()
    expires_at = session_expires_at()
    auth_session = repository.create_auth_session(
        token_hash=hash_session_token(token),
        user_id=user["user_id"],
        session_id=new_session_id(),
        expires_at=expires_at,
    )
    set_session_cookie(response, token, expires_at)
    return _auth_response(auth_session)


@app.post("/auth/logout", response_model=LogoutResponse)
def logout(request: Request, response: Response) -> LogoutResponse:
    token = request.cookies.get("spotiboys_session")
    if token:
        repository.revoke_auth_session(hash_session_token(token))
    clear_session_cookie(response)
    return LogoutResponse()


@app.get("/auth/me", response_model=AuthResponse)
def auth_me(request: Request) -> AuthResponse:
    return _auth_response(require_authenticated_session(request, repository))


@app.get("/session/bootstrap", response_model=BootstrapResponse)
def bootstrap(request: Request) -> BootstrapResponse:
    started_at = time.monotonic()
    auth_session = None
    auth_session = require_authenticated_session(request, repository)
    request_id = f"req_bootstrap_{auth_session.session_id}"
    try:
        queue = runtime_state.get_queue(auth_session.session_id)
        if not queue.items:
            browse_surface, queue_items = recommendation_service.build_bootstrap_surfaces(
                auth_session.session_id,
                auth_session.user_id,
            )
            queue = runtime_state.set_queue(auth_session.session_id, queue_items)
        else:
            browse_surface, _ = recommendation_service.build_bootstrap_surfaces(auth_session.session_id, auth_session.user_id)
        repository.persist_recommendation_impression(
            f"imp_bootstrap_{auth_session.session_id}",
            {
                "request_id": request_id,
                "session_id": auth_session.session_id,
                "user_id": auth_session.user_id,
                "model_version": recommendation_service.model_version,
                "fallback_level": "none",
                "browse_surface": browse_surface.dict(),
                "queue": {"items": [item.dict() for item in queue.items], "revision": queue.revision},
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        _record_metric(
            endpoint="recommendations.bootstrap",
            started_at=started_at,
            status="success",
            status_code=200,
            request_id=request_id,
            session_id=auth_session.session_id,
            user_id=auth_session.user_id,
            fallback_level=queue.fallback_level,
            candidate_count=recommendation_service.last_pipeline_trace.c2_candidate_count
            if recommendation_service.last_pipeline_trace
            else None,
            final_count=len(queue.items),
            metrics={
                "pipeline_trace": recommendation_service.last_pipeline_trace.__dict__
                if recommendation_service.last_pipeline_trace
                else None
            },
        )
        return BootstrapResponse(
            session_id=auth_session.session_id,
            user_id=auth_session.user_id,
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
    except Exception as exc:
        _record_metric(
            endpoint="recommendations.bootstrap",
            started_at=started_at,
            status="error",
            status_code=getattr(exc, "status_code", 500),
            request_id=request_id,
            session_id=auth_session.session_id if auth_session else None,
            user_id=auth_session.user_id if auth_session else None,
            error_type=type(exc).__name__,
        )
        raise


@app.post("/recommendations/next")
def recommendations_next(payload: RecommendationRequest, request: Request) -> dict:
    started_at = time.monotonic()
    auth_session = None
    auth_session = require_authenticated_session(request, repository)
    authenticated_payload = RecommendationRequest(
        session_id=auth_session.session_id,
        user_id=auth_session.user_id,
        request_id=payload.request_id,
        seed_track_ids=payload.seed_track_ids,
        queue_revision=payload.queue_revision,
    )
    try:
        response = recommendation_service.recommend_next(authenticated_payload)
        trace = recommendation_service.last_pipeline_trace
        _record_metric(
            endpoint="recommendations.next",
            started_at=started_at,
            status="success",
            status_code=200,
            request_id=response.request_id,
            session_id=auth_session.session_id,
            user_id=auth_session.user_id,
            fallback_level=response.fallback_level.value,
            candidate_count=trace.c2_candidate_count if trace else None,
            final_count=len(response.queue.items),
            metrics={"pipeline_trace": trace.__dict__ if trace else None},
        )
        return response.dict()
    except Exception as exc:
        _record_metric(
            endpoint="recommendations.next",
            started_at=started_at,
            status="error",
            status_code=getattr(exc, "status_code", 500),
            request_id=authenticated_payload.request_id,
            session_id=auth_session.session_id if auth_session else None,
            user_id=auth_session.user_id if auth_session else None,
            error_type=type(exc).__name__,
        )
        raise


@app.get("/playable-track/{track_id}")
def playable_track(track_id: str, request: Request) -> dict:
    require_authenticated_session(request, repository)
    try:
        return media_service.resolve_playable_track(track_id).dict()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/stream/{track_id}")
def stream(track_id: str, request: Request) -> Response:
    started_at = time.monotonic()
    auth_session = require_authenticated_session(request, repository)
    try:
        payload, media_type = media_service.stream_bytes(track_id)
    except LookupError as exc:
        _record_metric(
            endpoint="stream.proxy",
            started_at=started_at,
            status="error",
            status_code=404,
            session_id=auth_session.session_id,
            user_id=auth_session.user_id,
            error_type=type(exc).__name__,
            metrics={"track_id": track_id},
        )
        raise HTTPException(status_code=404, detail=str(exc))
    _record_metric(
        endpoint="stream.proxy",
        started_at=started_at,
        status="success",
        status_code=200,
        session_id=auth_session.session_id,
        user_id=auth_session.user_id,
        metrics={"track_id": track_id, "media_type": media_type, "byte_count": len(payload)},
    )
    return Response(content=payload, media_type=media_type)


@app.get("/monitoring/summary")
def monitoring_summary(request: Request) -> dict:
    require_authenticated_session(request, repository)
    return {
        "status": "ok",
        "active_model": repository.get_active_model_version(),
        "serving_bundle_version": serving_bundle.version,
        "model_version": serving_bundle.model_version,
        "latest_5m_rollup": repository.latest_serving_metric_rollup("5m"),
        "latest_1h_rollup": repository.latest_serving_metric_rollup("1h"),
        "latest_promotion_decision": repository.latest_model_trigger_decision("promotion"),
        "latest_rollback_decision": repository.latest_model_trigger_decision("rollback"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/admin/data-quality")
def admin_data_quality() -> dict:
    """Return the latest live data quality report (for demo/monitoring)."""
    import json
    reports_dir = config.object_storage_root / "quality_reports" / "live"
    reports = sorted(reports_dir.glob("*.json")) if reports_dir.exists() else []
    if not reports:
        return {"status": "no_reports", "message": "No live quality reports found yet."}
    return json.loads(reports[-1].read_text(encoding="utf-8"))


@app.api_route("/admin/trigger-retrain", methods=["GET", "POST"])
def admin_trigger_retrain() -> dict:
    """Force delta export + Airflow retraining DAG trigger (demo/manual use)."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "export_delta",
        Path(__file__).parent.parent.parent.parent
        / "workers" / "parser-export-worker" / "export_delta.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    export_path = str(mod.export_delta())

    dag_run = trigger_airflow_retrain()
    return {
        "status": "triggered",
        "export_path": export_path,
        "dag_run_id": dag_run.get("dag_run_id"),
        "dag_state": dag_run.get("state"),
    }


@app.get("/covers/{track_id}")
def cover_art(track_id: str, request: Request) -> Response:
    require_authenticated_session(request, repository)
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
