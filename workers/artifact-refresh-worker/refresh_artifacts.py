from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from packages.artifact_runtime import validate_serving_bundle_directory
from packages.config import load_config


ACTIVE_ARTIFACT_ROOT = Path(os.environ.get("SPOTIBOYS_ACTIVE_ARTIFACT_ROOT", "/serving-bundle"))


def refresh_artifacts(version: Optional[str] = None) -> Path:
    """Stage a manifest-confirmed Real_service bundle and mark restart activation.

    This worker intentionally does not hot-swap the running model. It validates
    the promoted bundle in the object-storage layout, copies it into a local
    staged area, and writes a restart marker that deployment automation can use
    to restart the recommendation API.
    """
    config = load_config()
    real_service_root = config.object_storage_root / "proj23-mlflow-artifacts" / "Real_service"
    source = _select_bundle(real_service_root, version)
    manifest = validate_serving_bundle_directory(source)
    staged_root = ACTIVE_ARTIFACT_ROOT / "Real_service" / "vm1_staged_serving"
    destination = staged_root / str(manifest["version"])
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    marker = staged_root / "restart_required.json"
    marker.write_text(
        json.dumps(
            {
                "status": "restart_required",
                "staged_bundle": str(destination),
                "serving_bundle_version": manifest["version"],
                "model_version": manifest["model_version"],
                "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def _select_bundle(root: Path, version: Optional[str]) -> Path:
    if version:
        candidate = root / version
        if not candidate.exists():
            raise FileNotFoundError(f"promoted serving bundle not found: {candidate}")
        return candidate
    candidates = sorted(path for path in root.iterdir() if path.is_dir() and (path / "manifest.json").exists())
    if not candidates:
        raise FileNotFoundError(f"no promoted serving bundles found below {root}")
    return candidates[-1]


if __name__ == "__main__":
    print(refresh_artifacts())
