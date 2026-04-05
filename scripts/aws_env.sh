#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
IS_SOURCED=0

if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  IS_SOURCED=1
fi

fail() {
  echo "[aws-env] ERROR: $*" >&2
  if [ "$IS_SOURCED" -eq 1 ]; then
    return 1
  fi
  exit 1
}

echo "[aws-env] Loading project environment..."

if [ ! -f "$ROOT_DIR/.env" ]; then
  fail "$ROOT_DIR/.env not found. Create .env first."
fi

set -a
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
set +a

VENV_PATH="${AWSCLI_VENV_PATH:-$HOME/awscli-venv}"
ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-${ARTIFACT_STORAGE_BUCKET:-proj23-mlflow-artifacts}}"

if [ ! -d "$VENV_PATH" ]; then
  fail "awscli venv not found at $VENV_PATH. Run: bash scripts/install_awscli.sh"
fi

# shellcheck disable=SC1090
source "$VENV_PATH/bin/activate"

if ! command -v aws >/dev/null 2>&1; then
  fail "aws not found in venv. Run: bash scripts/install_awscli.sh"
fi

if [ -z "${AWS_ACCESS_KEY_ID:-}" ] || [ -z "${AWS_SECRET_ACCESS_KEY:-}" ]; then
  echo "[aws-env] Your .env file does not have object-storage credentials yet." >&2
  echo "[aws-env] Add these lines to $ROOT_DIR/.env and try again:" >&2
  echo "AWS_ACCESS_KEY_ID=..." >&2
  echo "AWS_SECRET_ACCESS_KEY=..." >&2
  fail "AWS credentials are missing."
fi

if [ -z "${S3_ENDPOINT:-}" ]; then
  fail "S3_ENDPOINT is not set in $ROOT_DIR/.env"
fi

if [ -z "${ARTIFACT_BUCKET:-}" ]; then
  fail "ARTIFACT_BUCKET is not set in $ROOT_DIR/.env"
fi

export ARTIFACT_BUCKET
export AWS_ACCESS_KEY_ID
export AWS_SECRET_ACCESS_KEY
export S3_ENDPOINT
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore:Unverified HTTPS request}"
export AWS_PAGER="${AWS_PAGER:-}"

echo "[aws-env] AWS CLI:"
aws --version

echo "[aws-env] Testing bucket access..."
aws \
  --endpoint-url "$S3_ENDPOINT" \
  --no-verify-ssl \
  s3 ls "s3://$ARTIFACT_BUCKET/" | head -n 20

echo "[aws-env] Ready."
echo "[aws-env] Bucket: s3://$ARTIFACT_BUCKET/"
echo "[aws-env] Endpoint: $S3_ENDPOINT"
echo "[aws-env] Tip: source this script, then run aws commands directly."
