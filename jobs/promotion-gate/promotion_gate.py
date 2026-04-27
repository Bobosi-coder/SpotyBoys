from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from packages.artifact_runtime import validate_serving_bundle_directory


DEFAULT_THRESHOLDS = {
    "min_recall_at_20": 0.0,
    "min_mrr_at_20": 0.0,
    "max_recall_drop": 0.02,
    "max_mrr_drop": 0.02,
}


def evaluate_promotion(
    candidate_bundle: str | Path,
    evaluation_path: str | Path,
    *,
    thresholds: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    bundle = Path(candidate_bundle)
    threshold_values = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        threshold_values.update({key: float(value) for key, value in thresholds.items()})

    try:
        manifest = validate_serving_bundle_directory(bundle)
    except Exception as exc:
        decision = _decision(
            bundle,
            model_version="unknown",
            decision="block",
            reason=f"bundle validation failed: {exc}",
            offline_metrics={},
            thresholds=threshold_values,
        )
        _write_decision(bundle, decision)
        return decision

    evaluation = json.loads(Path(evaluation_path).read_text(encoding="utf-8"))
    candidate = dict(evaluation.get("candidate") or {})
    baseline = dict(evaluation.get("baseline") or {})
    reason = _offline_reason(candidate, baseline, threshold_values)
    decision = _decision(
        bundle,
        model_version=str(manifest.get("model_version") or manifest.get("version") or "unknown"),
        decision="approve" if reason == "offline_metrics_passed" else "block",
        reason=reason,
        offline_metrics={"candidate": candidate, "baseline": baseline},
        thresholds=threshold_values,
    )
    _write_decision(bundle, decision)
    return decision


def evaluate_activation_gate(candidate_bundle: str | Path, *, allow_override: bool | None = None) -> Dict[str, Any]:
    bundle = Path(candidate_bundle)
    override = _truthy(os.environ.get("SPOTIBOYS_ALLOW_UNAPPROVED_REFRESH", "false")) if allow_override is None else allow_override
    try:
        manifest = validate_serving_bundle_directory(bundle)
    except Exception as exc:
        if override:
            return _override_decision(bundle, "unknown", f"bundle validation failed but override enabled: {exc}")
        raise RuntimeError(f"promotion gate blocked serving refresh: bundle validation failed: {exc}") from exc

    model_version = str(manifest.get("model_version") or manifest.get("version") or "unknown")
    decision_path = bundle / "promotion_decision.json"
    if not decision_path.exists():
        if override:
            return _override_decision(bundle, model_version, "missing promotion_decision.json but override enabled")
        raise RuntimeError("promotion gate blocked serving refresh: missing promotion_decision.json")

    payload = json.loads(decision_path.read_text(encoding="utf-8"))
    decision = str(payload.get("decision") or "").lower()
    approved = decision in {"approve", "approved"}
    if not approved:
        if override:
            return _override_decision(bundle, model_version, f"promotion decision was {decision or 'missing'} but override enabled")
        raise RuntimeError(f"promotion gate blocked serving refresh: decision={decision or 'missing'}")
    if str(payload.get("model_version") or model_version) != model_version:
        raise RuntimeError("promotion gate blocked serving refresh: promotion decision model_version mismatch")
    return payload


def _offline_reason(candidate: Mapping[str, Any], baseline: Mapping[str, Any], thresholds: Mapping[str, float]) -> str:
    recall = float(candidate.get("recall_at_20") or 0)
    mrr = float(candidate.get("mrr_at_20") or 0)
    baseline_recall = float(baseline.get("recall_at_20") or 0)
    baseline_mrr = float(baseline.get("mrr_at_20") or 0)
    if recall < thresholds["min_recall_at_20"]:
        return "recall_below_minimum"
    if mrr < thresholds["min_mrr_at_20"]:
        return "mrr_below_minimum"
    if baseline_recall - recall > thresholds["max_recall_drop"]:
        return "recall_regression"
    if baseline_mrr - mrr > thresholds["max_mrr_drop"]:
        return "mrr_regression"
    return "offline_metrics_passed"


def _decision(
    bundle: Path,
    *,
    model_version: str,
    decision: str,
    reason: str,
    offline_metrics: Mapping[str, Any],
    thresholds: Mapping[str, float],
) -> Dict[str, Any]:
    return {
        "decision_id": f"promotion_{uuid.uuid4().hex}",
        "model_version": model_version,
        "decision": decision,
        "offline_metrics": dict(offline_metrics),
        "thresholds": dict(thresholds),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "bundle_path": str(bundle),
    }


def _override_decision(bundle: Path, model_version: str, reason: str) -> Dict[str, Any]:
    return _decision(
        bundle,
        model_version=model_version,
        decision="approved",
        reason=reason,
        offline_metrics={},
        thresholds={},
    )


def _write_decision(bundle: Path, decision: Mapping[str, Any]) -> None:
    (bundle / "promotion_decision.json").write_text(
        json.dumps(dict(decision), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
