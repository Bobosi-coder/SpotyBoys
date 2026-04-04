#!/usr/bin/env bash
# =============================================================================
# setup_vm.sh — Spoty Boys VM Initialization Script
#
# Steps:
#   1. Install uv (Python package manager) if not already present
#   2. Run `uv sync` to install all project dependencies
#   3. Install awscli into the project virtualenv if not already present
#   4. Download data/ and panns/ from Chameleon S3
#
# No manual configuration required — just run this script from the project root.
#
# Usage:
#   bash setup_vm.sh
# =============================================================================

set -euo pipefail

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
# Step 1: Install uv if not already available
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 1: Setting up uv"
log "========================================================"

if ! command -v uv &>/dev/null; then
    log "uv not found. Installing via official installer..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Make uv available in the current shell session immediately after install
    export PATH="${HOME}/.local/bin:${PATH}"
    log "uv installed: $(uv --version)"
else
    log "uv is already installed: $(uv --version)"
fi

echo ""

# --------------------------------------------------------------------------- #
# Step 2: Sync project dependencies via uv
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 2: Installing project dependencies (uv sync)"
log "========================================================"

if [[ ! -f "pyproject.toml" ]]; then
    die "pyproject.toml not found. Make sure you are running this script from the project root."
fi

uv sync
log "Dependencies installed successfully."
echo ""

# --------------------------------------------------------------------------- #
# Step 3: Install awscli into the project virtualenv
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 3: Setting up aws CLI"
log "========================================================"

# uv creates the virtualenv at .venv/ — use the binary directly from there
# so we never rely on the venv being activated in the current shell session
AWS_BIN="${SCRIPT_DIR}/.venv/bin/aws"

if [[ ! -f "${AWS_BIN}" ]]; then
    log "aws CLI not found in .venv. Installing via uv pip..."
    uv pip install awscli
    log "awscli installed successfully."
else
    log "aws CLI is already installed: $("${AWS_BIN}" --version 2>&1)"
fi

echo ""

# --------------------------------------------------------------------------- #
# Step 4: Download data/ and panns/ from Chameleon S3
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 4: Downloading assets from S3"
log "========================================================"

# S3 credentials and endpoint
export AWS_ACCESS_KEY_ID="11580ec852704238a35acfbd65c7146a"
export AWS_SECRET_ACCESS_KEY="2759a133cae84a8e9a48c609c4dbc1b1"

S3_ENDPOINT="https://chi.tacc.chameleoncloud.org:7480"
BUCKET="proj23-mlflow-artifacts"

# Mapping of S3 prefixes to local destination paths (relative to project root)
declare -A SYNC_TARGETS=(
    ["data/"]="./data/"
    ["panns/"]="./panns/"
)

# Suppress InsecureRequestWarning produced by --no-verify-ssl
# (Chameleon's Swift S3 gateway uses a self-signed certificate; this is expected)
export PYTHONWARNINGS="ignore:Unverified HTTPS request"

TOTAL=${#SYNC_TARGETS[@]}
COUNT=0

for S3_PREFIX in "${!SYNC_TARGETS[@]}"; do
    LOCAL_PATH="${SYNC_TARGETS[$S3_PREFIX]}"
    COUNT=$((COUNT + 1))
    log "[${COUNT}/${TOTAL}] s3://${BUCKET}/${S3_PREFIX}  ->  ${LOCAL_PATH}"

    # Create the local directory if it does not exist
    mkdir -p "${LOCAL_PATH}"

    # Use the full path to aws binary inside .venv to avoid PATH issues
    "${AWS_BIN}" s3 sync \
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
# All done
# --------------------------------------------------------------------------- #
log "========================================================"
log "VM setup complete. Environment is ready."
log "========================================================"