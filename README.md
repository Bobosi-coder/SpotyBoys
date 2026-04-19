# SpotyBoys Serving Stack

SpotyBoys is the VM1 serving application for the Option B music recommendation architecture.

This repository now keeps only the code needed to run, validate, and hand off the serving path:

- first-party frontend
- recommendation API
- event API
- PostgreSQL durable truth
- Redis hot/session state
- internal Navidrome media backend
- same-origin stream proxy
- catalog sync worker
- serving artifact fetch/refresh workers
- parser export and VM2 handoff scaffolds
- shared contracts and tests

## Local Demo

```bash
cp .env.example .env

export COMPOSE_PROJECT_NAME=spotiboys_local_demo
export SPOTIBOYS_FRONTEND_PORT=5173

docker compose -f docker-compose.yml up --build -d
BASE_URL=http://127.0.0.1:5173 bash infra/scripts/healthcheck_demo.sh
```

Open:

```text
http://127.0.0.1:5173/
```

## VM Demo

The VM demo uses frontend port `8089` and reads songs from:

```text
/mnt/mlflow_persist_large/music/
```

Use the VM runbook:

```text
docs/vm_docker_demo_setup.md
```

Short form:

```bash
git fetch origin
git checkout serving_requirements
git pull origin serving_requirements

cp .env.example .env
# Fill object-storage credentials in .env.

export COMPOSE_PROJECT_NAME=spotiboys_vm1_demo
export SPOTIBOYS_FRONTEND_PORT=8089
export SPOTIBOYS_VM_MUSIC_ROOT=/mnt/mlflow_persist_large/music

docker compose -f docker-compose.yml -f docker-compose.vm-library.yml up --build -d
BASE_URL=http://127.0.0.1:8089 bash infra/scripts/healthcheck_demo.sh
```

Open:

```text
http://<VM_PUBLIC_IP_OR_HOSTNAME>:8089/
```

## Current Serving Path

```text
browser
  -> nginx same-origin ingress
  -> frontend-web
  -> recommendation-api / event-api
  -> PostgreSQL + Redis
  -> internal Navidrome
  -> VM music mount or local fixture music
```

The browser never receives raw Navidrome credentials or internal Navidrome URLs.

Playback goes through:

```text
GET /stream/<track_id>
```

## Model Artifacts

Object storage is used for model artifacts, not song files.

The artifact fetch worker downloads:

- `Real_service/<version>/`
- `Item2vec/`

and stages the active serving bundle under:

```text
/serving-bundle/Real_service/active
/serving-bundle/runtime
```

The active online serving stages are:

- C1: offline Item2Vec artifacts consumed at serving time
- C2: online candidate retrieval from co-occurrence, user centroids, and popularity artifacts
- C3: online GRU ranker inference
- C4: policy reranking and playable-only filtering

## Important Directories

```text
apps/frontend-web/              frontend product surface
apps/recommendation-api/         session, recommendations, playable-track, stream, auth
apps/event-api/                  impression, playback, feedback ingestion
packages/                        shared contracts, config, DB access, runtime state, recommendation engine
db/                              PostgreSQL schema and indexes
infra/nginx/                     same-origin reverse proxy config
infra/scripts/                   demo, healthcheck, validation, fixture-media helpers
workers/                         catalog sync, artifact fetch/refresh, parser export, outcome derivation
jobs/                            VM2 retraining/promotion placeholders
fixtures/                        tiny local fixture catalog and test serving bundle
src/ranker/                      latest GRU ranker training/inference code
src/retriever/                   latest C2 retrieval code
tests/                           backend/frontend contract tests
docs/                            implementation and VM setup runbooks
```

## Verification

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall packages apps infra/scripts workers jobs src tests
docker compose -f docker-compose.yml config
```

For a running stack:

```bash
BASE_URL=http://127.0.0.1:${SPOTIBOYS_FRONTEND_PORT:-5173} bash infra/scripts/healthcheck_demo.sh
```
