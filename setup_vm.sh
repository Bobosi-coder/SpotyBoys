#!/usr/bin/env bash
# =============================================================================
# setup_vm.sh — Spoty Boys VM Initialization Script
#
# Downloads the project's data/ and panns/ directories from Chameleon S3.
# No manual configuration required — just run this script from the project root.
#
# Usage:
#   bash setup_vm.sh
# =============================================================================

set -euo pipefail

# --------------------------------------------------------------------------- #
# S3 Configuration
# --------------------------------------------------------------------------- #
export AWS_ACCESS_KEY_ID="11580ec852704238a35acfbd65c7146a"
export AWS_SECRET_ACCESS_KEY="2759a133cae84a8e9a48c609c4dbc1b1"

S3_ENDPOINT="https://chi.tacc.chameleoncloud.org:7480"
BUCKET="proj23-mlflow-artifacts"

# Mapping of S3 prefixes to local destination paths (relative to project root)
declare -A SYNC_TARGETS=(
    ["data/"]="./data/"
    ["panns/"]="./panns/"
)

# --------------------------------------------------------------------------- #
# Utility functions
# --------------------------------------------------------------------------- #
log()  { echo "[setup_vm] $*"; }
die()  { echo "[setup_vm] ERROR: $*" >&2; exit 1; }

# --------------------------------------------------------------------------- #
# Ensure the working directory is the project root (same dir as this script)
# --------------------------------------------------------------------------- #
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"
log "Working directory: ${SCRIPT_DIR}"

# --------------------------------------------------------------------------- #
# Install awscli if not already available
# --------------------------------------------------------------------------- #
if ! command -v aws &>/dev/null; then
    log "aws CLI not found. Installing via pip..."
    pip install --quiet awscli
    log "awscli installed successfully."
else
    log "aws CLI is ready: $(aws --version 2>&1)"
fi

# --------------------------------------------------------------------------- #
# Suppress InsecureRequestWarning produced by --no-verify-ssl
# (Chameleon's Swift S3 gateway uses a self-signed certificate; this is expected)
# --------------------------------------------------------------------------- #
export PYTHONWARNINGS="ignore:Unverified HTTPS request"

# --------------------------------------------------------------------------- #
# Sync each target directory from S3
# --------------------------------------------------------------------------- #
log "========================================================"
log "Downloading assets from s3://${BUCKET}"
log "========================================================"

TOTAL=${#SYNC_TARGETS[@]}
COUNT=0

for S3_PREFIX in "${!SYNC_TARGETS[@]}"; do
    LOCAL_PATH="${SYNC_TARGETS[$S3_PREFIX]}"
    COUNT=$((COUNT + 1))
    log "[${COUNT}/${TOTAL}] s3://${BUCKET}/${S3_PREFIX}  ->  ${LOCAL_PATH}"

    # Create the local directory if it does not exist
    mkdir -p "${LOCAL_PATH}"

    # Sync from S3; only downloads new or changed files (safe to re-run)
    aws s3 sync \
        "s3://${BUCKET}/${S3_PREFIX}" \
        "${LOCAL_PATH}" \
        --endpoint-url "${S3_ENDPOINT}" \
        --no-verify-ssl \
        --no-progress

    FILE_COUNT=$(find "${LOCAL_PATH}" -type f | wc -l | tr -d ' ')
    log "Done: ${LOCAL_PATH} (${FILE_COUNT} files total)"
    echo ""
done

# --------------------------------------------------------------------------- #
# Done
# --------------------------------------------------------------------------- #
log "========================================================"
log "All assets downloaded. Environment is ready."
log "========================================================"