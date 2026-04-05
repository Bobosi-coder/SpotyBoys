# SpotyBoys Item2Vec Workspace

This branch is based on `feature/item2vec-embedding` and keeps the local database bootstrap files from the cleanup work.

## Quick Start

```bash
bash setup.sh
```

By default `setup.sh`:

- installs `uv` if needed and runs `uv sync`
- creates `.env` from `.env.example` on first run
- installs and starts PostgreSQL on apt-based systems when `psql` is missing
- initializes the local `spotiboys` schema from `db/001_init.sql`
- installs `awscli` into `.venv` and syncs `data/` and `panns/` from Chameleon S3

## Common Toggles

```bash
SYNC_REMOTE_ASSETS=false bash setup.sh
INSTALL_POSTGRES=false INIT_DB=false bash setup.sh
RUN_INDEXES=true bash setup.sh
```

## Re-run DB Init Only

```bash
bash scripts/init_db.sh
```

## Release Workflows

Versioned data releases for the initial implementation:

```bash
bash scripts/run_item2vec_release.sh
bash scripts/run_retriever_release.sh
```

Published object-storage prefixes:

- `processed/item2vec/{dataset_version}/`
- `features/retriever/{feature_version}/`
- `manifests/releases/...`

## Project Notes

- Item2Vec code lives under `src/item2vec/`
- Retriever code lives under `src/retriever/`
- Data release helpers live under `src/data_release/`
- Data preprocessing notes live in `data_preprocess_pipeline.md`
- Item2Vec pipeline notes live in `item2vec_pipeline.md`
