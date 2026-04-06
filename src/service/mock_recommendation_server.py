from __future__ import annotations

import argparse
import json
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from src.ranker.ranker import GRURankerInference
from src.retriever.retriever import MultiRecallRetriever

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "mock_service"

log = logging.getLogger("service.mock")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a minimal mock recommendation service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--default-top-k", type=int, default=5)
    parser.add_argument("--enable-retriever", action="store_true")
    parser.add_argument("--enable-ranker", action="store_true")
    parser.add_argument("--disable-retriever", action="store_true")
    parser.add_argument("--disable-ranker", action="store_true")
    return parser.parse_args()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_jsonl(path: Path, payload: dict[str, Any], lock: threading.Lock) -> None:
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


@dataclass
class ServiceArtifacts:
    retriever: MultiRecallRetriever | None
    ranker: GRURankerInference | None


class RecommendationService:
    def __init__(
        self,
        output_dir: Path,
        default_top_k: int,
        use_retriever: bool,
        use_ranker: bool,
    ) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.default_top_k = default_top_k
        self.log_lock = threading.Lock()

        self.recommend_path = self.output_dir / "recommend_logs.jsonl"
        self.impression_path = self.output_dir / "impression_logs.jsonl"
        self.outcome_path = self.output_dir / "outcome_logs.jsonl"

        retriever = None
        if use_retriever:
            try:
                retriever = MultiRecallRetriever()
                log.info("Retriever loaded for /recommend.")
            except Exception as exc:
                log.warning("Retriever unavailable; falling back to seed candidates only: %s", exc)
        else:
            log.info("Retriever disabled; /recommend will use seed candidates only.")

        ranker = None
        if use_ranker:
            try:
                ranker = GRURankerInference()
                log.info("Ranker loaded for /recommend.")
            except Exception as exc:
                log.warning("Ranker unavailable; returning candidate order without reranking: %s", exc)
        else:
            log.info("Ranker disabled; /recommend will not rerank candidates.")

        self.artifacts = ServiceArtifacts(retriever=retriever, ranker=ranker)

    def handle_recommend(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        request_id = payload.get("request_id") or str(uuid.uuid4())
        user_id = int(payload.get("user_id", 0))
        session_id = payload.get("session_id")
        session_track_ids = [int(x) for x in payload.get("session_track_ids", [])]
        session_labels = [str(x) for x in payload.get("session_labels", [])]
        top_k = int(payload.get("top_k", self.default_top_k))
        seed_candidate_ids = [int(x) for x in payload.get("seed_candidate_ids", [])]

        candidate_source = "seed_candidates"
        candidate_pool_ids = seed_candidate_ids
        candidate_pool_scores: list[float] | None = None

        if not candidate_pool_ids and self.artifacts.retriever is not None:
            retrieved = self.artifacts.retriever.retrieve(user_id, session_track_ids, session_labels)
            candidate_pool_ids = [track_id for track_id, _ in retrieved]
            candidate_pool_scores = [float(score) for _, score in retrieved]
            candidate_source = "retriever"

        ranked_candidates: list[tuple[int, float]]
        if self.artifacts.ranker is not None and candidate_pool_ids:
            ranked_candidates = self.artifacts.ranker.score(
                user_id=user_id,
                session_track_ids=session_track_ids,
                session_labels=session_labels,
                candidates=candidate_pool_ids,
            )
            ranker_used = True
        else:
            if candidate_pool_scores is None:
                candidate_pool_scores = [1.0 - (idx * 0.01) for idx in range(len(candidate_pool_ids))]
            ranked_candidates = list(zip(candidate_pool_ids, candidate_pool_scores))
            ranker_used = False

        top_ranked = ranked_candidates[:top_k]
        response = {
            "request_id": request_id,
            "session_id": session_id,
            "user_id": user_id,
            "candidate_pool_ids": [track_id for track_id, _ in ranked_candidates],
            "candidate_scores": [round(float(score), 6) for _, score in ranked_candidates],
            "top5_ids": [track_id for track_id, _ in top_ranked],
            "top5_scores": [round(float(score), 6) for _, score in top_ranked],
            "online_features": {
                "prefix_len": len(session_track_ids),
                "recent_session_tracks": session_track_ids[-5:],
                "num_seed_candidates": len(seed_candidate_ids),
                "candidate_source": candidate_source,
                "ranker_used": ranker_used,
                "retriever_enabled": self.artifacts.retriever is not None,
                "ranker_enabled": self.artifacts.ranker is not None,
            },
            "created_at": iso_now(),
        }

        write_jsonl(
            self.recommend_path,
            {"request": payload, "response": response},
            self.log_lock,
        )
        return response, HTTPStatus.OK

    def handle_impression(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        payload = payload | {"received_at": iso_now()}
        write_jsonl(self.impression_path, payload, self.log_lock)
        return {"status": "ok", "request_id": payload.get("request_id")}, HTTPStatus.OK

    def handle_outcome(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        payload = payload | {"received_at": iso_now()}
        write_jsonl(self.outcome_path, payload, self.log_lock)
        return {"status": "ok", "request_id": payload.get("request_id")}, HTTPStatus.OK


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "SpotyBoysMock/0.1"

    def do_POST(self) -> None:  # noqa: N802
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            payload = json.loads(raw_body.decode("utf-8") or "{}")
        except Exception:
            self._send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
            return

        service: RecommendationService = self.server.app  # type: ignore[attr-defined]

        if self.path == "/recommend":
            body, status = service.handle_recommend(payload)
        elif self.path == "/impression":
            body, status = service.handle_impression(payload)
        elif self.path == "/outcome":
            body, status = service.handle_outcome(payload)
        else:
            body, status = {"error": "not_found"}, HTTPStatus.NOT_FOUND

        self._send_json(body, status)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json({"status": "ok", "time": iso_now()}, HTTPStatus.OK)
            return
        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        log.info("%s - %s", self.address_string(), format % args)

    def _send_json(self, payload: dict[str, Any], status: int) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    use_retriever = args.enable_retriever and not args.disable_retriever
    use_ranker = args.enable_ranker and not args.disable_ranker

    service = RecommendationService(
        output_dir=Path(args.output_dir),
        default_top_k=args.default_top_k,
        use_retriever=use_retriever,
        use_ranker=use_ranker,
    )
    httpd = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    httpd.app = service  # type: ignore[attr-defined]

    log.info("Mock service listening on http://%s:%s", args.host, args.port)
    log.info("Health check: http://%s:%s/health", args.host, args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down mock service.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
