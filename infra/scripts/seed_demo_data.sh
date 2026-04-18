#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

python3 infra/scripts/generate_fixture_music.py --fixture fixtures/demo_catalog.json --output-dir .local/fixture_music --source-root data/raw/audio_previews
docker compose -f docker-compose.yml run --rm catalog-sync-worker
echo "Seeded fixture music and synchronized playable mappings."
