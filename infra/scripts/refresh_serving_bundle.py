from __future__ import annotations

import importlib.util
from pathlib import Path


def refresh_serving_bundle() -> Path:
    """Fetch the newest Real_service bundle from object storage and stage it locally."""
    script_path = Path(__file__).resolve().parents[2] / "workers" / "artifact-fetch-worker" / "fetch_remote_artifacts.py"
    spec = importlib.util.spec_from_file_location("spotiboys_artifact_fetch_worker", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load artifact fetch worker from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.fetch_remote_artifacts()


if __name__ == "__main__":
    print(refresh_serving_bundle())
