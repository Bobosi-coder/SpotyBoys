#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# load .env
set -a
. "${SCRIPT_DIR}/.env"
set +a

# aws cli path
AWS_BIN="${SCRIPT_DIR}/.venv/bin/aws"

# export credentials
export AWS_ACCESS_KEY_ID
export AWS_SECRET_ACCESS_KEY
export S3_ENDPOINT="https://chi.tacc.chameleoncloud.org:7480"

echo "[aws-env] Ready to use object storage"

# test connection
"${AWS_BIN}" s3 ls \
  --endpoint-url "${S3_ENDPOINT}" \
  --no-verify-ssl