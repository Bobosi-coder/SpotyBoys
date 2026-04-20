from __future__ import annotations

import logging
import sys
from typing import Optional

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
        return str(result)

    except Exception as exc:
        logger.error(f"Delta export failed: {exc}", exc_info=True)
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    try:
        result = check_and_trigger_delta_export()
        if result:
            print(f"Export completed: {result}")
            sys.exit(0)
        else:
            print("Threshold not reached, no export performed")
            sys.exit(0)
    except Exception as exc:
        logger.error(f"Fatal error: {exc}", exc_info=True)
        sys.exit(1)
