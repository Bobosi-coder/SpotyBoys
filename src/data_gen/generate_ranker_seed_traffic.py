from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "generator"
DEFAULT_PARQUET_CANDIDATES = (
    PROJECT_ROOT / "ranker_val.parquet",
    PROJECT_ROOT / "artifacts" / "ranker" / "ranker_val.parquet",
)

LABEL_DEC = {0: "positive", 1: "neutral", 2: "skip", 3: "pad"}
Y_TO_LABEL = {1.0: "positive", 0.5: "neutral", 0.0: "skip"}
PLAY_RATIO_BY_LABEL = {"positive": 1.0, "neutral": 0.5, "skip": 0.0}

log = logging.getLogger("data_gen.ranker_seed")


@dataclass
class SeedContext:
    context_id: int
    session_id: int
    user_id: int
    session_track_ids: list[int]
    session_labels: list[str]
    candidate_ids: list[int]
    positive_candidate_id: int
    expected_label: str
    expected_y: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate production-like recommendation traffic from ranker validation data."
    )
    parser.add_argument("--parquet-path", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--max-requests", type=int, default=25)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--sample-mode", choices=["random", "sequential"], default="random")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--recommend-url", default=None)
    parser.add_argument("--impression-url", default=None)
    parser.add_argument("--outcome-url", default=None)
    parser.add_argument("--model-version", default="generator-seed-demo")
    parser.add_argument("--trigger-type", default="generator_ranker_val_seed")
    parser.add_argument("--print-samples", type=int, default=3)
    return parser.parse_args()


def resolve_parquet_path(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            raise FileNotFoundError(f"Parquet file not found: {path}")
        return path

    for candidate in DEFAULT_PARQUET_CANDIDATES:
        if candidate.exists():
            return candidate

    searched = "\n".join(f"- {path}" for path in DEFAULT_PARQUET_CANDIDATES)
    raise FileNotFoundError(
        "No ranker validation parquet found. Looked in:\n" + searched
    )


def resolve_output_dir(explicit: str | None, run_name: str | None) -> Path:
    root = Path(explicit) if explicit else DEFAULT_OUTPUT_ROOT
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    resolved_name = run_name or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = root / resolved_name
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def decode_prefix(prefix_ids: list[Any], prefix_labels: list[Any], prefix_len: int) -> tuple[list[int], list[str]]:
    plen = max(0, min(int(prefix_len), len(prefix_ids)))
    if plen == 0:
        return [], []

    session_track_ids = [int(track_id) for track_id in prefix_ids[-plen:]]
    session_labels = [
        LABEL_DEC.get(int(label_id), "unknown")
        for label_id in prefix_labels[-plen:]
    ]
    session_labels = ["unknown" if label == "pad" else label for label in session_labels]
    return session_track_ids, session_labels


def decode_expected_label(y_value: float) -> str:
    rounded = round(float(y_value), 1)
    return Y_TO_LABEL.get(rounded, "unknown")


def load_seed_contexts(parquet_path: Path) -> list[SeedContext]:
    df = pd.read_parquet(
        parquet_path,
        columns=[
            "context_id",
            "session_id",
            "user_id",
            "prefix_ids",
            "prefix_labels",
            "prefix_len",
            "candidate_id",
            "y",
            "is_positive",
        ],
    )
    n_rows = len(df)
    if n_rows % 6 != 0:
        raise ValueError(f"Expected rows divisible by 6, got {n_rows}")

    contexts: list[SeedContext] = []
    for row0 in range(0, n_rows, 6):
        shared = df.iloc[row0]
        chunk = df.iloc[row0 : row0 + 6]

        session_track_ids, session_labels = decode_prefix(
            shared["prefix_ids"],
            shared["prefix_labels"],
            int(shared["prefix_len"]),
        )
        candidate_ids = [int(value) for value in chunk["candidate_id"].tolist()]

        positive_rows = chunk[chunk["is_positive"] == True]
        if len(positive_rows) != 1:
            raise ValueError(
                f"Expected exactly one positive row for context_id={int(shared['context_id'])}"
            )
        positive_row = positive_rows.iloc[0]

        contexts.append(
            SeedContext(
                context_id=int(shared["context_id"]),
                session_id=int(shared["session_id"]),
                user_id=int(shared["user_id"]),
                session_track_ids=session_track_ids,
                session_labels=session_labels,
                candidate_ids=candidate_ids,
                positive_candidate_id=int(positive_row["candidate_id"]),
                expected_label=decode_expected_label(float(positive_row["y"])),
                expected_y=float(positive_row["y"]),
            )
        )

    return contexts


def sample_contexts(
    contexts: list[SeedContext],
    max_requests: int,
    mode: str,
    seed: int,
) -> list[SeedContext]:
    if max_requests <= 0:
        return []
    if mode == "sequential" or max_requests >= len(contexts):
        return contexts[:max_requests]
    sampled = pd.Series(contexts).sample(n=max_requests, random_state=seed, replace=False)
    return sampled.tolist()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def post_json(
    session: requests.Session,
    url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    response = session.post(url, json=payload, timeout=timeout_seconds)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type.lower():
        return response.json()
    body = response.text.strip()
    return {"raw_text": body} if body else {}


def extract_track_ids(payload: Any) -> list[int]:
    if payload is None:
        return []
    if isinstance(payload, list):
        result: list[int] = []
        for item in payload:
            if isinstance(item, dict):
                track_id = item.get("track_id")
                if track_id is not None:
                    result.append(int(track_id))
            else:
                result.append(int(item))
        return result
    return []


def parse_recommendation_response(
    response_json: dict[str, Any] | None,
    seed_candidates: list[int],
    top_k: int,
) -> tuple[list[int], list[int]]:
    if not response_json:
        top_ids = seed_candidates[:top_k]
        return top_ids, seed_candidates

    top_ids = extract_track_ids(response_json.get("top5_ids"))
    if not top_ids:
        top_ids = extract_track_ids(response_json.get("recommendations"))
    if not top_ids:
        top_ids = extract_track_ids(response_json.get("top_ids"))
    if not top_ids:
        top_ids = extract_track_ids(response_json.get("candidate_ids"))
    if not top_ids:
        top_ids = seed_candidates[:top_k]

    candidate_pool = extract_track_ids(response_json.get("candidate_pool_ids"))
    if not candidate_pool:
        candidate_pool = extract_track_ids(response_json.get("candidate_ids"))
    if not candidate_pool:
        candidate_pool = seed_candidates

    return top_ids[:top_k], candidate_pool


def write_jsonl(path: Path, payloads: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def build_recommend_payload(
    seed_context: SeedContext,
    request_id: str,
    requested_at: str,
    trigger_type: str,
    top_k: int,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "requested_at": requested_at,
        "trigger_type": trigger_type,
        "seed_context_id": seed_context.context_id,
        "user_id": seed_context.user_id,
        "session_id": seed_context.session_id,
        "session_track_ids": seed_context.session_track_ids,
        "session_labels": seed_context.session_labels,
        "seed_candidate_ids": seed_context.candidate_ids,
        "top_k": top_k,
    }


def build_impression_payload(
    seed_context: SeedContext,
    request_id: str,
    requested_at: str,
    trigger_type: str,
    candidate_pool_ids: list[int],
    top_ids: list[int],
    model_version: str,
) -> dict[str, Any]:
    top_scores = [round(1.0 - (idx * 0.05), 4) for idx, _ in enumerate(top_ids)]
    candidate_scores = [round(1.0 - (idx * 0.03), 4) for idx, _ in enumerate(candidate_pool_ids)]
    return {
        "request_id": request_id,
        "user_id": seed_context.user_id,
        "session_id": seed_context.session_id,
        "requested_at": requested_at,
        "trigger_type": trigger_type,
        "context_track_ids": seed_context.session_track_ids,
        "candidate_pool_ids": candidate_pool_ids,
        "candidate_scores": candidate_scores,
        "top5_ids": top_ids,
        "top5_scores": top_scores,
        "exploration_pos": [],
        "model_version": model_version,
        "fallback_level": 0,
        "latency_ms": 0.0,
        "seed_context_id": seed_context.context_id,
        "expected_positive_candidate_id": seed_context.positive_candidate_id,
    }


def build_outcome_payload(
    seed_context: SeedContext,
    request_id: str,
    top_ids: list[int],
) -> dict[str, Any]:
    chosen_track_id = seed_context.positive_candidate_id
    chosen_position = next(
        (index + 1 for index, track_id in enumerate(top_ids) if track_id == chosen_track_id),
        None,
    )
    derived_label = seed_context.expected_label
    return {
        "outcome_id": str(uuid.uuid4()),
        "request_id": request_id,
        "chosen_track_id": chosen_track_id,
        "chosen_from_rec": chosen_position is not None,
        "chosen_position": chosen_position,
        "play_duration_sec": None,
        "play_ratio": PLAY_RATIO_BY_LABEL.get(derived_label, 0.0),
        "explicit_feedback": None,
        "derived_label": derived_label,
        "created_at": iso_now(),
        "seed_context_id": seed_context.context_id,
    }


def main() -> None:
    args = parse_args()
    parquet_path = resolve_parquet_path(args.parquet_path)
    output_dir = resolve_output_dir(args.output_dir, args.run_name)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log.info("Loading ranker seed contexts from %s", parquet_path)
    all_contexts = load_seed_contexts(parquet_path)
    selected_contexts = sample_contexts(
        all_contexts,
        max_requests=args.max_requests,
        mode=args.sample_mode,
        seed=args.seed,
    )
    log.info(
        "Prepared %s sampled contexts from %s total validation contexts",
        len(selected_contexts),
        len(all_contexts),
    )

    session = requests.Session()
    recommend_logs: list[dict[str, Any]] = []
    impression_logs: list[dict[str, Any]] = []
    outcome_logs: list[dict[str, Any]] = []

    for index, seed_context in enumerate(selected_contexts, start=1):
        request_id = str(uuid.uuid4())
        requested_at = iso_now()

        recommend_payload = build_recommend_payload(
            seed_context,
            request_id=request_id,
            requested_at=requested_at,
            trigger_type=args.trigger_type,
            top_k=args.top_k,
        )
        recommend_response: dict[str, Any] | None = None
        recommend_error: str | None = None
        if args.recommend_url:
            try:
                recommend_response = post_json(
                    session,
                    args.recommend_url,
                    recommend_payload,
                    timeout_seconds=args.timeout_seconds,
                )
            except Exception as exc:
                recommend_error = str(exc)
                log.warning("Recommend request failed for context %s: %s", seed_context.context_id, exc)

        top_ids, candidate_pool_ids = parse_recommendation_response(
            recommend_response,
            seed_context.candidate_ids,
            args.top_k,
        )

        impression_payload = build_impression_payload(
            seed_context,
            request_id=request_id,
            requested_at=requested_at,
            trigger_type=args.trigger_type,
            candidate_pool_ids=candidate_pool_ids,
            top_ids=top_ids,
            model_version=args.model_version,
        )
        impression_error: str | None = None
        if args.impression_url:
            try:
                post_json(
                    session,
                    args.impression_url,
                    impression_payload,
                    timeout_seconds=args.timeout_seconds,
                )
            except Exception as exc:
                impression_error = str(exc)
                log.warning("Impression request failed for context %s: %s", seed_context.context_id, exc)

        outcome_payload = build_outcome_payload(
            seed_context,
            request_id=request_id,
            top_ids=top_ids,
        )
        outcome_error: str | None = None
        if args.outcome_url:
            try:
                post_json(
                    session,
                    args.outcome_url,
                    outcome_payload,
                    timeout_seconds=args.timeout_seconds,
                )
            except Exception as exc:
                outcome_error = str(exc)
                log.warning("Outcome request failed for context %s: %s", seed_context.context_id, exc)

        recommend_logs.append(
            {
                "request_payload": recommend_payload,
                "response_json": recommend_response,
                "error": recommend_error,
                "seed_context": asdict(seed_context),
            }
        )
        impression_logs.append(impression_payload | {"error": impression_error})
        outcome_logs.append(outcome_payload | {"error": outcome_error})

        if index <= args.print_samples:
            log.info(
                "Sample %s | session=%s user=%s prefix_len=%s expected_next=%s top_ids=%s",
                index,
                seed_context.session_id,
                seed_context.user_id,
                len(seed_context.session_track_ids),
                seed_context.positive_candidate_id,
                top_ids,
            )

        if args.sleep_seconds > 0 and index < len(selected_contexts):
            time.sleep(args.sleep_seconds)

    recommend_path = output_dir / "recommend_requests.jsonl"
    impression_path = output_dir / "impression_logs.jsonl"
    outcome_path = output_dir / "outcome_logs.jsonl"
    summary_path = output_dir / "run_summary.json"

    write_jsonl(recommend_path, recommend_logs)
    write_jsonl(impression_path, impression_logs)
    write_jsonl(outcome_path, outcome_logs)

    summary = {
        "parquet_path": str(parquet_path),
        "output_dir": str(output_dir),
        "n_available_contexts": len(all_contexts),
        "n_requests_emitted": len(selected_contexts),
        "sample_mode": args.sample_mode,
        "seed": args.seed,
        "top_k": args.top_k,
        "recommend_url": args.recommend_url,
        "impression_url": args.impression_url,
        "outcome_url": args.outcome_url,
        "request_log": str(recommend_path),
        "impression_log": str(impression_path),
        "outcome_log": str(outcome_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    log.info("Generator run complete.")
    log.info("Requests written to %s", recommend_path)
    log.info("Impressions written to %s", impression_path)
    log.info("Outcomes written to %s", outcome_path)
    log.info("Summary written to %s", summary_path)


if __name__ == "__main__":
    main()
