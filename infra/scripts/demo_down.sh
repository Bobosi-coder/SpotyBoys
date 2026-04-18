#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

if [ "${1:-gateway}" = "compose" ]; then
  docker compose -f infra/docker/docker-compose.demo.yml down
  exit 0
fi

PID_FILE="${PROJECT_ROOT}/.demo_gateway.pid"
if [ -f "${PID_FILE}" ]; then
  PID="$(cat "${PID_FILE}")"
  if kill -0 "${PID}" >/dev/null 2>&1; then
    kill "${PID}"
  fi
  rm -f "${PID_FILE}"
fi
echo "Demo gateway stopped."
