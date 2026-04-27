from __future__ import annotations

import os
import statistics
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional


def compute_rollup(
    *,
    window_name: str,
    window_start: datetime,
    window_end: datetime,
    inputs: Mapping[str, List[Dict[str, Any]]],
    minimum_sample_size: int = 20,
) -> Dict[str, Any]:
    request_metrics = _in_window(inputs.get("request_metrics", []), window_start, window_end)
    playback_events = _in_window(inputs.get("playback_events", []), window_start, window_end)
    feedback_events = _in_window(inputs.get("feedback_events", []), window_start, window_end)
    latencies = [float(item.get("total_latency_ms") or 0) for item in request_metrics]
    request_count = len(request_metrics)
    error_count = sum(1 for item in request_metrics if item.get("pipeline_error") or item.get("error_code"))
    fallback_count = sum(
        1
        for item in request_metrics
        if str(item.get("fallback_level") or "none") != "none"
        or str(item.get("fallback_state") or "healthy") != "healthy"
    )
    returned_counts = [float(item.get("returned_count") or 0) for item in request_metrics]
    model_version = _mode([str(item.get("model_version") or "") for item in request_metrics if item.get("model_version")])
    finalized = [item for item in playback_events if item.get("event_type") in {"skip", "complete"} or item.get("playratio") is not None]
    skip_count = sum(1 for item in finalized if item.get("event_type") == "skip")
    complete_count = sum(1 for item in finalized if item.get("event_type") == "complete" or float(item.get("playratio") or 0) >= 0.8)
    dislike_count = sum(1 for item in feedback_events if item.get("feedback_type") == "dislike")
    catalog_failures = sum(1 for item in request_metrics if item.get("fallback_state") == "catalog_insufficient")
    stream_failures = sum(1 for item in playback_events if str(item.get("event_type") or "").startswith("stream_failure"))
    artists = [str(item.get("artist") or "") for item in request_metrics if item.get("artist")]
    top_artist_share = _ratio(_mode_count(artists), len(artists))
    repeat_violation_count = _repeat_violation_count(request_metrics)
    sample_status = "ok" if request_count >= minimum_sample_size else "insufficient"

    return {
        "rollup_id": f"rollup_{uuid.uuid4().hex}",
        "window_name": window_name,
        "window_start": _iso(window_start),
        "window_end": _iso(window_end),
        "model_version": model_version,
        "request_count": request_count,
        "error_rate": _ratio(error_count, request_count),
        "fallback_rate": _ratio(fallback_count, request_count),
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
        "avg_returned_count": (sum(returned_counts) / len(returned_counts)) if returned_counts else 0.0,
        "catalog_failure_count": catalog_failures,
        "stream_failure_count": stream_failures,
        "completion_rate": _ratio(complete_count, len(finalized)),
        "skip_rate": _ratio(skip_count, len(finalized)),
        "dislike_rate": _ratio(dislike_count, max(request_count, 1)),
        "top_artist_share": top_artist_share,
        "repeat_violation_count": repeat_violation_count,
        "sample_status": sample_status,
        "metrics_json": {
            "sample_status": sample_status,
            "finalized_playback_count": len(finalized),
            "feedback_count": len(feedback_events),
            "stream_failure_rate": _ratio(stream_failures, max(len(playback_events), 1)),
        },
        "created_at": _iso(datetime.now(timezone.utc)),
    }


def evaluate_rollback(
    rollup: Mapping[str, Any],
    *,
    active_model: Optional[Mapping[str, Any]] = None,
    previous_model: Optional[Mapping[str, Any]] = None,
    thresholds: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    threshold_values = {
        "minimum_request_count": float(os.environ.get("SPOTIBOYS_ROLLBACK_MIN_REQUESTS", "20")),
        "max_error_rate": float(os.environ.get("SPOTIBOYS_ROLLBACK_MAX_ERROR_RATE", "0.05")),
        "max_fallback_rate": float(os.environ.get("SPOTIBOYS_ROLLBACK_MAX_FALLBACK_RATE", "0.25")),
        "max_p95_latency_ms": float(os.environ.get("SPOTIBOYS_ROLLBACK_MAX_P95_LATENCY_MS", "1500")),
        "max_stream_failure_rate": float(os.environ.get("SPOTIBOYS_ROLLBACK_MAX_STREAM_FAILURE_RATE", "0.05")),
        "max_dislike_rate": float(os.environ.get("SPOTIBOYS_ROLLBACK_MAX_DISLIKE_RATE", "0.15")),
        "max_top_artist_share": float(os.environ.get("SPOTIBOYS_ROLLBACK_MAX_TOP_ARTIST_SHARE", "0.60")),
    }
    if thresholds:
        threshold_values.update({key: float(value) for key, value in thresholds.items()})

    request_count = int(rollup.get("request_count") or rollup.get("recommendation_request_count") or 0)
    model_version = str(
        rollup.get("model_version")
        or (active_model or {}).get("model_version")
        or "unknown"
    )
    metrics_json = dict(rollup.get("metrics_json") or rollup.get("metrics") or {})
    stream_failure_rate = float(metrics_json.get("stream_failure_rate") or rollup.get("stream_failure_rate") or 0)

    decision = "no_action"
    reason = "within_thresholds"
    if request_count < int(threshold_values["minimum_request_count"]):
        reason = "insufficient_sample"
    elif float(rollup.get("error_rate") or 0) > threshold_values["max_error_rate"]:
        decision, reason = "recommend_rollback", "high_error_rate"
    elif float(rollup.get("fallback_rate") or 0) > threshold_values["max_fallback_rate"]:
        decision, reason = "recommend_rollback", "high_fallback_rate"
    elif float(rollup.get("p95_latency_ms") or 0) > threshold_values["max_p95_latency_ms"]:
        decision, reason = "mark_degraded", "latency_breach"
    elif stream_failure_rate > threshold_values["max_stream_failure_rate"]:
        decision, reason = "mark_degraded", "stream_failure_rate"
    elif float(rollup.get("dislike_rate") or 0) > threshold_values["max_dislike_rate"]:
        decision, reason = "recommend_rollback", "high_dislike_rate"
    elif int(rollup.get("repeat_violation_count") or 0) > 0:
        decision, reason = "mark_degraded", "repeat_violation"
    elif float(rollup.get("top_artist_share") or 0) > threshold_values["max_top_artist_share"]:
        decision, reason = "mark_degraded", "top_artist_concentration"

    previous_version = (previous_model or {}).get("model_version")
    if decision == "recommend_rollback" and not previous_version:
        decision = "mark_degraded"
        reason = f"{reason}_no_previous_good"

    return {
        "decision_id": f"decision_{uuid.uuid4().hex}",
        "decision_type": "rollback_check",
        "model_version": model_version,
        "decision": decision,
        "reason": reason,
        "metrics_json": dict(rollup),
        "thresholds_json": threshold_values,
        "created_at": _iso(datetime.now(timezone.utc)),
        "executed": False,
        "execution_note": "manual rollback recommended only; auto rollback disabled by default",
    }


def _in_window(rows: Iterable[Dict[str, Any]], start: datetime, end: datetime) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for row in rows:
        created_at = _parse_time(row.get("created_at") or row.get("occurred_at") or row.get("received_at"))
        if created_at is None or start <= created_at <= end:
            output.append(row)
    return output


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _ratio(numerator: float, denominator: float) -> float:
    return round(float(numerator) / float(denominator), 6) if denominator else 0.0


def _percentile(values: List[float], percentile: int) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    if len(values) == 1:
        return float(values[0])
    index = round((percentile / 100) * (len(values) - 1))
    return float(values[index])


def _mode(values: List[str]) -> Optional[str]:
    if not values:
        return None
    return statistics.mode(values)


def _mode_count(values: List[str]) -> int:
    return Counter(values).most_common(1)[0][1] if values else 0


def _repeat_violation_count(rows: List[Dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        track_ids = row.get("track_ids") or row.get("returned_track_ids") or []
        if isinstance(track_ids, str):
            track_ids = [item for item in track_ids.split(",") if item]
        if len(track_ids) != len(set(track_ids)):
            count += 1
    return count
