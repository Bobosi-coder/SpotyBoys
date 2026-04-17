#!/usr/bin/env bash
# =============================================================================
# upload_results.sh — 训练完成后将 artifacts 上传至 S3
#
# 在 MLflow 中确认最优 run 后手动执行，将 ranker 模型 + retriever artifacts
# 写入 Real_service/{VERSION}/ 并生成 manifest.json。
#
# Usage:
#   bash upload_results.sh --version 20260417_143022 \
#                          --retrieve-version 20260417_051148
#
# 如果 retrain.sh 刚运行过，VERSION 保存在 VERSION.txt:
#   VERSION=$(cat VERSION.txt)
#   bash upload_results.sh --version $VERSION --retrieve-version $VERSION
#
# 环境变量:
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
#   AWS_ENDPOINT_URL  (默认 https://chi.tacc.chameleoncloud.org:7480)
# =============================================================================

set -euo pipefail

BUCKET="proj23-mlflow-artifacts"
ENDPOINT="${AWS_ENDPOINT_URL:-https://chi.tacc.chameleoncloud.org:7480}"
EP="--endpoint-url ${ENDPOINT} --no-verify-ssl --no-progress"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# --------------------------------------------------------------------------- #
# 解析参数
# --------------------------------------------------------------------------- #
VERSION=""
RETRIEVE_VERSION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)          VERSION="$2";          shift 2 ;;
        --retrieve-version) RETRIEVE_VERSION="$2"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

[[ -n "${VERSION}" ]]          || die "Must specify --version VERSION"
[[ -n "${RETRIEVE_VERSION}" ]] || die "Must specify --retrieve-version VERSION"

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-11580ec852704238a35acfbd65c7146a}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-2759a133cae84a8e9a48c609c4dbc1b1}"

DST="s3://${BUCKET}/Real_service/${VERSION}"

log "========================================================"
log "Uploading to Real_service/${VERSION}/"
log "Using retriever: Retrieve/${RETRIEVE_VERSION}/"
log "========================================================"

# --------------------------------------------------------------------------- #
# Ranker model
# --------------------------------------------------------------------------- #
log "--- Ranker model ---"
[[ -f "artifacts/ranker/gru_ranker.pt" ]] \
    || die "artifacts/ranker/gru_ranker.pt not found — did training complete?"

aws ${EP} s3 cp artifacts/ranker/gru_ranker.pt          "${DST}/gru_ranker.pt"
aws ${EP} s3 cp artifacts/ranker/gru_ranker_config.json "${DST}/gru_ranker_config.json"
log "OK: gru_ranker.pt + gru_ranker_config.json"

# --------------------------------------------------------------------------- #
# Retriever artifacts (from Retrieve/{RETRIEVE_VERSION}/ → Real_service/{VERSION}/)
# --------------------------------------------------------------------------- #
log "--- Retriever artifacts (copy from Retrieve/${RETRIEVE_VERSION}/) ---"

for key in cooc_session.npz cooc_playlist.npz user_centroids.pkl pop_scores.csv; do
    aws ${EP} s3 cp \
        "s3://${BUCKET}/Retrieve/${RETRIEVE_VERSION}/${key}" \
        "${DST}/${key}"
    log "OK: ${key}"
done

# --------------------------------------------------------------------------- #
# manifest.json
# --------------------------------------------------------------------------- #
log "--- Writing manifest.json ---"

GIT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")

cat <<EOF | aws ${EP} s3 cp - "${DST}/manifest.json"
{
  "version": "${VERSION}",
  "retrieve_version": "${RETRIEVE_VERSION}",
  "git_sha": "${GIT_SHA}",
  "files": [
    "gru_ranker.pt",
    "gru_ranker_config.json",
    "cooc_session.npz",
    "cooc_playlist.npz",
    "user_centroids.pkl",
    "pop_scores.csv"
  ]
}
EOF
log "OK: manifest.json"

echo ""
log "========================================================"
log "Upload complete!"
log "  Real_service path: s3://${BUCKET}/Real_service/${VERSION}/"
log "  Verify:  aws --endpoint-url ${ENDPOINT} --no-verify-ssl s3 ls s3://${BUCKET}/Real_service/${VERSION}/"
log "========================================================"
