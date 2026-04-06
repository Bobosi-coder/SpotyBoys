#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

EXPLICIT_DATASET_VERSION="${DATASET_VERSION-__UNSET__}"
EXPLICIT_FEATURE_VERSION="${FEATURE_VERSION-__UNSET__}"
EXPLICIT_RANKER_VERSION="${RANKER_VERSION-__UNSET__}"
EXPLICIT_MLFLOW_TRACKING_URI="${MLFLOW_TRACKING_URI-__UNSET__}"
EXPLICIT_AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID-__UNSET__}"
EXPLICIT_AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY-__UNSET__}"
EXPLICIT_AWS_SESSION_TOKEN="${AWS_SESSION_TOKEN-__UNSET__}"
EXPLICIT_S3_ENDPOINT="${S3_ENDPOINT-__UNSET__}"
EXPLICIT_ARTIFACT_BUCKET="${ARTIFACT_BUCKET-__UNSET__}"
EXPLICIT_ARTIFACT_STORAGE_BUCKET="${ARTIFACT_STORAGE_BUCKET-__UNSET__}"
EXPLICIT_DATA_RELEASE_BUCKET="${DATA_RELEASE_BUCKET-__UNSET__}"
EXPLICIT_RANKER_RELEASE_PREFIX="${RANKER_RELEASE_PREFIX-__UNSET__}"

if [ -f "${PROJECT_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${PROJECT_ROOT}/.env"
  set +a
fi

if [ "${EXPLICIT_DATASET_VERSION}" != "__UNSET__" ]; then export "DATASET_VERSION=${EXPLICIT_DATASET_VERSION}"; fi
if [ "${EXPLICIT_FEATURE_VERSION}" != "__UNSET__" ]; then export "FEATURE_VERSION=${EXPLICIT_FEATURE_VERSION}"; fi
if [ "${EXPLICIT_RANKER_VERSION}" != "__UNSET__" ]; then export "RANKER_VERSION=${EXPLICIT_RANKER_VERSION}"; fi
if [ "${EXPLICIT_MLFLOW_TRACKING_URI}" != "__UNSET__" ]; then export "MLFLOW_TRACKING_URI=${EXPLICIT_MLFLOW_TRACKING_URI}"; fi
if [ "${EXPLICIT_AWS_ACCESS_KEY_ID}" != "__UNSET__" ]; then export "AWS_ACCESS_KEY_ID=${EXPLICIT_AWS_ACCESS_KEY_ID}"; fi
if [ "${EXPLICIT_AWS_SECRET_ACCESS_KEY}" != "__UNSET__" ]; then export "AWS_SECRET_ACCESS_KEY=${EXPLICIT_AWS_SECRET_ACCESS_KEY}"; fi
if [ "${EXPLICIT_AWS_SESSION_TOKEN}" != "__UNSET__" ]; then export "AWS_SESSION_TOKEN=${EXPLICIT_AWS_SESSION_TOKEN}"; fi
if [ "${EXPLICIT_S3_ENDPOINT}" != "__UNSET__" ]; then export "S3_ENDPOINT=${EXPLICIT_S3_ENDPOINT}"; fi
if [ "${EXPLICIT_ARTIFACT_BUCKET}" != "__UNSET__" ]; then export "ARTIFACT_BUCKET=${EXPLICIT_ARTIFACT_BUCKET}"; fi
if [ "${EXPLICIT_ARTIFACT_STORAGE_BUCKET}" != "__UNSET__" ]; then export "ARTIFACT_STORAGE_BUCKET=${EXPLICIT_ARTIFACT_STORAGE_BUCKET}"; fi
if [ "${EXPLICIT_DATA_RELEASE_BUCKET}" != "__UNSET__" ]; then export "DATA_RELEASE_BUCKET=${EXPLICIT_DATA_RELEASE_BUCKET}"; fi
if [ "${EXPLICIT_RANKER_RELEASE_PREFIX}" != "__UNSET__" ]; then export "RANKER_RELEASE_PREFIX=${EXPLICIT_RANKER_RELEASE_PREFIX}"; fi

if ! command -v uv >/dev/null 2>&1 && [ -x "${HOME}/.local/bin/uv" ]; then
  export PATH="${HOME}/.local/bin:${PATH}"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "[ranker-release] ERROR: uv not found on PATH." >&2
  echo "[ranker-release] Try: export PATH=\"\$HOME/.local/bin:\$PATH\"" >&2
  exit 1
fi

echo "[ranker-release] project root: ${PROJECT_ROOT}"
echo "[ranker-release] ranker version: ${RANKER_VERSION:-auto}"
echo "[ranker-release] upstream processed dataset: ${DATASET_VERSION:-local}"
echo "[ranker-release] upstream retriever features: ${FEATURE_VERSION:-local}"

uv run python -m src.data_release.publish_ranker_release "$@"
