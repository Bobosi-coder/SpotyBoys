from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Mapping

from packages.shared_contracts.manifests import validate_serving_bundle_manifest


def validate_serving_bundle(manifest_path: str | Path) -> Mapping[str, object]:
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    validate_serving_bundle_manifest(payload)
    return payload


def validate_serving_bundle_directory(bundle_path: str | Path) -> Dict[str, object]:
    bundle = Path(bundle_path)
    manifest_path = bundle / "manifest.json"
    payload = dict(validate_serving_bundle(manifest_path))
    missing = [name for name in payload.get("artifacts", []) if not (bundle / str(name)).exists()]
    if missing:
        raise ValueError("serving bundle missing artifact files: " + ", ".join(sorted(str(item) for item in missing)))
    return payload
