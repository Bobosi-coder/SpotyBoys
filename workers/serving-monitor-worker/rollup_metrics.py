from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.config import load_config
from packages.db_access.factory import build_repository_and_runtime
from packages.monitoring.rollups import compute_rollup


def main() -> None:
    args = _parse_args()
    config = load_config()
    repository, _runtime = build_repository_and_runtime(config)
    if args.once:
        summaries = run_once(repository, args.window)
        if args.print_json:
            print(json.dumps(summaries, indent=2, sort_keys=True))
        return

    interval = int(os.environ.get("SPOTIBOYS_SERVING_MONITOR_INTERVAL_SECONDS", "300"))
    while True:
        summaries = run_once(repository, args.window)
        if args.print_json:
            print(json.dumps(summaries, indent=2, sort_keys=True), flush=True)
        time.sleep(interval)


def run_once(repository, windows: List[str]) -> List[dict]:
    now = datetime.now(timezone.utc)
    summaries = []
    for window in windows:
        delta = _parse_window(window)
        window_start = now - delta
        inputs = repository.get_monitoring_inputs(window_start, now)
        rollup = compute_rollup(
            window_name=window,
            window_start=window_start,
            window_end=now,
            inputs=inputs,
        )
        repository.write_serving_metric_rollup(rollup)
        summaries.append(rollup)
    return summaries


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute SpotyBoys serving monitoring rollups.")
    parser.add_argument("--window", action="append", default=[], help="Window such as 5m, 1h, or 1d. May be repeated.")
    parser.add_argument("--print-json", action="store_true", help="Print rollups as JSON for demo evidence.")
    parser.add_argument("--once", action="store_true", default=True, help="Run once and exit.")
    parser.add_argument("--loop", dest="once", action="store_false", help="Run continuously.")
    args = parser.parse_args()
    if not args.window:
        args.window = ["5m"]
    return args


def _parse_window(value: str) -> timedelta:
    unit = value[-1].lower()
    amount = int(value[:-1])
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    raise ValueError(f"Unsupported window {value!r}; expected suffix m, h, or d")


if __name__ == "__main__":
    main()
