from __future__ import annotations

import argparse
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from src.features.online_features import build_online_feature_summary

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "online_feature_demo"
DEFAULT_RECOMMEND_URL = "http://localhost:8001/recommend"
DEFAULT_TOP_K = 5
DEFAULT_REQUEST = {
    "user_id": 43849,
    "session_id": 9771,
    "session_track_ids": [289120, 2168411, 2118750, 3353310, 2608312],
    "session_labels": ["positive", "positive", "positive", "positive", "neutral"],
    "seed_candidate_ids": [3349857, 3349899, 2595351, 1674973, 1507453, 3830146],
    "top_k": DEFAULT_TOP_K,
    "trigger_type": "online_feature_demo_default",
}

log = logging.getLogger("features.online_demo")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a single end-to-end online feature inference demo."
    )
    parser.add_argument("--recommend-url", default=DEFAULT_RECOMMEND_URL)
    parser.add_argument("--request-json", default=None)
    parser.add_argument("--generator-log", default=None)
    parser.add_argument("--sample-index", type=int, default=0)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    return parser.parse_args()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_output_dir(explicit: str | None, run_name: str | None) -> Path:
    root = Path(explicit) if explicit else DEFAULT_OUTPUT_ROOT
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    resolved_name = run_name or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = root / resolved_name
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def resolve_optional_path(explicit: str | None) -> Path | None:
    if not explicit:
        return None
    path = Path(explicit)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def latest_generator_log() -> Path | None:
    root = PROJECT_ROOT / "artifacts" / "generator"
    if not root.exists():
        return None

    candidates = sorted(
        root.glob("*/recommend_requests.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_generator_request(path: Path, sample_index: int) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Generator log not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]

    if not rows:
        raise ValueError(f"Generator log is empty: {path}")
    if sample_index < 0 or sample_index >= len(rows):
        raise IndexError(f"Sample index {sample_index} out of range for {len(rows)} rows")

    payload = rows[sample_index].get("request_payload")
    if not isinstance(payload, dict):
        raise ValueError(f"Missing request_payload at row {sample_index} in {path}")
    return payload


def load_request_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Request JSON not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Expected request JSON object")
    return payload


def normalize_request(payload: dict[str, Any], top_k: int) -> dict[str, Any]:
    normalized = {
        "request_id": payload.get("request_id") or str(uuid.uuid4()),
        "requested_at": payload.get("requested_at") or iso_now(),
        "trigger_type": payload.get("trigger_type") or "online_feature_demo",
        "seed_context_id": payload.get("seed_context_id"),
        "user_id": int(payload.get("user_id", 0)),
        "session_id": payload.get("session_id"),
        "session_track_ids": [int(x) for x in payload.get("session_track_ids", [])],
        "session_labels": [str(x) for x in payload.get("session_labels", [])],
        "seed_candidate_ids": [int(x) for x in payload.get("seed_candidate_ids", [])],
        "top_k": int(payload.get("top_k", top_k)),
    }
    return normalized


def build_request_preview(payload: dict[str, Any]) -> dict[str, Any]:
    return build_online_feature_summary(
        user_id=int(payload.get("user_id", 0)),
        session_track_ids=[int(x) for x in payload.get("session_track_ids", [])],
        session_labels=[str(x) for x in payload.get("session_labels", [])],
        seed_candidate_ids=[int(x) for x in payload.get("seed_candidate_ids", [])],
        candidate_pool_ids=[int(x) for x in payload.get("seed_candidate_ids", [])],
        candidate_source="request_seed",
        retriever_enabled=False,
        ranker_enabled=False,
        ranker_used=False,
    )


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    output_dir = resolve_output_dir(args.output_dir, args.run_name)
    explicit_request = resolve_optional_path(args.request_json)
    explicit_generator_log = resolve_optional_path(args.generator_log)

    request_source = "built_in_default"
    if explicit_request is not None:
        raw_payload = load_request_json(explicit_request)
        request_source = str(explicit_request)
    else:
        generator_log = explicit_generator_log or latest_generator_log()
        if generator_log is not None:
            raw_payload = load_generator_request(generator_log, args.sample_index)
            request_source = str(generator_log)
        else:
            raw_payload = DEFAULT_REQUEST

    request_payload = normalize_request(raw_payload, top_k=args.top_k)
    request_preview = build_request_preview(request_payload)

    log.info("Using request source: %s", request_source)
    log.info(
        "Running online feature demo for user=%s session=%s prefix_len=%s",
        request_payload["user_id"],
        request_payload["session_id"],
        len(request_payload["session_track_ids"]),
    )

    response = requests.post(
        args.recommend_url,
        json=request_payload,
        timeout=args.timeout_seconds,
    )
    response.raise_for_status()
    response_json = response.json()

    feature_summary = {
        "request_preview": request_preview,
        "response_online_features": response_json.get("online_features", {}),
        "candidate_pool_size": len(response_json.get("candidate_pool_ids", [])),
        "top5_ids": response_json.get("top5_ids", []),
        "top5_scores": response_json.get("top5_scores", []),
    }
    demo_summary = {
        "request_source": request_source,
        "recommend_url": args.recommend_url,
        "request_id": request_payload["request_id"],
        "output_dir": str(output_dir),
        "request_path": str(output_dir / "request.json"),
        "response_path": str(output_dir / "response.json"),
        "feature_summary_path": str(output_dir / "feature_summary.json"),
    }

    (output_dir / "request.json").write_text(
        json.dumps(request_payload, indent=2),
        encoding="utf-8",
    )
    (output_dir / "response.json").write_text(
        json.dumps(response_json, indent=2),
        encoding="utf-8",
    )
    (output_dir / "feature_summary.json").write_text(
        json.dumps(feature_summary, indent=2),
        encoding="utf-8",
    )
    (output_dir / "demo_summary.json").write_text(
        json.dumps(demo_summary, indent=2),
        encoding="utf-8",
    )

    log.info("Online feature demo complete.")
    log.info("Request saved to %s", output_dir / "request.json")
    log.info("Response saved to %s", output_dir / "response.json")
    log.info("Feature summary saved to %s", output_dir / "feature_summary.json")


if __name__ == "__main__":
    main()
