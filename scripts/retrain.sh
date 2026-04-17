#!/usr/bin/env bash
# =============================================================================
# scripts/retrain.sh — 完整 retraining 编排脚本
#
# Usage:
#   # 只重新训练 ranker（复用已有 retriever artifacts）:
#   bash scripts/retrain.sh --no-delta --retrieve-version 20260417_051148
#
#   # 完整 retrain：合并新 session 数据 + retriever + ranker:
#   bash scripts/retrain.sh --with-delta
#
#   # 自定义实验名（可选）:
#   bash scripts/retrain.sh --no-delta --retrieve-version 20260417_051148 \
#       --experiment gru-ranker-retrain
#
# 环境变量 (docker-compose.yml 设置):
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_ENDPOINT_URL
#   MLFLOW_TRACKING_URI=http://129.114.25.207:8000/
# =============================================================================

set -euo pipefail

BUCKET="proj23-mlflow-artifacts"
ENDPOINT="${AWS_ENDPOINT_URL:-https://chi.tacc.chameleoncloud.org:7480}"
EP="--endpoint-url ${ENDPOINT} --no-verify-ssl --no-progress"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "${SCRIPT_DIR}")"
cd "${ROOT_DIR}"

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
    die "--no-delta requires --retrieve-version VERSION (e.g. 20260417_051148)"
fi

# --------------------------------------------------------------------------- #
# 生成本次 retrain 的 VERSION（用于 Retrieve/ 和 Real_service/ 路径）
# --------------------------------------------------------------------------- #
VERSION=$(date +%Y%m%d_%H%M%S)
log "========================================================"
log "Retrain VERSION: ${VERSION}  mode: ${MODE}"
log "MLFLOW_TRACKING_URI: ${MLFLOW_TRACKING_URI:-<not set>}"
log "========================================================"
echo "${VERSION}" > VERSION.txt

mkdir -p logs

# --------------------------------------------------------------------------- #
# Step 1: 从 S3 下载数据
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 1: Downloading data from S3"
log "========================================================"

if [[ "${MODE}" == "no-delta" ]]; then
    bash scripts/download_data.sh --no-delta --retrieve-version "${RETRIEVE_VERSION}"
else
    bash scripts/download_data.sh --with-delta
fi

# --------------------------------------------------------------------------- #
# Step 2 (with-delta only): 合并 snapshot + delta
# --------------------------------------------------------------------------- #
if [[ "${MODE}" == "with-delta" ]]; then
    log "========================================================"
    log "Step 2: Merging snapshot + delta"
    log "========================================================"
    python3 scripts/merge_delta.py
fi

# --------------------------------------------------------------------------- #
# Step 3 (with-delta only): 重新构建 retriever artifacts
# --------------------------------------------------------------------------- #
if [[ "${MODE}" == "with-delta" ]]; then
    log "========================================================"
    log "Step 3: Rebuilding retriever artifacts"
    log "========================================================"

    log "  3.1/4 split.build ..."
    python3 -m src.retriever.split.build \
        2>&1 | tee logs/retriever_split_${VERSION}.log

    log "  3.2/4 cooc.build ..."
    python3 -m src.retriever.cooc.build \
        2>&1 | tee logs/retriever_cooc_${VERSION}.log

    log "  3.3/4 popularity.build ..."
    python3 -m src.retriever.popularity.build \
        2>&1 | tee logs/retriever_popularity_${VERSION}.log

    log "  3.4/4 pref_nn.build ..."
    python3 -m src.retriever.pref_nn.build \
        2>&1 | tee logs/retriever_pref_nn_${VERSION}.log

    # Upload new Retrieve/{VERSION}/ to S3
    log "  Uploading Retrieve/${VERSION}/ to S3 ..."
    for src_dst in \
        "artifacts/retriever/cooc/cooc_session.npz:Retrieve/${VERSION}/cooc_session.npz" \
        "artifacts/retriever/cooc/cooc_playlist.npz:Retrieve/${VERSION}/cooc_playlist.npz" \
        "artifacts/retriever/pref_nn/user_centroids.pkl:Retrieve/${VERSION}/user_centroids.pkl" \
        "artifacts/retriever/popularity/pop_scores.csv:Retrieve/${VERSION}/pop_scores.csv" \
        "artifacts/retriever/split/split_train.npy:Retrieve/${VERSION}/split_train.npy" \
        "artifacts/retriever/split/split_val.npy:Retrieve/${VERSION}/split_val.npy" \
        "artifacts/retriever/split/split_test.npy:Retrieve/${VERSION}/split_test.npy"; do
        local_path="${src_dst%%:*}"
        s3_key="${src_dst##*:}"
        aws ${EP} s3 cp "${local_path}" "s3://${BUCKET}/${s3_key}"
        log "  OK: ${s3_key}"
    done

    RETRIEVE_VERSION="${VERSION}"
    log "New Retrieve version: ${RETRIEVE_VERSION}"
fi

# --------------------------------------------------------------------------- #
# Step 4: 构建 ranker 训练数据
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 4: Building ranker training data"
log "========================================================"
python3 -m src.ranker.data.build \
    2>&1 | tee logs/ranker_data_build_${VERSION}.log

# --------------------------------------------------------------------------- #
# Step 5: 超参数 sweep（训练）
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 5: Running experiment sweep"
log "========================================================"
bash experiment_sweep.sh 2>&1 | tee logs/ranker_sweep_${VERSION}.log

# --------------------------------------------------------------------------- #
# 完成
# --------------------------------------------------------------------------- #
echo ""
log "========================================================"
log "Retrain complete!"
log "  VERSION:          ${VERSION}"
log "  Retrieve version: ${RETRIEVE_VERSION}"
log "  MLflow UI:        ${MLFLOW_TRACKING_URI:-http://129.114.25.207:8000/}"
log ""
log "Next: review MLflow results, then promote best run:"
log "  bash upload_results.sh --version ${VERSION} --retrieve-version ${RETRIEVE_VERSION}"
log "========================================================"
