#!/usr/bin/env bash
# =============================================================================
# scripts/retrain.sh — Full retraining orchestration
#
# Phase 1 (initial training on 30Music data, Ray Tune sweep):
#   bash scripts/retrain.sh --phase1 --retrieve-version 20260417_051148
#
# Phase 2 (retrain after online delta arrives, fixed hyperparams):
#   bash scripts/retrain.sh --phase2
#
# Environment variables (set in .env / docker-compose.yml):
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_ENDPOINT_URL
#   MLFLOW_TRACKING_URI=http://129.114.25.207:8000/
# =============================================================================

set -euo pipefail

BUCKET="${ARTIFACT_BUCKET:-proj23-mlflow-artifacts}"
ENDPOINT="${AWS_ENDPOINT_URL:-https://chi.tacc.chameleoncloud.org:7480}"
EP="--endpoint-url ${ENDPOINT} --no-verify-ssl --no-progress"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "${SCRIPT_DIR}")"
cd "${ROOT_DIR}"

# --------------------------------------------------------------------------- #
# Parse arguments
# --------------------------------------------------------------------------- #
MODE=""
RETRIEVE_VERSION=""
EXPERIMENT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --phase1)          MODE="phase1"; shift ;;
        --phase2)          MODE="phase2"; shift ;;
        --retrieve-version) RETRIEVE_VERSION="$2"; shift 2 ;;
        --experiment)       EXPERIMENT="$2";        shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

[[ -n "${MODE}" ]] || die "Must specify --phase1 or --phase2"
if [[ "${MODE}" == "phase1" && -z "${RETRIEVE_VERSION}" ]]; then
    die "--phase1 requires --retrieve-version VERSION (e.g. 20260417_051148)"
fi

# --------------------------------------------------------------------------- #
# Generate VERSION for this retrain run
# --------------------------------------------------------------------------- #
VERSION=$(date +%Y%m%d_%H%M%S)
log "========================================================"
log "Retrain VERSION: ${VERSION}  mode: ${MODE}"
log "MLFLOW_TRACKING_URI: ${MLFLOW_TRACKING_URI:-<not set>}"
log "========================================================"
echo "${VERSION}" > VERSION.txt

mkdir -p logs

# --------------------------------------------------------------------------- #
# Phase 1: Download no-delta data → build ranker data → Ray Tune sweep
# --------------------------------------------------------------------------- #
if [[ "${MODE}" == "phase1" ]]; then

    log "========================================================"
    log "Phase 1 — Step 1: Downloading data from S3 (no-delta)"
    log "========================================================"
    bash scripts/download_data.sh --no-delta --retrieve-version "${RETRIEVE_VERSION}"

    log "========================================================"
    log "Phase 1 — Step 2: Building ranker training data"
    log "========================================================"
    python3 -m src.ranker.data.build \
        2>&1 | tee logs/ranker_data_build_${VERSION}.log

    log "========================================================"
    log "Phase 1 — Step 3: Fixed-config training (epochs 3 and 5)"
    log "========================================================"
    python3 scripts/tune_phase1.py \
        2>&1 | tee logs/ranker_tune_${VERSION}.log

    echo ""
    log "========================================================"
    log "Phase 1 complete!"
    log "  VERSION: ${VERSION}"
    log "  MLflow UI: ${MLFLOW_TRACKING_URI:-http://129.114.25.207:8000/}"
    log ""
    log "Next: review MLflow results, then promote best run:"
    log "  python3 scripts/promote.py --mode manual --retrieve-version ${RETRIEVE_VERSION}"
    log "========================================================"

fi

# --------------------------------------------------------------------------- #
# Phase 2: Download with-delta → merge → retriever rebuild → train → promote
# --------------------------------------------------------------------------- #
if [[ "${MODE}" == "phase2" ]]; then

    log "========================================================"
    log "Phase 2 — Step 1: Downloading data from S3 (with-delta)"
    log "========================================================"
    bash scripts/download_data.sh --with-delta

    log "========================================================"
    log "Phase 2 — Step 2: Merging snapshot + delta"
    log "========================================================"
    python3 scripts/merge_delta.py

    log "========================================================"
    log "Phase 2 — Step 3: Rebuilding retriever artifacts"
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

    log "========================================================"
    log "Phase 2 — Step 4: Building ranker training data"
    log "========================================================"
    python3 -m src.ranker.data.build \
        2>&1 | tee logs/ranker_data_build_${VERSION}.log

    log "========================================================"
    log "Phase 2 — Step 5: Fetching best Phase 1 hyperparams"
    log "========================================================"
    eval $(python3 scripts/get_best_params.py)
    log "  batch_size=${BEST_BATCH_SIZE}  lr=${BEST_LR}  dropout=${BEST_DROPOUT}  epochs=${BEST_EPOCHS}"

    log "========================================================"
    log "Phase 2 — Step 6: Training ranker"
    log "========================================================"
    RETRAIN_EXPERIMENT="${EXPERIMENT:-retraining after online service}"
    python3 -m src.ranker.train \
        --mlflow-experiment "${RETRAIN_EXPERIMENT}" \
        --run-name "retrain_${VERSION}" \
        --batch-size "${BEST_BATCH_SIZE}" \
        --lr "${BEST_LR}" \
        --dropout "${BEST_DROPOUT}" \
        --epochs "${BEST_EPOCHS}" \
        --n-layers 2 \
        2>&1 | tee logs/ranker_train_${VERSION}.log

    log "========================================================"
    log "Phase 2 — Step 7: Auto-promoting best model"
    log "========================================================"
    python3 scripts/promote.py \
        --mode auto \
        --retrieve-version "${RETRIEVE_VERSION}" \
        --version "${VERSION}" \
        2>&1 | tee logs/promote_${VERSION}.log

    echo ""
    log "========================================================"
    log "Phase 2 complete!"
    log "  VERSION:          ${VERSION}"
    log "  Retrieve version: ${RETRIEVE_VERSION}"
    log "  MLflow UI:        ${MLFLOW_TRACKING_URI:-http://129.114.25.207:8000/}"
    log "========================================================"

fi
