#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

EXPLICIT_RECOMMEND_URL="${RECOMMEND_URL-__UNSET__}"
EXPLICIT_ONLINE_FEATURE_OUTPUT_DIR="${ONLINE_FEATURE_OUTPUT_DIR-__UNSET__}"
EXPLICIT_ONLINE_FEATURE_REQUEST_JSON="${ONLINE_FEATURE_REQUEST_JSON-__UNSET__}"
EXPLICIT_ONLINE_FEATURE_GENERATOR_LOG="${ONLINE_FEATURE_GENERATOR_LOG-__UNSET__}"

if [ -f "${PROJECT_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${PROJECT_ROOT}/.env"
  set +a
fi

if [ "${EXPLICIT_RECOMMEND_URL}" != "__UNSET__" ]; then export "RECOMMEND_URL=${EXPLICIT_RECOMMEND_URL}"; fi
if [ "${EXPLICIT_ONLINE_FEATURE_OUTPUT_DIR}" != "__UNSET__" ]; then export "ONLINE_FEATURE_OUTPUT_DIR=${EXPLICIT_ONLINE_FEATURE_OUTPUT_DIR}"; fi
if [ "${EXPLICIT_ONLINE_FEATURE_REQUEST_JSON}" != "__UNSET__" ]; then export "ONLINE_FEATURE_REQUEST_JSON=${EXPLICIT_ONLINE_FEATURE_REQUEST_JSON}"; fi
if [ "${EXPLICIT_ONLINE_FEATURE_GENERATOR_LOG}" != "__UNSET__" ]; then export "ONLINE_FEATURE_GENERATOR_LOG=${EXPLICIT_ONLINE_FEATURE_GENERATOR_LOG}"; fi

if ! command -v uv >/dev/null 2>&1 && [ -x "${HOME}/.local/bin/uv" ]; then
  export PATH="${HOME}/.local/bin:${PATH}"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "[online-feature-demo] ERROR: uv not found on PATH." >&2
  echo "[online-feature-demo] Try: export PATH=\"\$HOME/.local/bin:\$PATH\"" >&2
  exit 1
fi

echo "[online-feature-demo] project root: ${PROJECT_ROOT}"
echo "[online-feature-demo] recommend url: ${RECOMMEND_URL:-http://localhost:8001/recommend}"
echo "[online-feature-demo] request json: ${ONLINE_FEATURE_REQUEST_JSON:-auto}"
echo "[online-feature-demo] generator log: ${ONLINE_FEATURE_GENERATOR_LOG:-latest-artifacts-generator-log}"
echo "[online-feature-demo] output dir: ${ONLINE_FEATURE_OUTPUT_DIR:-artifacts/online_feature_demo}"

CMD=(uv run python -m src.features.online_feature_demo)

if [ -n "${RECOMMEND_URL:-}" ]; then
  CMD+=(--recommend-url "${RECOMMEND_URL}")
fi
if [ -n "${ONLINE_FEATURE_OUTPUT_DIR:-}" ]; then
  CMD+=(--output-dir "${ONLINE_FEATURE_OUTPUT_DIR}")
fi
if [ -n "${ONLINE_FEATURE_REQUEST_JSON:-}" ]; then
  CMD+=(--request-json "${ONLINE_FEATURE_REQUEST_JSON}")
fi
if [ -n "${ONLINE_FEATURE_GENERATOR_LOG:-}" ]; then
  CMD+=(--generator-log "${ONLINE_FEATURE_GENERATOR_LOG}")
fi

CMD+=("$@")
"${CMD[@]}"
