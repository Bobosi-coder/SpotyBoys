#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

EXPLICIT_GENERATOR_PARQUET_PATH="${GENERATOR_PARQUET_PATH-__UNSET__}"
EXPLICIT_GENERATOR_OUTPUT_DIR="${GENERATOR_OUTPUT_DIR-__UNSET__}"
EXPLICIT_RECOMMEND_URL="${RECOMMEND_URL-__UNSET__}"
EXPLICIT_IMPRESSION_URL="${IMPRESSION_URL-__UNSET__}"
EXPLICIT_OUTCOME_URL="${OUTCOME_URL-__UNSET__}"

if [ -f "${PROJECT_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${PROJECT_ROOT}/.env"
  set +a
fi

if [ "${EXPLICIT_GENERATOR_PARQUET_PATH}" != "__UNSET__" ]; then export "GENERATOR_PARQUET_PATH=${EXPLICIT_GENERATOR_PARQUET_PATH}"; fi
if [ "${EXPLICIT_GENERATOR_OUTPUT_DIR}" != "__UNSET__" ]; then export "GENERATOR_OUTPUT_DIR=${EXPLICIT_GENERATOR_OUTPUT_DIR}"; fi
if [ "${EXPLICIT_RECOMMEND_URL}" != "__UNSET__" ]; then export "RECOMMEND_URL=${EXPLICIT_RECOMMEND_URL}"; fi
if [ "${EXPLICIT_IMPRESSION_URL}" != "__UNSET__" ]; then export "IMPRESSION_URL=${EXPLICIT_IMPRESSION_URL}"; fi
if [ "${EXPLICIT_OUTCOME_URL}" != "__UNSET__" ]; then export "OUTCOME_URL=${EXPLICIT_OUTCOME_URL}"; fi

if ! command -v uv >/dev/null 2>&1 && [ -x "${HOME}/.local/bin/uv" ]; then
  export PATH="${HOME}/.local/bin:${PATH}"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "[ranker-generator] ERROR: uv not found on PATH." >&2
  echo "[ranker-generator] Try: export PATH=\"\$HOME/.local/bin:\$PATH\"" >&2
  exit 1
fi

echo "[ranker-generator] project root: ${PROJECT_ROOT}"
echo "[ranker-generator] parquet path: ${GENERATOR_PARQUET_PATH:-auto}"
echo "[ranker-generator] recommend url: ${RECOMMEND_URL:-local-jsonl-only}"
echo "[ranker-generator] impression url: ${IMPRESSION_URL:-local-jsonl-only}"
echo "[ranker-generator] outcome url: ${OUTCOME_URL:-local-jsonl-only}"

CMD=(uv run python -m src.data_gen.generate_ranker_seed_traffic)

if [ -n "${GENERATOR_PARQUET_PATH:-}" ]; then
  CMD+=(--parquet-path "${GENERATOR_PARQUET_PATH}")
fi
if [ -n "${GENERATOR_OUTPUT_DIR:-}" ]; then
  CMD+=(--output-dir "${GENERATOR_OUTPUT_DIR}")
fi
if [ -n "${RECOMMEND_URL:-}" ]; then
  CMD+=(--recommend-url "${RECOMMEND_URL}")
fi
if [ -n "${IMPRESSION_URL:-}" ]; then
  CMD+=(--impression-url "${IMPRESSION_URL}")
fi
if [ -n "${OUTCOME_URL:-}" ]; then
  CMD+=(--outcome-url "${OUTCOME_URL}")
fi

CMD+=("$@")
"${CMD[@]}"
