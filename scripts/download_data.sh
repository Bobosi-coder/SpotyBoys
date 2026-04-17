#!/usr/bin/env bash
# =============================================================================
# scripts/download_data.sh — S3からアーティファクトをダウンロード
#
# Usage:
#   bash scripts/download_data.sh --no-delta --retrieve-version 20260417_051148
#   bash scripts/download_data.sh --with-delta
#
# 环境变量 (docker-compose.yml 或手动设置):
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
#   AWS_ENDPOINT_URL  (默认 https://chi.tacc.chameleoncloud.org:7480)
# =============================================================================

set -euo pipefail

BUCKET="proj23-mlflow-artifacts"
ENDPOINT="${AWS_ENDPOINT_URL:-https://chi.tacc.chameleoncloud.org:7480}"
EP="--endpoint-url ${ENDPOINT} --no-verify-ssl --no-progress"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }

# --------------------------------------------------------------------------- #
# 解析参数
# --------------------------------------------------------------------------- #
MODE=""
RETRIEVE_VERSION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-delta)    MODE="no-delta";    shift ;;
        --with-delta)  MODE="with-delta";  shift ;;
        --retrieve-version) RETRIEVE_VERSION="$2"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

[[ -n "${MODE}" ]] || die "Must specify --no-delta or --with-delta"
if [[ "${MODE}" == "no-delta" && -z "${RETRIEVE_VERSION}" ]]; then
    die "--no-delta requires --retrieve-version VERSION"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "${SCRIPT_DIR}")"
cd "${ROOT_DIR}"

# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
s3_download() {
    local src="$1" dst="$2"
    if [[ -f "${dst}" ]]; then
        log "SKIP (exists): ${dst}"
        return
    fi
    mkdir -p "$(dirname "${dst}")"
    log "Downloading: s3://${BUCKET}/${src} → ${dst}"
    aws ${EP} s3 cp "s3://${BUCKET}/${src}" "${dst}"
    log "OK: ${dst}  ($(du -sh "${dst}" | cut -f1))"
}

# --------------------------------------------------------------------------- #
# Step 1: Item2vec 模型文件 (frozen)
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 1: Item2vec artifacts"
log "========================================================"

for f in item2vec_128d.npy item2vec_track_to_row.json item2vec_catalog.csv \
          item2vec_corpus.parquet playlist_tracks_i2v.parquet playlist_meta_i2v.parquet \
          item2vec_model.bin; do
    s3_download "Item2vec/${f}" "artifacts/item2vec/${f}"
done

# --------------------------------------------------------------------------- #
# Step 2: session_event/snapshot (rename love/users on download)
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 2: session_event snapshot"
log "========================================================"

s3_download "session_event/snapshot/session_tracks_i2v.parquet" "artifacts/item2vec/session_tracks_i2v.parquet"
s3_download "session_event/snapshot/session_meta_i2v.parquet"   "artifacts/item2vec/session_meta_i2v.parquet"
s3_download "session_event/snapshot/love_i2v.parquet"           "artifacts/item2vec/love_filtered_i2v.parquet"
s3_download "session_event/snapshot/users_i2v.parquet"          "artifacts/item2vec/users_filtered_i2v.parquet"

# --------------------------------------------------------------------------- #
# Step 3a (no-delta): 下载现有 Retrieve/{VERSION}
# --------------------------------------------------------------------------- #
if [[ "${MODE}" == "no-delta" ]]; then
    log "========================================================"
    log "Step 3: Retrieve artifacts (version=${RETRIEVE_VERSION})"
    log "========================================================"

    s3_download "Retrieve/${RETRIEVE_VERSION}/cooc_session.npz"   "artifacts/retriever/cooc/cooc_session.npz"
    s3_download "Retrieve/${RETRIEVE_VERSION}/cooc_playlist.npz"  "artifacts/retriever/cooc/cooc_playlist.npz"
    s3_download "Retrieve/${RETRIEVE_VERSION}/user_centroids.pkl" "artifacts/retriever/pref_nn/user_centroids.pkl"
    s3_download "Retrieve/${RETRIEVE_VERSION}/pop_scores.csv"     "artifacts/retriever/popularity/pop_scores.csv"
    s3_download "Retrieve/${RETRIEVE_VERSION}/split_train.npy"    "artifacts/retriever/split/split_train.npy"
    s3_download "Retrieve/${RETRIEVE_VERSION}/split_val.npy"      "artifacts/retriever/split/split_val.npy"
    s3_download "Retrieve/${RETRIEVE_VERSION}/split_test.npy"     "artifacts/retriever/split/split_test.npy"

# --------------------------------------------------------------------------- #
# Step 3b (with-delta): 下载所有 delta 分区到 /tmp/delta/
# --------------------------------------------------------------------------- #
else
    log "========================================================"
    log "Step 3: Downloading delta partitions → /tmp/delta/"
    log "========================================================"

    rm -rf /tmp/delta && mkdir -p /tmp/delta
    aws ${EP} s3 cp --recursive \
        "s3://${BUCKET}/session_event/delta/" /tmp/delta/
    log "OK: delta partitions downloaded to /tmp/delta/"
fi

echo ""
log "========================================================"
log "Download complete. Artifact sizes:"
du -sh artifacts/item2vec/ artifacts/retriever/ 2>/dev/null || true
log "========================================================"
