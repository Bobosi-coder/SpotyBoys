from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional


ROLLBACK_THRESHOLDS = {
    "recommendation_error_rate": {"min": 20, "threshold": 0.05},
    "fallback_rate": {"min": 50, "threshold": 0.25},
    "stream_failure_rate": {"min": 20, "threshold": 0.05},
    "completion_rate_drop": {"min": 20, "threshold": 0.30},
    "dislike_rate": {"min": 20, "threshold": 0.15},
    "top_artist_share": {"min": 50, "threshold": 0.60},
    "repeat_violation_count": {"min": 20, "threshold": 0},
}


def build_serving_rollup(
    inputs: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    window_name: str,
    window_start: datetime,
    window_end: datetime,
    model_version: str = "unknown",
    baseline_rollup: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    request_metrics = list(inputs.get("request_metrics", []))
    impressions = list(inputs.get("recommendation_impressions", []))
    rendered = list(inputs.get("rendered_impressions", []))
    playback = list(inputs.get("playback_events", []))
    feedback = list(inputs.get("feedback_events", []))

    recommendation_metrics = [
        row for row in request_metrics if str(row.get("endpoint", "")).startswith("recommendations")
    ]
    stream_metrics = [row for row in request_metrics if str(row.get("endpoint", "")).startswith("stream")]
    errors = [row for row in request_metrics if row.get("status") != "success"]
    rec_errors = [row for row in recommendation_metrics if row.get("status") != "success"]
    fallback_rows = [
        row
        for row in recommendation_metrics
        if str(row.get("fallback_level") or "none").lower() not in {"", "none"}
    ]
    latencies = [float(row.get("latency_ms") or 0) for row in request_metrics]

    queue_items = _queue_items(impressions)
    unique_track_ids = {str(item.get("track_id")) for item in queue_items if item.get("track_id")}
    artist_counts = Counter(str(item.get("artist") or "Unknown Artist") for item in queue_items)
    unique_artist_count = len(artist_counts)
    top_artist_share = (max(artist_counts.values()) / len(queue_items)) if queue_items else 0.0
    repeat_violation_count = _repeat_violations(impressions)

    playback_start_count = sum(1 for row in playback if row.get("event_type") == "playback_start")
    skip_count = sum(1 for row in playback if row.get("event_type") == "skip")
    complete_count = sum(1 for row in playback if row.get("event_type") == "complete")
    dislike_count = sum(1 for row in feedback if row.get("feedback_type") == "dislike")

    request_count = len(request_metrics)
    recommendation_request_count = len(recommendation_metrics)
    stream_request_count = len(stream_metrics)
    feedback_denominator = max(playback_start_count, len(feedback))
    metrics = {
        "model_output": {
            "recommended_item_count": len(queue_items),
            "unique_track_count": len(unique_track_ids),
            "unique_artist_count": unique_artist_count,
            "top_artist_share": top_artist_share,
            "top_artists": artist_counts.most_common(10),
            "repeat_violation_count": repeat_violation_count,
        },
        "operational": {
            "request_count": request_count,
            "recommendation_request_count": recommendation_request_count,
            "recommendation_error_count": len(rec_errors),
            "stream_request_count": stream_request_count,
            "stream_failure_count": sum(1 for row in stream_metrics if row.get("status") != "success"),
            "error_count": len(errors),
            "p50_latency_ms": _percentile(latencies, 50),
            "p95_latency_ms": _percentile(latencies, 95),
        },
        "feedback": {
            "rendered_impression_count": len(rendered),
            "playback_start_count": playback_start_count,
            "skip_count": skip_count,
            "complete_count": complete_count,
            "feedback_count": len(feedback),
            "dislike_count": dislike_count,
        },
        "sample_guards": ROLLBACK_THRESHOLDS,
    }
    rollup = {
        "rollup_id": f"rollup_{window_name}_{uuid.uuid4().hex}",
        "window_name": window_name,
        "window_start": _iso(window_start),
        "window_end": _iso(window_end),
        "model_version": model_version,
        "request_count": request_count,
        "recommendation_request_count": recommendation_request_count,
        "stream_request_count": stream_request_count,
        "error_rate": _rate(len(errors), request_count),
        "fallback_rate": _rate(len(fallback_rows), recommendation_request_count),
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
        "stream_failure_rate": _rate(
            sum(1 for row in stream_metrics if row.get("status") != "success"),
            stream_request_count,
        ),
        "event_ingestion_count": len(rendered) + len(playback) + len(feedback),
        "impression_count": len(impressions),
        "playback_start_count": playback_start_count,
        "skip_rate": _rate(skip_count, playback_start_count),
        "completion_rate": _rate(complete_count, playback_start_count),
        "dislike_rate": _rate(dislike_count, feedback_denominator),
        "unique_track_count": len(unique_track_ids),
        "unique_artist_count": unique_artist_count,
        "top_artist_share": top_artist_share,
        "repeat_violation_count": repeat_violation_count,
        "sample_status": _sample_status(
            recommendation_request_count=recommendation_request_count,
            stream_request_count=stream_request_count,
            playback_start_count=playback_start_count,
            recommended_item_count=len(queue_items),
        ),
        "metrics": metrics,
    }
    if baseline_rollup:
        baseline_completion = float(baseline_rollup.get("completion_rate") or 0)
        if baseline_completion:
            rollup["metrics"]["baseline"] = {
                "completion_rate": baseline_completion,
                "completion_rate_drop": max(0.0, baseline_completion - rollup["completion_rate"]) / baseline_completion,
            }
    return rollup


def evaluate_rollback(
    rollup: Mapping[str, Any],
    *,
    active_model: Optional[Mapping[str, Any]] = None,
    previous_model: Optional[Mapping[str, Any]] = None,
    baseline_rollup: Optional[Mapping[str, Any]] = None,
    auto_rollback: bool = False,
) -> Dict[str, Any]:
    breaches: List[str] = []
    insufficient: List[str] = []
    rec_count = int(rollup.get("recommendation_request_count") or 0)
    stream_count = int(rollup.get("stream_request_count") or 0)
    playback_starts = int(rollup.get("playback_start_count") or 0)
    recommended_items = int(rollup.get("metrics", {}).get("model_output", {}).get("recommended_item_count") or 0)

    _check_rate(
        breaches,
        insufficient,
        "recommendation_error_rate",
        rec_count,
        float(rollup.get("metrics", {}).get("operational", {}).get("recommendation_error_count") or 0) / rec_count
        if rec_count
        else 0.0,
    )
    _check_rate(breaches, insufficient, "fallback_rate", rec_count, float(rollup.get("fallback_rate") or 0.0))
    _check_rate(breaches, insufficient, "stream_failure_rate", stream_count, float(rollup.get("stream_failure_rate") or 0.0))
    _check_rate(breaches, insufficient, "dislike_rate", playback_starts, float(rollup.get("dislike_rate") or 0.0))
    _check_rate(breaches, insufficient, "top_artist_share", recommended_items, float(rollup.get("top_artist_share") or 0.0))

    repeat_min = int(ROLLBACK_THRESHOLDS["repeat_violation_count"]["min"])
    if rec_count < repeat_min:
        insufficient.append("repeat_violation_count")
    elif int(rollup.get("repeat_violation_count") or 0) > 0:
        breaches.append("repeat_violation_count")

    baseline_completion = None
    if baseline_rollup:
        baseline_completion = float(baseline_rollup.get("completion_rate") or 0.0)
    if baseline_completion:
        if playback_starts < int(ROLLBACK_THRESHOLDS["completion_rate_drop"]["min"]):
            insufficient.append("completion_rate_drop")
        else:
            drop = max(0.0, baseline_completion - float(rollup.get("completion_rate") or 0.0)) / baseline_completion
            if drop > float(ROLLBACK_THRESHOLDS["completion_rate_drop"]["threshold"]):
                breaches.append("completion_rate_drop")
    else:
        insufficient.append("completion_rate_drop")

    if breaches:
        decision = "rollback_executed" if auto_rollback else "rollback_recommended"
        reason = "online serving thresholds breached: " + ", ".join(sorted(breaches))
    elif insufficient:
        decision = "no_action_insufficient_sample"
        reason = "sample size below guarded thresholds: " + ", ".join(sorted(set(insufficient)))
    else:
        decision = "no_action"
        reason = "online serving metrics are within guarded thresholds"

    return {
        "decision_id": f"rollback_{uuid.uuid4().hex}",
        "decision_type": "rollback",
        "model_version": (active_model or {}).get("model_version") or rollup.get("model_version"),
        "candidate_version": (previous_model or {}).get("model_version"),
        "decision": decision,
        "reason": reason,
        "metrics": {
            "breaches": sorted(set(breaches)),
            "insufficient_sample": sorted(set(insufficient)),
            "rollup": dict(rollup),
            "thresholds": ROLLBACK_THRESHOLDS,
        },
        "artifact_uri": (previous_model or {}).get("manifest_uri"),
        "created_at": _iso(datetime.now(timezone.utc)),
    }


def window_bounds(window: str, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end - _parse_window(window), end


def _parse_window(window: str) -> timedelta:
    value = window.strip().lower()
    if value.endswith("m"):
        return timedelta(minutes=int(value[:-1]))
    if value.endswith("h"):
        return timedelta(hours=int(value[:-1]))
    raise ValueError(f"unsupported window: {window}")


def _queue_items(impressions: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    items: List[Mapping[str, Any]] = []
    for impression in impressions:
        queue = impression.get("queue") or {}
        if isinstance(queue, Mapping):
            raw_items = queue.get("items", [])
            if isinstance(raw_items, list):
                items.extend(item for item in raw_items if isinstance(item, Mapping))
    return items


def _repeat_violations(impressions: Iterable[Mapping[str, Any]]) -> int:
    violations = 0
    by_session: Dict[str, set[str]] = defaultdict(set)
    for impression in impressions:
        session_id = str(impression.get("session_id") or "")
        for item in _queue_items([impression]):
            track_id = str(item.get("track_id") or "")
            if not track_id:
                continue
            if track_id in by_session[session_id]:
                violations += 1
            by_session[session_id].add(track_id)
    return violations


def _rate(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _percentile(values: List[float], percentile: int) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((percentile / 100) * (len(ordered) - 1)))))
    return ordered[index]


def _sample_status(
    *,
    recommendation_request_count: int,
    stream_request_count: int,
    playback_start_count: int,
    recommended_item_count: int,
) -> str:
    minimums = [
        recommendation_request_count >= int(ROLLBACK_THRESHOLDS["recommendation_error_rate"]["min"]),
        stream_request_count >= int(ROLLBACK_THRESHOLDS["stream_failure_rate"]["min"]),
        playback_start_count >= int(ROLLBACK_THRESHOLDS["dislike_rate"]["min"]),
        recommended_item_count >= int(ROLLBACK_THRESHOLDS["top_artist_share"]["min"]),
    ]
    return "sufficient_sample" if all(minimums) else "insufficient_sample"


def _check_rate(breaches: List[str], insufficient: List[str], name: str, sample: int, value: float) -> None:
    rule = ROLLBACK_THRESHOLDS[name]
    if sample < int(rule["min"]):
        insufficient.append(name)
        return
    if value > float(rule["threshold"]):
        breaches.append(name)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.replace(microsecond=0).isoformat()
