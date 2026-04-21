from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.artifact_runtime import validate_serving_bundle_directory  # noqa: E402
from packages.config import load_config  # noqa: E402
from packages.db_access.factory import build_repository_and_runtime  # noqa: E402
from packages.monitoring.rollups import evaluate_rollback  # noqa: E402


ACTIVE_ARTIFACT_ROOT = Path(os.environ.get("SPOTIBOYS_ACTIVE_ARTIFACT_ROOT", "/serving-bundle"))


def check_rollback(*, auto_rollback: bool = False, write_marker: bool = True) -> dict:
    config = load_config()
    repository, _runtime = build_repository_and_runtime(config)
    active = repository.get_active_model_version()
    previous = repository.get_previous_good_model_version()
    rollup = repository.latest_serving_metric_rollup("1h") or repository.latest_serving_metric_rollup("5m")
    if not rollup:
        decision = {
            "decision_id": "rollback_no_rollup",
            "decision_type": "rollback",
            "model_version": active.get("model_version") if active else None,
            "candidate_version": previous.get("model_version") if previous else None,
            "decision": "no_action_insufficient_sample",
            "reason": "no serving monitoring rollup is available yet",
            "metrics": {},
            "artifact_uri": previous.get("manifest_uri") if previous else None,
        }
    elif not previous:
        decision = evaluate_rollback(rollup, active_model=active, previous_model=None, auto_rollback=False)
        decision["decision"] = "no_action"
        decision["reason"] = "no previous known-good model is registered for rollback"
    else:
        decision = evaluate_rollback(rollup, active_model=active, previous_model=previous, auto_rollback=auto_rollback)

    repository.record_model_trigger_decision(decision)
    decision_path = _decision_path(config)
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if auto_rollback and write_marker and decision["decision"] == "rollback_executed" and previous:
        _stage_rollback(previous, decision)
    return decision


def _decision_path(config) -> Path:
    return config.object_storage_root / "proj23-mlflow-artifacts" / "rollback_decision.json"


def _stage_rollback(previous: dict, decision: dict) -> None:
    source = _find_bundle(previous)
    validate_serving_bundle_directory(source)
    staged_root = ACTIVE_ARTIFACT_ROOT / "Real_service" / "vm1_staged_serving"
    destination = staged_root / str(previous["serving_bundle_version"])
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    marker = staged_root / "restart_required.json"
    marker.write_text(
        json.dumps(
            {
                "status": "restart_required",
                "reason": "rollback",
                "staged_bundle": str(destination),
                "serving_bundle_version": previous["serving_bundle_version"],
                "model_version": previous["model_version"],
                "from_model_version": decision.get("model_version"),
                "to_model_version": previous["model_version"],
                "decision_id": decision["decision_id"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _find_bundle(previous: dict) -> Path:
    candidates = [
        ACTIVE_ARTIFACT_ROOT / "Real_service" / str(previous["serving_bundle_version"]),
        ACTIVE_ARTIFACT_ROOT / "Real_service" / str(previous["model_version"]),
    ]
    manifest_uri = previous.get("manifest_uri")
    if manifest_uri:
        manifest_path = Path(str(manifest_uri))
        candidates.append(manifest_path.parent if manifest_path.name == "manifest.json" else manifest_path)
    for candidate in candidates:
        if (candidate / "manifest.json").exists():
            return candidate
    raise FileNotFoundError("previous known-good serving bundle is not available locally")


def main() -> None:
    parser = argparse.ArgumentParser(description="VM1 rollback checker for online serving degradation.")
    parser.add_argument("--auto", action="store_true", help="Stage rollback and write restart marker on breach.")
    parser.add_argument("--dry-run-marker", action="store_true", help="Evaluate auto decision but do not write restart marker.")
    args = parser.parse_args()
    env_auto = os.environ.get("SPOTIBOYS_AUTO_ROLLBACK", "").lower() in {"1", "true", "yes", "on"}
    decision = check_rollback(auto_rollback=args.auto or env_auto, write_marker=not args.dry_run_marker)
    print(json.dumps(decision, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
