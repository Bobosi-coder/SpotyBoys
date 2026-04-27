from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional

from packages.shared_contracts.enums import FallbackLevel


class FallbackState(str, Enum):
    HEALTHY = "healthy"
    MODEL_DEGRADED = "model_degraded"
    MODEL_UNAVAILABLE = "model_unavailable"
    CATALOG_INSUFFICIENT = "catalog_insufficient"
    NAVIDROME_DEGRADED = "navidrome_degraded"
    FALLBACK_ONLY = "fallback_only"


class DegradedAction(str, Enum):
    NORMAL = "normal"
    FALLBACK_ONLY = "fallback_only"


CATALOG_MIN_PLAYABLE = 4


@dataclass(frozen=True)
class ServingStateDecision:
    state: FallbackState
    fallback_level: FallbackLevel
    reason: Optional[str] = None
    action: DegradedAction = DegradedAction.NORMAL
    degraded: bool = False
    pipeline_error: bool = False
    error_code: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def uses_fallback(self) -> bool:
        return self.state != FallbackState.HEALTHY or self.fallback_level != FallbackLevel.NONE


def normalize_model_status(status: Mapping[str, Any] | None) -> dict[str, Any]:
    status = status or {}
    action = str(status.get("action") or DegradedAction.NORMAL.value).strip().lower()
    if action not in {item.value for item in DegradedAction}:
        action = DegradedAction.NORMAL.value
    return {
        "degraded": bool(status.get("degraded", False)),
        "reason": status.get("reason"),
        "action": action,
        "updated_at": status.get("updated_at"),
    }


def decide_serving_state(
    *,
    serving_bundle_available: bool,
    playable_count: int,
    ranked_count: int,
    returned_count: int,
    model_status: Mapping[str, Any] | None = None,
    pipeline_error: Exception | str | None = None,
) -> ServingStateDecision:
    status = normalize_model_status(model_status)
    error_code = _error_code(pipeline_error)
    details = {
        "playable_count": playable_count,
        "ranked_count": ranked_count,
        "returned_count": returned_count,
    }

    if playable_count < CATALOG_MIN_PLAYABLE or returned_count < min(CATALOG_MIN_PLAYABLE, playable_count):
        return ServingStateDecision(
            state=FallbackState.CATALOG_INSUFFICIENT,
            fallback_level=FallbackLevel.CATALOG_SAFE_FALLBACK,
            reason="catalog_insufficient",
            action=DegradedAction.FALLBACK_ONLY,
            degraded=True,
            pipeline_error=bool(pipeline_error),
            error_code=error_code,
            details=details,
        )
    if not serving_bundle_available:
        return ServingStateDecision(
            state=FallbackState.MODEL_UNAVAILABLE,
            fallback_level=FallbackLevel.NON_PERSONALIZED,
            reason="model_unavailable",
            action=DegradedAction.FALLBACK_ONLY,
            degraded=True,
            pipeline_error=bool(pipeline_error),
            error_code=error_code,
            details=details,
        )
    if pipeline_error:
        return ServingStateDecision(
            state=FallbackState.MODEL_UNAVAILABLE,
            fallback_level=FallbackLevel.CATALOG_SAFE_FALLBACK,
            reason="pipeline_error",
            action=DegradedAction.FALLBACK_ONLY,
            degraded=True,
            pipeline_error=True,
            error_code=error_code,
            details=details,
        )
    if status["degraded"]:
        action = DegradedAction(status["action"])
        return ServingStateDecision(
            state=FallbackState.FALLBACK_ONLY if action == DegradedAction.FALLBACK_ONLY else FallbackState.MODEL_DEGRADED,
            fallback_level=FallbackLevel.CATALOG_SAFE_FALLBACK if action == DegradedAction.FALLBACK_ONLY else FallbackLevel.NON_PERSONALIZED,
            reason=str(status.get("reason") or "manual_degraded"),
            action=action,
            degraded=True,
            details=details,
        )
    if returned_count == 0 or ranked_count == 0:
        return ServingStateDecision(
            state=FallbackState.FALLBACK_ONLY,
            fallback_level=FallbackLevel.CATALOG_SAFE_FALLBACK,
            reason="empty_ranked_output",
            action=DegradedAction.FALLBACK_ONLY,
            degraded=True,
            details=details,
        )
    return ServingStateDecision(
        state=FallbackState.HEALTHY,
        fallback_level=FallbackLevel.NONE,
        details=details,
    )


def _error_code(error: Exception | str | None) -> Optional[str]:
    if not error:
        return None
    if isinstance(error, Exception):
        return type(error).__name__
    return str(error)[:80] or "pipeline_error"
