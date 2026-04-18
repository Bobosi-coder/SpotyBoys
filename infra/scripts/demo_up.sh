#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

MODE="${1:-gateway}"

if [ "${MODE}" = "compose" ]; then
  docker compose -f infra/docker/docker-compose.demo.yml up --build -d
  echo "Frontend: http://127.0.0.1:5173/"
  echo "Recommendation API: http://127.0.0.1:8001/health"
  echo "Event API: http://127.0.0.1:8002/health"
  exit 0
fi

PID_FILE="${PROJECT_ROOT}/.demo_gateway.pid"
LOG_FILE="${PROJECT_ROOT}/.demo_gateway.log"

if [ -f "${PID_FILE}" ] && kill -0 "$(cat "${PID_FILE}")" >/dev/null 2>&1; then
  echo "Demo gateway already running: http://127.0.0.1:5173/?gateway=1"
  exit 0
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
DEMO_HOST="${DEMO_HOST:-127.0.0.1}" DEMO_PORT="${DEMO_PORT:-5173}" \
  nohup "${PYTHON_BIN}" infra/scripts/run_demo_gateway.py >"${LOG_FILE}" 2>&1 &
echo "$!" >"${PID_FILE}"

sleep 1
echo "Demo gateway: http://127.0.0.1:5173/?gateway=1"
echo "Health: http://127.0.0.1:5173/health"
echo "Log: ${LOG_FILE}"
