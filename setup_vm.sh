#!/usr/bin/env bash
# =============================================================================
# setup_vm.sh — VM 环境准备 + Ranker 训练数据下载
#
# 使用方法:
#   git clone <repo_url> && cd <repo_dir>
#   export AWS_ACCESS_KEY_ID=<your_key>
#   export AWS_SECRET_ACCESS_KEY=<your_secret>
#   bash setup_vm.sh
#
# 完成后:
#   uv run python -m src.ranker.train 2>&1 | tee logs/ranker_train.log
#
# 训练结束后上传结果:
#   bash upload_results.sh
# =============================================================================

set -euo pipefail

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
S3_BUCKET="proj23-mlflow-artifacts"
S3_ENDPOINT="https://chi.tacc.chameleoncloud.org:7480"
export PYTHONWARNINGS="ignore:Unverified HTTPS request"

# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
log() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"
log "Working directory: ${SCRIPT_DIR}"

# --------------------------------------------------------------------------- #
# Step 1: 安装 uv
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 1/5: 安装 uv"
log "========================================================"
if ! command -v uv &>/dev/null; then
    log "uv 未找到，正在安装..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${PATH}"
    log "uv 安装完成: $(uv --version)"
else
    log "uv 已安装: $(uv --version)"
fi
echo ""

# --------------------------------------------------------------------------- #
# Step 2: 安装 Python 依赖
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 2/5: 安装 Python 依赖 (uv sync)"
log "========================================================"
[[ -f "pyproject.toml" ]] || die "pyproject.toml 未找到，请在项目根目录运行此脚本"
uv sync
log "依赖安装完成"
echo ""

# --------------------------------------------------------------------------- #
# Step 3: 安装 awscli (进 .venv)
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 3/5: 配置 AWS CLI"
log "========================================================"
AWS_BIN="${SCRIPT_DIR}/.venv/bin/aws"
if [[ ! -f "${AWS_BIN}" ]]; then
    log "安装 awscli 到 .venv..."
    uv pip install awscli
    log "awscli 安装完成"
else
    log "awscli 已安装: $("${AWS_BIN}" --version 2>&1)"
fi
echo ""

# --------------------------------------------------------------------------- #
# Step 4: 创建目录
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 4/5: 创建 artifact 目录"
log "========================================================"
mkdir -p \
    artifacts/item2vec \
    artifacts/retriever/pref_nn \
    artifacts/ranker \
    logs
log "目录创建完成"
echo ""

# --------------------------------------------------------------------------- #
# Step 5: 从 S3 下载训练所需 artifacts
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 5/5: 从 S3 下载 artifacts"
log "========================================================"

s3_download() {
    local src="$1" dst="$2"
    if [[ -f "$dst" ]]; then
        log "SKIP (已存在): $dst"
    else
        log "Downloading: $(basename $dst) ..."
        "${AWS_BIN}" s3 cp "s3://${S3_BUCKET}/${src}" "$dst" \
            --endpoint-url "${S3_ENDPOINT}" \
            --no-verify-ssl \
            --no-progress
        log "OK: $dst  ($(du -sh $dst | cut -f1))"
    fi
}

# Item2Vec 嵌入矩阵 (365 MB)
s3_download "artifacts/item2vec/item2vec_128d.npy"          artifacts/item2vec/item2vec_128d.npy

# Track ID → 矩阵行号映射 (13 MB)
s3_download "artifacts/item2vec/item2vec_track_to_row.json" artifacts/item2vec/item2vec_track_to_row.json

# 用户偏好聚类 (110 MB)
s3_download "artifacts/retriever/pref_nn/user_centroids.pkl" artifacts/retriever/pref_nn/user_centroids.pkl

# Ranker 训练数据 (~4-5 GB)
s3_download "artifacts/ranker/ranker_train.parquet" artifacts/ranker/ranker_train.parquet

# Ranker 验证数据 (~30 MB)
s3_download "artifacts/ranker/ranker_val.parquet"   artifacts/ranker/ranker_val.parquet

# MLflow 数据库 (实验/run 元数据)
if [[ ! -f mlflow.db ]]; then
    log "Downloading mlflow.db ..."
    "${AWS_BIN}" s3 cp "s3://${S3_BUCKET}/mlflow.db" mlflow.db \
        --endpoint-url "${S3_ENDPOINT}" --no-verify-ssl --no-progress
    log "OK: mlflow.db"
else
    log "SKIP (已存在): mlflow.db"
fi

echo ""

# --------------------------------------------------------------------------- #
# 完成
# --------------------------------------------------------------------------- #
log "========================================================"
log "Setup 完成！Artifact 大小:"
log "========================================================"
du -sh \
    artifacts/item2vec/item2vec_128d.npy \
    artifacts/item2vec/item2vec_track_to_row.json \
    artifacts/retriever/pref_nn/user_centroids.pkl \
    artifacts/ranker/ranker_train.parquet \
    artifacts/ranker/ranker_val.parquet \
    2>/dev/null
echo ""
echo "========================================================"
echo "开始训练 (默认: 3 epochs, batch=512, 自动检测 GPU):"
echo "  uv run python -m src.ranker.train 2>&1 | tee logs/ranker_train.log"
echo ""
echo "自定义超参示例:"
echo "  uv run python -m src.ranker.train --epochs 5 --batch-size 256 --device cuda"
echo ""
echo "训练完成后上传结果到 S3:"
echo "  bash upload_results.sh"
echo "========================================================"