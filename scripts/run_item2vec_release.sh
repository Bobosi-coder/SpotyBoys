#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

if [ -f "${PROJECT_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${PROJECT_ROOT}/.env"
  set +a
fi

echo "[item2vec-release] project root: ${PROJECT_ROOT}"
echo "[item2vec-release] raw source: ${RAW_SOURCE_URI:-s3://proj23-mlflow-artifacts/data/raw/content/30music_parsed/}"
echo "[item2vec-release] dataset version: ${DATASET_VERSION:-auto}"

uv run python -m src.data_release.publish_item2vec_release "$@"

