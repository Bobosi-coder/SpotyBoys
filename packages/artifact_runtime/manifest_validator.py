from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from packages.shared_contracts.manifests import validate_serving_bundle_manifest


def validate_serving_bundle(manifest_path: str | Path) -> Mapping[str, object]:
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    validate_serving_bundle_manifest(payload)
    return payload

