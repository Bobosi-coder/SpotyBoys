#!/usr/bin/env bash
# =============================================================================
# experiment_sweep.sh — Run 5 GRU Ranker training experiments consecutively
#
# Usage:
#   bash experiment_sweep.sh 2>&1 | tee logs/sweep.log
# =============================================================================

set -euo pipefail

EXPERIMENT="gru-ranker-sweep-for-first-implementation"
LOG_DIR="logs"
mkdir -p "${LOG_DIR}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

run_experiment() {
    local run_name="$1"; shift
    local log_file="${LOG_DIR}/ranker_${run_name}.log"
    log "▶ Starting: ${run_name}"
    uv run python -m src.ranker.train \
        --mlflow-experiment "${EXPERIMENT}" \
        --run-name "${run_name}" \
        "$@" 2>&1 | tee "${log_file}"
    log "✓ Done: ${run_name}  (log: ${log_file})"
    echo ""
}

log "========================================================"
log "GRU Ranker Experiment Sweep  (experiment=${EXPERIMENT})"
log "========================================================"
echo ""

# =============================================================================
#
# This for small batch. Initial test for the training.
#
# =============================================================================


# # ── baseline: lr=1e-4, epochs=3, n_layers=2, dropout=0.1 ─────────────────────
# run_experiment "baseline" \
#     --lr 1e-4 --epochs 3 --batch-size 512 \
#     --n-layers 2 --dropout 0.1

# # ── v1_lr3e4: higher LR ───────────────────────────────────────────────────────
# run_experiment "v1_lr3e4" \
#     --lr 3e-4 --epochs 3 --batch-size 512 \
#     --n-layers 2 --dropout 0.1

# # ── v2_ep5: more epochs ───────────────────────────────────────────────────────
# run_experiment "v2_ep5" \
#     --lr 1e-4 --epochs 5 --batch-size 512 \
#     --n-layers 2 --dropout 0.1

# # ── v3_deep: 3-layer GRU ─────────────────────────────────────────────────────
# run_experiment "v3_deep" \
#     --lr 1e-4 --epochs 3 --batch-size 512 \
#     --n-layers 3 --dropout 0.1

# # ── v4_lr3e4_ep5: higher LR + more epochs ────────────────────────────────────
# run_experiment "v4_lr3e4_ep5" \
#     --lr 3e-4 --epochs 5 --batch-size 512 \
#     --n-layers 2 --dropout 0.1

# =============================================================================
#
# This for 8 x 512 batch size. Second test for the training.
#
# =============================================================================

# ── baseline_b4k: 对标原 baseline，batch 放大 8× ─────────────────────────────
run_experiment "baseline_b4k" \
    --lr 8e-4 --epochs 3 --batch-size 4096 \
    --n-layers 2 --dropout 0.1

# ── v1_lr: 对标原 v1_lr3e4，lr 同步 ×8 ──────────────────────────────────────
run_experiment "v1_lr_b4k" \
    --lr 2.4e-3 --epochs 3 --batch-size 4096 \
    --n-layers 2 --dropout 0.1

# ── v2_ep: 大 batch 收敛快，epochs 可以少一点先试试 ──────────────────────────
run_experiment "v2_ep3_b4k" \
    --lr 8e-4 --epochs 3 --batch-size 4096 \
    --n-layers 2 --dropout 0.1

# ── v3_deep: 3-layer，结构不变，lr/batch 跟进 ────────────────────────────────
run_experiment "v3_deep_b4k" \
    --lr 8e-4 --epochs 3 --batch-size 4096 \
    --n-layers 3 --dropout 0.1

# ── v4_combined: 高 lr + 大 batch 的组合拳 ───────────────────────────────────
run_experiment "v4_combined_b4k" \
    --lr 2.4e-3 --epochs 3 --batch-size 4096 \
    --n-layers 2 --dropout 0.1


log "========================================================"
log "All experiments complete. View results:"
log "  MLflow UI → ${MLFLOW_TRACKING_URI}  experiment: ${EXPERIMENT}"
log "========================================================"
