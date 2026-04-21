# SpotyBoys — Music Recommendation Service

A full-stack music recommendation system built on a GRU-based session ranker, co-occurrence retrieval, and a self-hosted Navidrome media backend.

## Architecture Overview

```
browser
  → nginx :8089          same-origin ingress
  → frontend-web         React SPA
  → recommendation-api   C1–C4 ML pipeline + stream proxy
  → event-api            playback / feedback ingestion
  → PostgreSQL           durable state
  → Redis                session cache
  → Navidrome            internal media server (never exposed to browser)
  → /mnt/mlflow_persist_large/music/   MP3 library (read-only)
```

**Recommendation pipeline (C1–C4):**
- C1: Item2Vec embeddings + co-occurrence matrices + popularity scores (offline artifacts)
- C2: Multi-recall candidate retrieval (co-occurrence + preference NN + popularity fallback)
- C3: GRU ranker inference
- C4: Policy reranking, diversity, recency filtering

Model artifacts are fetched from S3 on startup. The music library is served from VM-local persistent storage.

---

## Deploying on a Fresh VM

### Prerequisites

- Ubuntu 22.04 VM (tested on Chameleon Cloud)
- Docker + Docker Compose v2 installed
- Two persistent block storage mounts:
  - `/mnt/mlflow_persist/` — databases and model artifacts (~50 GB)
  - `/mnt/mlflow_persist_large/` — MP3 music library (~200 GB)
- Port **8089** open in the VM security group
- S3 credentials for the Chameleon object storage bucket

### Step 1 — Prepare persistent directories

```bash
sudo mkdir -p /mnt/mlflow_persist/spotiboys/postgres
sudo mkdir -p /mnt/mlflow_persist/spotiboys/navidrome
sudo mkdir -p /mnt/mlflow_persist/spotiboys/serving-bundle
sudo mkdir -p /mnt/mlflow_persist/spotiboys/object-storage
sudo chown -R $USER:$USER /mnt/mlflow_persist/spotiboys/
```

The music library must already be present at:
```
/mnt/mlflow_persist_large/music/*.mp3
```

If a `manifest.csv` with track metadata exists at that path, it will be used to populate title and artist names. Otherwise filenames are used as titles.

### Step 2 — Clone the repository

```bash
git clone <repo-url> ~/SpotyBoys
cd ~/SpotyBoys
git checkout serving_requirements
```

### Step 3 — Configure environment

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Set the S3 credentials for model artifact download:

```
AWS_ACCESS_KEY_ID=<your-access-key>
AWS_SECRET_ACCESS_KEY=<your-secret-key>
AWS_ENDPOINT_URL=https://chi.tacc.chameleoncloud.org:7480
S3_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480
S3_NO_VERIFY_SSL=true
ARTIFACT_BUCKET=proj23-mlflow-artifacts
```

### Step 4 — Verify port 8089 is free

```bash
sudo lsof -iTCP:8089 -sTCP:LISTEN
```

If something is already on 8089, stop it before continuing.

### Step 5 — Start the stack

```bash
docker compose up --build -d
```

This starts all services. The first startup takes longer because:
- `artifact-fetch-worker` downloads GRU ranker + Item2Vec artifacts from S3
- `catalog-sync-worker` maps 100K+ tracks from the music library to Navidrome IDs

### Step 6 — Monitor startup progress

```bash
# Watch all services
docker compose ps

# Follow artifact download (first run only — downloads ~500 MB from S3)
docker compose logs -f artifact-fetch-worker

# Follow catalog sync
docker compose logs -f catalog-sync-worker
```

Expected one-shot jobs to reach `Exited (0)`:
- `navidrome-bootstrap` — creates Navidrome API user
- `artifact-fetch-worker` — downloads serving bundle from S3
- `catalog-sync-worker` — maps music library to playable catalog

Expected long-running services to stay `Up`:
- `postgres`, `redis`, `navidrome`
- `recommendation-api`, `event-api`
- `frontend-web`, `nginx`

Catalog sync completes with:
```
synced XXXXX canonical tracks into playable Navidrome mappings
```

On subsequent restarts, catalog sync skips already-mapped tracks and finishes in seconds.

### Step 7 — Verify the service

```bash
BASE_URL=http://127.0.0.1:8089 bash infra/scripts/healthcheck_demo.sh
```

Open in a browser:
```
http://<VM_PUBLIC_IP>:8089/
```

Sign up with any email and password to start using the app.

---

## Stopping and Restarting

**Stop (preserves all data on disk):**
```bash
docker compose down
```

**Restart:**
```bash
docker compose up --build -d
```

Always use `--build` to pick up any code changes. Data is persisted via bind mounts to `/mnt/mlflow_persist/`, so `docker compose down` never loses database state or downloaded artifacts.

**Full reset (wipes database and artifacts — use with caution):**
```bash
rm -rf /mnt/mlflow_persist/spotiboys/
# Recreate directories (Step 1), then start fresh
```

---

## Useful Commands

```bash
# Live logs
docker compose logs -f recommendation-api
docker compose logs -f event-api
docker compose logs -f nginx

# Database inspection
docker exec spotyboys_service-postgres-1 psql -U postgres -d spotiboys -c "\dt app.*"
docker exec spotyboys_service-postgres-1 psql -U postgres -d spotiboys \
  -c "SELECT COUNT(*) FROM app.navidrome_track_mapping;"

# Check active serving bundle
docker exec spotyboys_service-recommendation-api-1 \
  cat /serving-bundle/Real_service/active/manifest.json
```

---

## Adding Test Users with Personalized Recommendations

The recommendation engine uses per-user centroids from the 30Music training dataset. To test personalized recommendations, insert a training user whose ID matches the centroid keys (1–45175):

```bash
docker exec spotyboys_service-postgres-1 psql -U postgres -d spotiboys << 'SQL'
INSERT INTO app.users (user_id, email, password_hash, display_name)
VALUES (
  'user_40305',
  '30music_40305@test.local',
  'pbkdf2_sha256$210000$3q2+796tvu/erb7v3q2+7w==$/nLX3+g4sd48DpwvMtOXxEOMuIgiZDcfJJ0Ez+EXdAE=',
  '30Music Power User (15126 plays)'
) ON CONFLICT (user_id) DO NOTHING;
SQL
```

Login with:
- Email: `30music_40305@test.local`
- Password: `test123`

This user has 15,126 historical plays in the training data and will receive fully personalized recommendations from the preference NN branch.

---

## Project Structure

```
apps/
  frontend-web/          React SPA
  recommendation-api/    C1–C4 pipeline, stream proxy, auth
  event-api/             playback / feedback ingestion
packages/
  recommendation_engine/ pipeline orchestration
  db_access/             PostgreSQL + in-memory repositories
  navidrome_adapter/     internal media access
  shared_contracts/      API schemas and enums
  config.py              environment-driven config
workers/
  catalog-sync-worker/   maps music library → Navidrome IDs → playable catalog
  artifact-fetch-worker/ downloads serving bundle from S3
  artifact-refresh-worker/ promotes new artifacts to active slot
  parser-export-worker/  exports event delta for retraining
  outcome-deriver-worker/ derives training labels from playback events
  delta-trigger-worker/  triggers delta export pipeline
  restart-monitor-worker/ hot-reload recommendation-api on new artifacts
src/
  ranker/                GRU ranker training + inference
  retriever/             C2 multi-recall retrieval
db/                      PostgreSQL schema migrations
infra/
  nginx/                 reverse proxy config
  scripts/               healthcheck, bootstrap helpers
service_vm/              Airflow DAG and docker-compose for training VM
docs/                    VM setup runbooks
```

---

## Ports

| Port | Service |
|------|---------|
| 8089 | nginx (public ingress — only port that needs to be open) |
| 8001 | recommendation-api (internal) |
| 8002 | event-api (internal) |
| 4533 | Navidrome (internal only, never expose) |
| 5432 | PostgreSQL (internal) |
| 6379 | Redis (internal) |
