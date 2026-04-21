from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

# Events timestamped more than 48 h in the past are stale (session timeout is 24 h;
# 48 h gives generous clock-skew headroom while still catching replayed/corrupted data).
MAX_EVENT_AGE_HOURS = 48

# Allow up to 60 s of client clock drift into the future.
MAX_FUTURE_TOLERANCE_SECONDS = 60

# playback_ms above this is almost certainly a bug (1 hour = 3 600 000 ms).
MAX_PLAYBACK_MS = 3_600_000


@dataclass
class IngestionRejection(Exception):
    event_type: str           # "impression" | "playback" | "feedback"
    reasons: List[str]
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"IngestionRejection({self.event_type}): {'; '.join(self.reasons)}"


def validate_impression(payload: Dict[str, Any]) -> None:
    """Raise IngestionRejection if the impression payload fails quality checks."""
    reasons: List[str] = []

    _check_nonempty_string(payload, "impression_id", reasons)
    _check_nonempty_string(payload, "request_id", reasons)
    _check_nonempty_string(payload, "session_id", reasons)
    _check_nonempty_string(payload, "user_id", reasons)

    visible = payload.get("visible_items")
    if not visible:
        reasons.append("visible_items must contain at least one item")

    rendered_at = payload.get("rendered_at")
    if rendered_at is not None:
        _check_timestamp_range(rendered_at, "rendered_at", reasons)

    if reasons:
        raise IngestionRejection(event_type="impression", reasons=reasons, raw_payload=payload)


def validate_playback(payload: Dict[str, Any]) -> None:
    """Raise IngestionRejection if the playback payload fails quality checks."""
    reasons: List[str] = []

    _check_nonempty_string(payload, "event_id", reasons)
    _check_nonempty_string(payload, "track_id", reasons)
    _check_nonempty_string(payload, "session_id", reasons)
    _check_nonempty_string(payload, "user_id", reasons)

    playback_ms = payload.get("playback_ms")
    if playback_ms is not None and int(playback_ms) > MAX_PLAYBACK_MS:
        reasons.append(
            f"playback_ms={playback_ms} exceeds maximum {MAX_PLAYBACK_MS} ms (1 hour)"
        )

    occurred_at = payload.get("occurred_at")
    if occurred_at is not None:
        _check_timestamp_range(occurred_at, "occurred_at", reasons)

    if reasons:
        raise IngestionRejection(event_type="playback", reasons=reasons, raw_payload=payload)


def validate_feedback(payload: Dict[str, Any]) -> None:
    """Raise IngestionRejection if the feedback payload fails quality checks."""
    reasons: List[str] = []

    _check_nonempty_string(payload, "event_id", reasons)
    _check_nonempty_string(payload, "track_id", reasons)
    _check_nonempty_string(payload, "session_id", reasons)
    _check_nonempty_string(payload, "user_id", reasons)

    occurred_at = payload.get("occurred_at")
    if occurred_at is not None:
        _check_timestamp_range(occurred_at, "occurred_at", reasons)

    if reasons:
        raise IngestionRejection(event_type="feedback", reasons=reasons, raw_payload=payload)


# ── helpers ───────────────────────────────────────────────────────────────────

def _check_nonempty_string(payload: Dict[str, Any], key: str, reasons: List[str]) -> None:
    value = payload.get(key)
    if not value or not str(value).strip():
        reasons.append(f"{key} must be a non-empty string")


def _check_timestamp_range(value: Any, field_name: str, reasons: List[str]) -> None:
    try:
        ts: datetime = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if ts > now + timedelta(seconds=MAX_FUTURE_TOLERANCE_SECONDS):
            reasons.append(
                f"{field_name} is {(ts - now).seconds}s in the future (max tolerance {MAX_FUTURE_TOLERANCE_SECONDS}s)"
            )
        elif ts < now - timedelta(hours=MAX_EVENT_AGE_HOURS):
            age_h = (now - ts).total_seconds() / 3600
            reasons.append(
                f"{field_name} is {age_h:.1f}h old (max {MAX_EVENT_AGE_HOURS}h)"
            )
    except (TypeError, ValueError):
        reasons.append(f"{field_name} could not be parsed as a datetime")
