from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.artifact_runtime import validate_serving_bundle  # noqa: E402


def main() -> None:
    manifest_path = Path(sys.argv[1] if len(sys.argv) > 1 else PROJECT_ROOT / "fixtures" / "serving_bundle_manifest.json")
    validate_serving_bundle(manifest_path)
    print(f"OK serving bundle manifest: {manifest_path}")


if __name__ == "__main__":
    main()
