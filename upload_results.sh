#!/usr/bin/env bash
# =============================================================================
# upload_results.sh — 训练完成后将结果上传至 S3
#
# 上传内容:
#   artifacts/ranker/gru_ranker.pt          模型 checkpoint
#   artifacts/ranker/gru_ranker_config.json 模型配置
#   mlruns/<new_run>/                        本次训练的 MLflow run 元数据
#   mlflow.db                                更新后的 MLflow 数据库
#
# 使用方法:
#   export AWS_ACCESS_KEY_ID=<your_key>
#   export AWS_SECRET_ACCESS_KEY=<your_secret>
#   bash upload_results.sh
# =============================================================================

set -euo pipefail

S3_BUCKET="proj23-mlflow-artifacts"
S3_ENDPOINT="https://chi.tacc.chameleoncloud.org:7480"
export PYTHONWARNINGS="ignore:Unverified HTTPS request"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"
AWS_BIN="${SCRIPT_DIR}/.venv/bin/aws"

[[ -f "${AWS_BIN}" ]] || { echo "ERROR: awscli 未找到，请先运行 setup_vm.sh"; exit 1; }

if [[ -z "${AWS_ACCESS_KEY_ID:-}" || -z "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
    echo "ERROR: AWS 凭据未设置"
    exit 1
fi

EP=(--endpoint-url "${S3_ENDPOINT}" --no-verify-ssl --no-progress)

# --------------------------------------------------------------------------- #
# 上传模型 checkpoint
# --------------------------------------------------------------------------- #
log "=== 上传模型 checkpoint ==="
if [[ -f artifacts/ranker/gru_ranker.pt ]]; then
    "${AWS_BIN}" s3 cp artifacts/ranker/gru_ranker.pt \
        "s3://${S3_BUCKET}/artifacts/ranker/gru_ranker.pt" "${EP[@]}"
    "${AWS_BIN}" s3 cp artifacts/ranker/gru_ranker_config.json \
        "s3://${S3_BUCKET}/artifacts/ranker/gru_ranker_config.json" "${EP[@]}"
    log "OK: gru_ranker.pt + gru_ranker_config.json 上传完成"
else
    log "WARNING: artifacts/ranker/gru_ranker.pt 未找到，训练可能未完成"
fi

# --------------------------------------------------------------------------- #
# 上传 MLflow 数据库 (含本次训练 run 的 metrics/params)
# --------------------------------------------------------------------------- #
log "=== 上传 MLflow 数据库 ==="
if [[ -f mlflow.db ]]; then
    "${AWS_BIN}" s3 cp mlflow.db \
        "s3://${S3_BUCKET}/mlflow.db" "${EP[@]}"
    log "OK: mlflow.db 上传完成"
fi

# --------------------------------------------------------------------------- #
# 上传 MLflow run 元数据 (只同步 metadata 文件，排除大 artifact)
# --------------------------------------------------------------------------- #
log "=== 同步 MLflow run 元数据 ==="
"${AWS_BIN}" s3 sync mlruns/ "s3://${S3_BUCKET}/mlruns/" \
    --exclude "*.npy" \
    --exclude "*.bin" \
    --exclude "*.npz" \
    --exclude "*.pkl" \
    --exclude "*.csv" \
    --exclude "*.parquet" \
    "${EP[@]}"
log "OK: MLflow 元数据同步完成"

echo ""
log "========================================================"
log "上传完成！S3 结果路径:"
log "  模型: s3://${S3_BUCKET}/artifacts/ranker/gru_ranker.pt"
log "  配置: s3://${S3_BUCKET}/artifacts/ranker/gru_ranker_config.json"
log "  MLflow: s3://${S3_BUCKET}/mlflow.db"
log "========================================================"
