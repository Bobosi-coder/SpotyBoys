from __future__ import annotations

import json
import mimetypes
import os
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.db_access.demo_bootstrap import get_demo_components  # noqa: E402
from packages.navidrome_adapter import MediaAccessService  # noqa: E402
from packages.recommendation_engine import RecommendationService  # noqa: E402
from packages.shared_contracts.schemas import (  # noqa: E402
    BootstrapResponse,
    DegradedState,
    FeedbackEventRequest,
    ImpressionEventRequest,
    QueueState,
    RecommendationRequest,
    PlaybackEventRequest,
)

FRONTEND_ROOT = PROJECT_ROOT / "apps" / "frontend-web"
SESSION_ID = "sess_demo"
USER_ID = "user_demo"

repository, runtime_state = get_demo_components()
recommendation_service = RecommendationService(repository, runtime_state)
media_service = MediaAccessService(repository)


class DemoGatewayHandler(BaseHTTPRequestHandler):
    server_version = "SpotiBoysDemoGateway/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self.send_json({"status": "ok", "service": "demo-gateway", "time": now_iso()})
        elif path == "/ready":
            self.send_json({"status": "ready", "playable_tracks": len(repository.list_playable_tracks())})
        elif path == "/session/bootstrap":
            self.send_json(build_bootstrap().dict())
        elif path.startswith("/playable-track/"):
            self.handle_playable(path.rsplit("/", 1)[-1])
        elif path.startswith("/stream/"):
            self.handle_stream(path.rsplit("/", 1)[-1])
        elif path == "/" or path.startswith("/src/"):
            self.serve_frontend(path)
        else:
            self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = json.loads(self.read_body() or "{}")
        except json.JSONDecodeError:
            self.send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
            return

        if self.path == "/recommendations/next":
            response = recommendation_service.recommend_next(RecommendationRequest(**payload))
            self.send_json(response.dict())
        elif self.path == "/events/impression":
            request = ImpressionEventRequest(**payload)
            accepted = runtime_state.remember_once("idem:impression", request.impression_id)
            if accepted:
                repository.persist_rendered_impression(request.impression_id, request.dict())
            self.send_json({"status": "ok", "duplicate": not accepted, "impression_id": request.impression_id})
        elif self.path == "/events/playback":
            request = PlaybackEventRequest(**payload)
            accepted = runtime_state.remember_once("idem:playback", request.event_id)
            if accepted:
                repository.persist_playback_event(request.event_id, request.dict())
            self.send_json({"status": "ok", "duplicate": not accepted, "event_id": request.event_id})
        elif self.path == "/events/feedback":
            request = FeedbackEventRequest(**payload)
            accepted = runtime_state.remember_once("idem:feedback", request.event_id)
            if accepted:
                repository.persist_feedback_event(request.event_id, request.dict())
            self.send_json({"status": "ok", "duplicate": not accepted, "event_id": request.event_id})
        else:
            self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.end_headers()

    def handle_playable(self, track_id: str) -> None:
        try:
            self.send_json(media_service.resolve_playable_track(track_id).dict())
        except LookupError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)

    def handle_stream(self, track_id: str) -> None:
        try:
            payload, media_type = media_service.stream_bytes(track_id)
        except LookupError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_cors_headers()
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def serve_frontend(self, path: str) -> None:
        file_path = FRONTEND_ROOT / ("index.html" if path == "/" else path.lstrip("/"))
        if not file_path.is_file() or FRONTEND_ROOT not in file_path.resolve().parents:
            self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        payload = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_cors_headers()
        self.send_header("Content-Type", mimetypes.guess_type(str(file_path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_body(self) -> str:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length).decode("utf-8") if length else ""

    def send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(to_jsonable(payload), ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[demo-gateway] " + fmt % args + "\n")


def build_bootstrap() -> BootstrapResponse:
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


def to_jsonable(value: object) -> object:
    if hasattr(value, "dict"):
        return to_jsonable(value.dict())  # type: ignore[no-any-return]
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def main() -> None:
    host = os.environ.get("DEMO_HOST", "127.0.0.1")
    port = int(os.environ.get("DEMO_PORT", "5173"))
    server = ThreadingHTTPServer((host, port), DemoGatewayHandler)
    print(f"[demo-gateway] listening on http://{host}:{port}/?gateway=1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
