#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

log()  { echo "[setup] $*"; }
warn() { echo "[setup] WARNING: $*" >&2; }
die()  { echo "[setup] ERROR: $*" >&2; exit 1; }

run_with_sudo() {
  if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    die "This step requires sudo: $*"
  fi
}

run_as_postgres() {
  if command -v runuser >/dev/null 2>&1; then
    run_with_sudo runuser -u postgres -- "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u postgres "$@"
  else
    die "Unable to switch to the postgres user. Install runuser or sudo first."
  fi
}

ensure_env_file() {
  if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp ".env.example" ".env"
    log "Created .env from .env.example"
  fi

  if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "${SCRIPT_DIR}/.env"
    set +a
    log "Loaded environment from .env"
  else
    warn ".env.example was not found; continuing without a local .env file."
  fi
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    log "uv is already installed: $(uv --version)"
    return
  fi

  log "uv not found. Installing via official installer..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
  command -v uv >/dev/null 2>&1 || die "uv installation succeeded but uv is not on PATH."
  log "uv installed: $(uv --version)"
}

sync_dependencies() {
  [ -f "pyproject.toml" ] || die "pyproject.toml not found in ${SCRIPT_DIR}"
  log "Installing project dependencies with uv sync"
  uv sync
}

ensure_postgres() {
  if command -v psql >/dev/null 2>&1; then
    log "psql is already available."
    return
  fi

  if [ "${INSTALL_POSTGRES:-true}" != "true" ]; then
    die "psql is not installed and INSTALL_POSTGRES=false."
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    die "Automatic PostgreSQL installation currently supports apt-get based systems only."
  fi

  log "Installing PostgreSQL packages"
  run_with_sudo apt-get update
  run_with_sudo apt-get install -y postgresql postgresql-contrib
}

start_postgres() {
  if ! command -v psql >/dev/null 2>&1; then
    warn "Skipping PostgreSQL startup because psql is unavailable."
    return
  fi

  if command -v systemctl >/dev/null 2>&1; then
    run_with_sudo systemctl start postgresql >/dev/null 2>&1 \
      || run_with_sudo systemctl start postgres >/dev/null 2>&1 \
      || warn "Could not start PostgreSQL with systemctl."
    return
  fi

  if command -v service >/dev/null 2>&1; then
    run_with_sudo service postgresql start >/dev/null 2>&1 \
      || run_with_sudo service postgres start >/dev/null 2>&1 \
      || warn "Could not start PostgreSQL with service."
    return
  fi

  warn "No supported service manager found. Start PostgreSQL manually if needed."
}

configure_postgres_password() {
  if [ "${CONFIGURE_POSTGRES_PASSWORD:-true}" != "true" ]; then
    log "Skipping postgres password bootstrap."
    return
  fi

  if ! command -v id >/dev/null 2>&1 || ! id postgres >/dev/null 2>&1; then
    warn "Local postgres OS user not found. Skipping password bootstrap."
    return
  fi

  if [ "${DB_USER:-postgres}" != "postgres" ]; then
    log "DB_USER is ${DB_USER}; skipping postgres superuser password bootstrap."
    return
  fi

  if [ -z "${DB_PASSWORD:-}" ]; then
    warn "DB_PASSWORD is not set. Skipping postgres password bootstrap."
    return
  fi

  if [[ "${DB_PASSWORD}" == *"'"* ]]; then
    warn "DB_PASSWORD contains a single quote. Set the postgres password manually."
    return
  fi

  log "Setting local postgres user password"
  run_as_postgres psql -v ON_ERROR_STOP=1 \
    -c "ALTER USER postgres PASSWORD '${DB_PASSWORD}';" >/dev/null
}

init_database() {
  if [ "${INIT_DB:-true}" != "true" ]; then
    log "Skipping database initialization."
    return
  fi

  [ -f "scripts/init_db.sh" ] || die "scripts/init_db.sh not found."
  log "Initializing local PostgreSQL schema"
  bash "scripts/init_db.sh"
}


main() {
  log "Working directory: ${SCRIPT_DIR}"
  ensure_env_file
  ensure_uv
  sync_dependencies
  ensure_postgres
  start_postgres
  configure_postgres_password
  init_database
  log "AWS CLI installation is handled separately by scripts/install_awscli.sh"
  log "Use scripts/download_remote_assets.sh when you want to download object-storage data."
  log "Setup complete."
}

main "$@"
