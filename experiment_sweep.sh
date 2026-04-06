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

# ── baseline: lr=1e-4, epochs=3, n_layers=2, dropout=0.1 ─────────────────────
run_experiment "baseline" \
    --lr 1e-4 --epochs 3 --batch-size 512 \
    --n-layers 2 --dropout 0.1

# ── v1_lr3e4: higher LR ───────────────────────────────────────────────────────
run_experiment "v1_lr3e4" \
    --lr 3e-4 --epochs 3 --batch-size 512 \
    --n-layers 2 --dropout 0.1

# ── v2_ep5: more epochs ───────────────────────────────────────────────────────
run_experiment "v2_ep5" \
    --lr 1e-4 --epochs 5 --batch-size 512 \
    --n-layers 2 --dropout 0.1

# ── v3_deep: 3-layer GRU ─────────────────────────────────────────────────────
run_experiment "v3_deep" \
    --lr 1e-4 --epochs 3 --batch-size 512 \
    --n-layers 3 --dropout 0.1

# ── v4_lr3e4_ep5: higher LR + more epochs ────────────────────────────────────
run_experiment "v4_lr3e4_ep5" \
    --lr 3e-4 --epochs 5 --batch-size 512 \
    --n-layers 2 --dropout 0.1

log "========================================================"
log "All experiments complete. View results:"
log "  MLflow UI → ${MLFLOW_TRACKING_URI}  experiment: ${EXPERIMENT}"
log "========================================================"
