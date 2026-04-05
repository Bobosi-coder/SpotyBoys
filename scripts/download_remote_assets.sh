#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Load AWS CLI venv + credentials + endpoint/bucket defaults.
# shellcheck disable=SC1091
source "$SCRIPT_DIR/aws_env.sh"

REMOTE_PREFIX="${1:-data/raw/content/30music_parsed/}"
LOCAL_PATH="${2:-$ROOT_DIR/data/raw/content/30music_parsed}"

mkdir -p "$LOCAL_PATH"

echo "[download-remote-assets] Syncing s3://$ARTIFACT_BUCKET/$REMOTE_PREFIX -> $LOCAL_PATH"
aws \
  --endpoint-url "$S3_ENDPOINT" \
  --no-verify-ssl \
  s3 sync "s3://$ARTIFACT_BUCKET/$REMOTE_PREFIX" "$LOCAL_PATH" \
  --no-progress

echo "[download-remote-assets] Done."
