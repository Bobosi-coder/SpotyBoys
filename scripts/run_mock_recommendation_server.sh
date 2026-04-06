#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

EXPLICIT_SERVICE_HOST="${SERVICE_HOST-__UNSET__}"
EXPLICIT_SERVICE_PORT="${SERVICE_PORT-__UNSET__}"
EXPLICIT_SERVICE_LOG_DIR="${SERVICE_LOG_DIR-__UNSET__}"

if [ -f "${PROJECT_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${PROJECT_ROOT}/.env"
  set +a
fi

if [ "${EXPLICIT_SERVICE_HOST}" != "__UNSET__" ]; then export "SERVICE_HOST=${EXPLICIT_SERVICE_HOST}"; fi
if [ "${EXPLICIT_SERVICE_PORT}" != "__UNSET__" ]; then export "SERVICE_PORT=${EXPLICIT_SERVICE_PORT}"; fi
if [ "${EXPLICIT_SERVICE_LOG_DIR}" != "__UNSET__" ]; then export "SERVICE_LOG_DIR=${EXPLICIT_SERVICE_LOG_DIR}"; fi

if ! command -v uv >/dev/null 2>&1 && [ -x "${HOME}/.local/bin/uv" ]; then
  export PATH="${HOME}/.local/bin:${PATH}"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "[mock-service] ERROR: uv not found on PATH." >&2
  echo "[mock-service] Try: export PATH=\"\$HOME/.local/bin:\$PATH\"" >&2
  exit 1
fi

echo "[mock-service] project root: ${PROJECT_ROOT}"
echo "[mock-service] host: ${SERVICE_HOST:-127.0.0.1}"
echo "[mock-service] port: ${SERVICE_PORT:-8000}"
echo "[mock-service] logs: ${SERVICE_LOG_DIR:-artifacts/mock_service}"

CMD=(
  uv run python -m src.service.mock_recommendation_server
  --host "${SERVICE_HOST:-127.0.0.1}"
  --port "${SERVICE_PORT:-8000}"
  --output-dir "${SERVICE_LOG_DIR:-artifacts/mock_service}"
)

CMD+=("$@")
"${CMD[@]}"
