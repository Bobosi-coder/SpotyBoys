#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "[aws-env] Loading project environment..."

if [ ! -f "$ROOT_DIR/.env" ]; then
  echo "[aws-env] ERROR: $ROOT_DIR/.env not found"
  echo "[aws-env] Create .env first."
  return 1 2>/dev/null || exit 1
fi

set -a
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
set +a

VENV_PATH="${AWSCLI_VENV_PATH:-$HOME/awscli-venv}"
ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-${ARTIFACT_STORAGE_BUCKET:-proj23-mlflow-artifacts}}"

if [ ! -d "$VENV_PATH" ]; then
  echo "[aws-env] ERROR: awscli venv not found at $VENV_PATH"
  echo "[aws-env] Run: bash scripts/install_awscli.sh"
  return 1 2>/dev/null || exit 1
fi

# shellcheck disable=SC1090
source "$VENV_PATH/bin/activate"

if ! command -v aws >/dev/null 2>&1; then
  echo "[aws-env] ERROR: aws not found in venv"
  echo "[aws-env] Run: bash scripts/install_awscli.sh"
  return 1 2>/dev/null || exit 1
fi

: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is not set}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is not set}"
: "${S3_ENDPOINT:?S3_ENDPOINT is not set}"
: "${ARTIFACT_BUCKET:?ARTIFACT_BUCKET is not set}"

export ARTIFACT_BUCKET
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
