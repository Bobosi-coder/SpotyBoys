from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.config import load_config
from packages.db_access.factory import build_repository_and_runtime
from packages.monitoring.rollups import evaluate_rollback


def main() -> None:
    args = _parse_args()
    config = load_config()
    repository, _runtime = build_repository_and_runtime(config)
    decision = run_check(repository, args.window)
    if args.print_json:
        print(json.dumps(decision, indent=2, sort_keys=True))


def run_check(repository, window_name: str = "5m") -> dict:
    active = repository.get_active_model_version()
    previous = repository.get_previous_good_model_version()
    rollup = repository.latest_serving_metric_rollup(window_name) or {
        "window_name": window_name,
        "model_version": (active or {}).get("model_version") or "unknown",
        "request_count": 0,
        "sample_status": "insufficient",
    }
    decision = evaluate_rollback(rollup, active_model=active, previous_model=previous)
    auto_rollback = os.environ.get("SPOTIBOYS_AUTO_ROLLBACK", "false").strip().lower() in {"1", "true", "yes", "on"}
    if auto_rollback and decision["decision"] == "recommend_rollback" and previous:
        decision["executed"] = False
        decision["execution_note"] = "auto rollback requested, but this implementation leaves bundle mutation manual for safety"
    else:
        decision["executed"] = False
        decision["execution_note"] = "manual recommendation only; SPOTIBOYS_AUTO_ROLLBACK is disabled or unsafe"
    decision.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    repository.record_model_trigger_decision(decision)
    return decision


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate whether SpotyBoys serving should be rolled back.")
    parser.add_argument("--window", default="5m", help="Rollup window to evaluate, default 5m.")
    parser.add_argument("--print-json", action="store_true", help="Print decision JSON for demo evidence.")
    return parser.parse_args()


if __name__ == "__main__":
    main()
