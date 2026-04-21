from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.config import load_config  # noqa: E402
from packages.db_access.factory import build_repository_and_runtime  # noqa: E402
from packages.monitoring.rollups import build_serving_rollup, window_bounds  # noqa: E402


def run_rollup(window: str) -> dict:
    config = load_config()
    repository, _runtime = build_repository_and_runtime(config)
    active = repository.get_active_model_version() or {}
    start, end = window_bounds(window)
    inputs = repository.get_monitoring_inputs(start, end)
    rollup = build_serving_rollup(
        inputs,
        window_name=window,
        window_start=start,
        window_end=end,
        model_version=str(active.get("model_version") or "unknown"),
    )
    repository.write_serving_metric_rollup(rollup)
    return rollup


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute derived serving monitoring rollups.")
    parser.add_argument("--window", action="append", choices=["5m", "1h"], help="Window to compute. Defaults to both.")
    parser.add_argument("--print-json", action="store_true", help="Print rollup JSON for submission evidence.")
    args = parser.parse_args()
    windows = args.window or ["5m", "1h"]
    rollups = [run_rollup(window) for window in windows]
    if args.print_json:
        print(json.dumps({"rollups": rollups}, indent=2, sort_keys=True, default=str))
    else:
        for rollup in rollups:
            print(
                f"wrote serving rollup window={rollup['window_name']} "
                f"model_version={rollup['model_version']} "
                f"requests={rollup['metrics'].get('request_count', 0)}"
            )


if __name__ == "__main__":
    main()
