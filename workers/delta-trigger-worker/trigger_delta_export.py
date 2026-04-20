from __future__ import annotations

import logging
import sys
import json
from pathlib import Path
from typing import Optional

from packages.airflow_trigger import trigger_airflow_retrain
from packages.config import load_config
from packages.db_access.postgres import PostgresRepository

logger = logging.getLogger(__name__)


def check_and_trigger_delta_export(threshold: int = 1000) -> Optional[str]:
    """
    Check if new sessions count >= threshold, trigger export if so.

    Runs hourly to check if enough new sessions have accumulated.
    If threshold is reached, exports delta and records checkpoint.

    Args:
        threshold: Number of new sessions before triggering export (default: 1000)

    Returns:
        Path to exported delta directory if export was triggered, None otherwise

    Raises:
        RuntimeError: If export fails at any step
    """
    config = load_config()
    repo = PostgresRepository(config.database_url)

    try:
        # Check new sessions count
        new_sessions_count = repo.count_new_sessions_since_checkpoint()
        logger.info(f"New sessions since checkpoint: {new_sessions_count}")

        if new_sessions_count < threshold:
            logger.info(f"Threshold {threshold} not reached, skipping export. Need {threshold - new_sessions_count} more.")
            return None

        logger.info(f"Threshold {threshold} reached with {new_sessions_count} new sessions. Triggering delta export.")

        import importlib.util
        from pathlib import Path
        _spec = importlib.util.spec_from_file_location(
            "export_delta",
            Path(__file__).parent.parent / "parser-export-worker" / "export_delta.py",
        )
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        export_delta = _mod.export_delta

        result = export_delta()
        logger.info(f"Delta export completed: {result}")

        quality_report_path = Path(result) / "quality_report.json"
        if quality_report_path.exists():
            quality_report = json.loads(quality_report_path.read_text(encoding="utf-8"))
            if quality_report.get("status") == "fail":
                logger.warning(
                    "Delta export quality gate failed; retraining will not be triggered. "
                    f"notes={quality_report.get('notes', [])}"
                )
                return str(result)
        else:
            logger.warning("Delta export quality report missing; retraining will not be triggered.")
            return str(result)

        try:
            dag_run = trigger_airflow_retrain()
            logger.info(f"Retraining triggered: dag_run_id={dag_run.get('dag_run_id')}")
        except Exception as airflow_exc:
            logger.warning(f"Delta export succeeded but Airflow trigger failed: {airflow_exc}")

        return str(result)

    except Exception as exc:
        logger.error(f"Delta export failed: {exc}", exc_info=True)
        raise


if __name__ == "__main__":
    import time

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    logger.info("Delta trigger worker started (hourly loop)")
    while True:
        try:
            result = check_and_trigger_delta_export()
            if result:
                logger.info(f"Export + retrain triggered: {result}")
            else:
                logger.info("Threshold not reached, sleeping 1h")
        except Exception as exc:
            logger.error(f"Error in delta trigger cycle: {exc}", exc_info=True)
        time.sleep(3600)
