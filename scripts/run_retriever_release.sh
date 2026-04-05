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

echo "[retriever-release] project root: ${PROJECT_ROOT}"
echo "[retriever-release] feature version: ${FEATURE_VERSION:-auto}"
echo "[retriever-release] upstream processed dataset: ${DATASET_VERSION:-local}"

uv run python -m src.data_release.publish_retriever_release "$@"
