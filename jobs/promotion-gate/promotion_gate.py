from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.artifact_runtime import validate_serving_bundle_directory  # noqa: E402


MAX_REGRESSION = 0.02
QUALITY_METRICS = ("recall_at_20", "mrr_at_20")


def evaluate_promotion(
    candidate_dir: Path,
    eval_path: Path,
    *,
    baseline_eval_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> dict:
    errors: list[str] = []
    manifest: Mapping[str, Any] = {}
    try:
        manifest = validate_serving_bundle_directory(candidate_dir)
    except Exception as exc:
        errors.append(f"bundle validation failed: {type(exc).__name__}: {exc}")

    evaluation = _read_json(eval_path)
    baseline = _read_json(baseline_eval_path) if baseline_eval_path else evaluation.get("baseline", {})
    candidate_metrics = evaluation.get("candidate", evaluation.get("metrics", evaluation))
    metric_results = {}
    for metric in QUALITY_METRICS:
        candidate_value = _as_float(candidate_metrics.get(metric))
        baseline_value = _as_float(baseline.get(metric))
        if candidate_value is None:
            errors.append(f"missing candidate metric {metric}")
            continue
        if baseline_value is None:
            metric_results[metric] = {
                "candidate": candidate_value,
                "baseline": None,
                "status": "no_baseline_available",
            }
            continue
        floor = baseline_value * (1.0 - MAX_REGRESSION)
        passed = candidate_value >= floor
        metric_results[metric] = {
            "candidate": candidate_value,
            "baseline": baseline_value,
            "minimum_allowed": floor,
            "status": "pass" if passed else "fail",
        }
        if not passed:
            errors.append(f"{metric} regressed more than {MAX_REGRESSION:.0%}")

    decision = {
        "decision_id": f"promotion_{uuid.uuid4().hex}",
        "decision_type": "promotion",
        "candidate_version": str(manifest.get("version") or candidate_dir.name),
        "model_version": str(manifest.get("model_version") or manifest.get("version") or candidate_dir.name),
        "decision": "block" if errors else "approve",
        "reason": "; ".join(errors) if errors else "candidate bundle passed manifest validation and offline non-regression gate",
        "metrics": {
            "metric_results": metric_results,
            "max_allowed_regression": MAX_REGRESSION,
            "candidate_dir": str(candidate_dir),
            "eval_path": str(eval_path),
        },
        "artifact_uri": str(candidate_dir / "manifest.json"),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "owner": "VM2_retraining",
    }
    destination = output_path or candidate_dir / "promotion_decision.json"
    destination.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return decision


def _read_json(path: Optional[Path]) -> dict:
    if not path:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="VM2 promotion gate for serving-ready model bundles.")
    parser.add_argument("--candidate", required=True, type=Path, help="Candidate Real_service/<version> bundle directory.")
    parser.add_argument("--eval", required=True, type=Path, help="Offline evaluation JSON.")
    parser.add_argument("--baseline-eval", type=Path, help="Optional baseline evaluation JSON.")
    parser.add_argument("--output", type=Path, help="Optional promotion decision JSON path.")
    args = parser.parse_args()
    decision = evaluate_promotion(
        args.candidate,
        args.eval,
        baseline_eval_path=args.baseline_eval,
        output_path=args.output,
    )
    print(json.dumps(decision, indent=2, sort_keys=True))
    raise SystemExit(0 if decision["decision"] == "approve" else 2)


if __name__ == "__main__":
    main()
