#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# load .env
if [ -f "${PROJECT_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${PROJECT_ROOT}/.env"
  set +a
else
  echo "[aws-env] .env not found at ${PROJECT_ROOT}/.env" >&2
  exit 1
fi

# aws cli path
if [ -n "${AWS_CLI_PATH:-}" ]; then
  AWS_BIN="${AWS_CLI_PATH}"
elif [ -f "${PROJECT_ROOT}/.venv/bin/aws" ]; then
  AWS_BIN="${PROJECT_ROOT}/.venv/bin/aws"
else
  AWS_BIN="${PROJECT_ROOT}/.venv/Scripts/aws.exe"
fi

# export credentials
export AWS_ACCESS_KEY_ID
export AWS_SECRET_ACCESS_KEY
export S3_ENDPOINT="${S3_ENDPOINT:-https://chi.tacc.chameleoncloud.org:7480}"

echo "[aws-env] Ready to use object storage"

# test connection
"${AWS_BIN}" s3 ls \
  --endpoint-url "${S3_ENDPOINT}" \
  --no-verify-ssl
