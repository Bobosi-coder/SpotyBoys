#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

VENV_PATH="${AWSCLI_VENV_PATH:-$HOME/awscli-venv}"

echo "[install-awscli] Installing prerequisites..."
sudo apt update
sudo apt install -y python3-pip python3-venv

if [ ! -d "$VENV_PATH" ]; then
  echo "[install-awscli] Creating venv at $VENV_PATH"
  python3 -m venv "$VENV_PATH"
else
  echo "[install-awscli] Reusing existing venv at $VENV_PATH"
fi

# shellcheck disable=SC1090
source "$VENV_PATH/bin/activate"

echo "[install-awscli] Installing awscli..."
python -m pip install --upgrade pip
python -m pip install --upgrade awscli

echo "[install-awscli] Installed version:"
aws --version

echo "[install-awscli] Done."
echo "[install-awscli] Next steps:"
echo "  1) bash setup.sh"
echo "  2) source scripts/aws_env.sh"
echo "  3) bash scripts/download_remote_assets.sh   # optional"
