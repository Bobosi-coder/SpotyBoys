from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def check_and_restart() -> Optional[Dict[str, Any]]:
    """
    Check for restart marker and restart recommendation-api if needed.

    Runs every minute to check if a restart_required.json marker file exists.
    If it does:
    1. Extract version info from marker
    2. Execute docker-compose restart
    3. Delete marker file
    4. Log the action

    Returns:
        Dict with restart info if restart was performed, None otherwise

    Raises:
        RuntimeError: If restart command fails
    """
    # Marker location (must match artifact-refresh-worker output)
    active_root = Path(os.environ.get("SPOTIBOYS_ACTIVE_ARTIFACT_ROOT", "/serving-bundle"))
    marker_path = active_root / "Real_service" / "vm1_staged_serving" / "restart_required.json"

    # Check if marker exists
    if not marker_path.exists():
        logger.debug(f"No restart marker found at {marker_path}")
        return None

    try:
        # Read marker to get version info
        marker_data = json.loads(marker_path.read_text(encoding="utf-8"))
        version = marker_data.get("serving_bundle_version", "unknown")
        model_version = marker_data.get("model_version", "unknown")

        logger.info(f"🔄 Restart marker detected!")
        logger.info(f"   Model version: {model_version}")
        logger.info(f"   Serving bundle: {version}")

        # Execute docker restart via socket-mounted Docker CLI
        container = os.environ.get(
            "RECOMMENDATION_API_CONTAINER", "spotyboys_service-recommendation-api-1"
        )
        logger.info(f"   Executing: docker restart {container}")
        result = subprocess.run(
            ["docker", "restart", container],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout
            raise RuntimeError(f"docker compose restart failed: {error_msg}")

        logger.info("   ✅ Restart command executed successfully")

        # Delete marker file to prevent re-restart
        marker_path.unlink()
        logger.info("   ✅ Marker file deleted")

        # Log restart completion
        restart_info = {
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model_version": model_version,
            "serving_bundle_version": version,
        }
        logger.info(f"✅ Restart completed: {restart_info}")

        return restart_info

    except FileNotFoundError:
        logger.error(f"Marker file disappeared: {marker_path}")
        return None
    except json.JSONDecodeError as exc:
        logger.error(f"Invalid marker JSON: {exc}")
        return None
    except subprocess.TimeoutExpired:
        logger.error("docker compose restart timed out (60s)")
        raise RuntimeError("docker compose restart timeout") from None
    except Exception as exc:
        logger.error(f"Restart check failed: {exc}", exc_info=True)
        raise RuntimeError(f"Restart check failed: {exc}") from exc


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] restart-monitor: %(message)s"
    )

    try:
        result = check_and_restart()
        if result:
            logger.info("Restart performed successfully")
            sys.exit(0)
        else:
            logger.debug("No restart needed")
            sys.exit(0)
    except Exception as exc:
        logger.error(f"Fatal error: {exc}", exc_info=True)
        sys.exit(1)
