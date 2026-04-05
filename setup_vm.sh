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
export AWS_ACCESS_KEY_ID="11580ec852704238a35acfbd65c7146a"
export AWS_SECRET_ACCESS_KEY="2759a133cae84a8e9a48c609c4dbc1b1"
export PYTHONWARNINGS="ignore:Unverified HTTPS request"
export MLFLOW_TRACKING_URI="http://localhost:8000"
# 持久化到 ~/.bashrc，SSH 重连后自动生效
if ! grep -q 'MLFLOW_TRACKING_URI' "${HOME}/.bashrc" 2>/dev/null; then
    echo 'export MLFLOW_TRACKING_URI=http://localhost:8000' >> "${HOME}/.bashrc"
fi

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

# 确保 ~/.local/bin 在 PATH 中（uv 安装后写在这里）
export PATH="${HOME}/.local/bin:${PATH}"

# 写入 ~/.bashrc，后续 SSH 登录也能直接用 uv
if ! grep -q '\.local/bin' "${HOME}/.bashrc" 2>/dev/null; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "${HOME}/.bashrc"
    log "已将 ~/.local/bin 写入 ~/.bashrc"
fi

if ! command -v uv &>/dev/null; then
    log "uv 未找到，正在安装..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # installer 可能写了 env 文件，source 一下确保当前 shell 生效
    [[ -f "${HOME}/.local/bin/env" ]] && source "${HOME}/.local/bin/env" || true
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
# Step 2b: 安装适配当前 GPU 平台的 PyTorch
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 2b/5: 检测 GPU 平台，安装正确的 PyTorch"
log "========================================================"
VENV_PIP="${SCRIPT_DIR}/.venv/bin/pip"

_amdgpu=$(lsmod | grep amdgpu || true)
if [[ -n "${_amdgpu}" ]]; then
    log "检测到 AMD GPU (amdgpu 模块已加载)"

    # 尝试读取 ROCm 版本
    if [[ -f /opt/rocm/.info/version ]]; then
        ROCM_FULL=$(cat /opt/rocm/.info/version)
    elif ROCM_FULL=$(apt-cache show rocm-libs 2>/dev/null | grep -oP 'Version: \K[\d.]+' | head -1) && [[ -n "${ROCM_FULL}" ]]; then
        :
    else
        ROCM_FULL="6.2.0"
    fi
    ROCM_XY=$(echo "${ROCM_FULL}" | grep -oP '^\d+\.\d+')
    log "ROCm 版本: ${ROCM_FULL}  →  whl 索引: rocm${ROCM_XY}"

    "${VENV_PIP}" install torch --index-url "https://download.pytorch.org/whl/rocm${ROCM_XY}" \
        --quiet && log "PyTorch (ROCm ${ROCM_XY}) 安装完成" \
        || log "WARNING: ROCm PyTorch 安装失败，将使用 CPU 版本"

elif command -v nvidia-smi &>/dev/null; then
    log "检测到 NVIDIA GPU — uv sync 已安装 CUDA PyTorch，无需额外操作"
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null || true

else
    log "未检测到 GPU — 将使用 CPU 训练"
fi
echo ""

# --------------------------------------------------------------------------- #
# Step 3: 创建目录
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 3/5: 创建 artifact 目录"
log "========================================================"
mkdir -p \
    artifacts/item2vec \
    artifacts/retriever/pref_nn \
    artifacts/ranker \
    logs
log "目录创建完成"
echo ""

# --------------------------------------------------------------------------- #
# Step 4: 从 S3 下载训练所需 artifacts
# --------------------------------------------------------------------------- #
log "========================================================"
log "Step 4/4: 从 S3 下载 artifacts"
log "========================================================"

s3_download() {
    local src="$1" dst="$2"
    if [[ -f "$dst" ]]; then
        log "SKIP (已存在): $dst"
    else
        log "Downloading: $(basename "$dst") ..."
        aws \
            --endpoint-url "${S3_ENDPOINT}" \
            --no-verify-ssl \
            s3 cp "s3://${S3_BUCKET}/${src}" "$dst" \
            --no-progress
        log "OK: $dst  ($(du -sh "$dst" | cut -f1))"
    fi
}

# Item2Vec 嵌入矩阵 (365 MB)
s3_download "artifacts/item2vec/item2vec_128d.npy"           artifacts/item2vec/item2vec_128d.npy

# Track ID → 矩阵行号映射 (13 MB)
s3_download "artifacts/item2vec/item2vec_track_to_row.json"  artifacts/item2vec/item2vec_track_to_row.json

# 用户偏好聚类 (110 MB)
s3_download "artifacts/retriever/pref_nn/user_centroids.pkl" artifacts/retriever/pref_nn/user_centroids.pkl

# Ranker 训练数据 (~945 MB)
s3_download "artifacts/ranker/ranker_train.parquet"          artifacts/ranker/ranker_train.parquet

# Ranker 验证数据 (~25 MB)
s3_download "artifacts/ranker/ranker_val.parquet"            artifacts/ranker/ranker_val.parquet

# MLflow 数据库
if [[ ! -f mlflow.db ]]; then
    log "Downloading mlflow.db ..."
    aws \
        --endpoint-url "${S3_ENDPOINT}" \
        --no-verify-ssl \
        s3 cp "s3://${S3_BUCKET}/mlflow.db" mlflow.db \
        --no-progress
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
