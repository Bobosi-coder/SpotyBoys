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

DB_NAME="${DB_NAME:-spotiboys}"
DB_USER="${DB_USER:-postgres}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
RUN_INDEXES="${RUN_INDEXES:-false}"

if [ -n "${DB_PASSWORD:-}" ] && [ -z "${PGPASSWORD:-}" ]; then
  export PGPASSWORD="${DB_PASSWORD}"
fi

echo "----------------------------------------"
echo "Spotiboys DB initialization"
echo "DB_NAME      = ${DB_NAME}"
echo "DB_USER      = ${DB_USER}"
echo "DB_HOST      = ${DB_HOST}"
echo "DB_PORT      = ${DB_PORT}"
echo "RUN_INDEXES  = ${RUN_INDEXES}"
echo "PROJECT_ROOT = ${PROJECT_ROOT}"
echo "----------------------------------------"

# --------------------------------------------------
# 1. Create database if it does not exist
# --------------------------------------------------
DB_EXISTS=$(psql \
  -U "${DB_USER}" \
  -h "${DB_HOST}" \
  -p "${DB_PORT}" \
  -d postgres \
  -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}';")

if [ "${DB_EXISTS}" != "1" ]; then
  echo "[1/3] Creating database '${DB_NAME}'..."
  createdb \
    -U "${DB_USER}" \
    -h "${DB_HOST}" \
    -p "${DB_PORT}" \
    "${DB_NAME}"
else
  echo "[1/3] Database '${DB_NAME}' already exists. Skipping create."
fi

# --------------------------------------------------
# 2. Run base schema
# --------------------------------------------------
echo "[2/3] Running db/001_init.sql ..."
psql \
  -v ON_ERROR_STOP=1 \
  -U "${DB_USER}" \
  -h "${DB_HOST}" \
  -p "${DB_PORT}" \
  -d "${DB_NAME}" \
  -f "${PROJECT_ROOT}/db/001_init.sql"

# --------------------------------------------------
# 3. Optionally run indexes
# --------------------------------------------------
if [ "${RUN_INDEXES}" = "true" ]; then
  echo "[3/3] Running db/002_indexes.sql ..."
  psql \
    -v ON_ERROR_STOP=1 \
    -U "${DB_USER}" \
    -h "${DB_HOST}" \
    -p "${DB_PORT}" \
    -d "${DB_NAME}" \
    -f "${PROJECT_ROOT}/db/002_indexes.sql"
else
  echo "[3/3] Skipping indexes for now."
  echo "      Recommended flow for large raw ingest:"
  echo "      1) init schema only"
  echo "      2) load raw CSV data"
  echo "      3) run indexes after ingest"
fi

echo "----------------------------------------"
echo "DB initialization complete."
echo "----------------------------------------"
