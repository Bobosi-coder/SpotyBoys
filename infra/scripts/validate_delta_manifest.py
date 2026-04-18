from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.shared_contracts.manifests import validate_delta_manifest  # noqa: E402


def main() -> None:
    manifest_path = Path(sys.argv[1] if len(sys.argv) > 1 else PROJECT_ROOT / "fixtures" / "delta_manifest.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_delta_manifest(payload)
    print(f"OK delta manifest: {manifest_path}")


if __name__ == "__main__":
    main()
