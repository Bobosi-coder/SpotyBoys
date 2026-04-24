# SpotyBoys — Music Recommendation System

A full-stack music recommendation system with a GRU session ranker, co-occurrence retrieval, and Navidrome as the integrated media player and UI. The recommendation pipeline runs entirely on-prem; model retraining is automated via Airflow on a separate Service VM.

---

## System Overview

```
                        ┌──────────────────────────────────┐
                        │         Service VM               │
                        │  ┌──────────────┐  ┌──────────┐ │
     Browser            │  │   Navidrome  │  │  nginx   │ │
       │                │  │  (patched)   │◄─┤  :8089   │◄├── users
       │                │  └──────────────┘  └──────────┘ │
       │                │  ┌──────────────┐               │
       │                │  │recommendation│               │
       │                │  │   -api :8001 │               │
       │                │  └──────────────┘               │
       │                │  ┌──────────────┐               │
       │                │  │  event-api   │               │
       │                │  │     :8002    │               │
       │                │  └──────────────┘               │
       │                │  ┌──────┐ ┌─────┐ ┌──────────┐ │
       │                │  │  PG  │ │Redis│ │ Workers  │ │
       │                │  └──────┘ └─────┘ └──────────┘ │
       │                └─────────────────────────────────┘
       │                           │  MLflow tracking + S3 artifacts
       │                ┌──────────▼──────────────────────┐
       │                │       Service VM                │
       │                │  MLflow :8000  Airflow :8080    │
       │                └──────────────────────────────────┘
       │                           │  SSH (Airflow DAG)
       │                ┌──────────▼──────────────────────┐
       └────────────────│         GPU VM                  │
                        │  GRU ranker + retriever training │
                        └──────────────────────────────────┘
```

**Recommendation pipeline (C1–C4):**
- **C1**: Item2Vec embeddings, co-occurrence matrices, popularity scores (offline artifacts)
- **C2**: Multi-recall retrieval — co-occurrence (100 candidates) + preference NN (80) + popularity (20)
- **C3**: GRU ranker inference on session prefix
- **C4**: Policy reranking, diversity filter, recency filter

---

## Branches

| Branch | Purpose |
|--------|---------|
| `serving_requirements` | Original service stack with separate frontend-web |
| `feature/navidrome-integration` | **Current** — Navidrome replaces frontend-web; Recommendations page built in |
| `feature/gpu-docker-training` | GPU VM training pipeline (Phase 1 + Phase 2 + promote) |

---

## Service VM — Deploying the Stack

### Prerequisites

- Ubuntu 22.04 VM (Chameleon Cloud)
- Docker + Docker Compose v2
- Two persistent block storage mounts:
  - `/mnt/mlflow_persist/` — databases and model artifacts (~50 GB)
  - `/mnt/mlflow_persist_large/` — MP3 music library (~200 GB)
- Security group: port **8089** open (only public port needed)
- S3 credentials for `proj23-mlflow-artifacts`

### Step 1 — Prepare storage

```bash
sudo mkdir -p /mnt/mlflow_persist/spotiboys/{postgres,navidrome,serving-bundle,object-storage}
sudo chown -R $USER:$USER /mnt/mlflow_persist/spotiboys/
```

Music files must be present at `/mnt/mlflow_persist_large/music/*.mp3`.
If a `manifest.csv` exists there with `track_id,title,artist` columns, it is used for metadata. Otherwise filenames are used as titles.

### Step 2 — Clone and checkout

```bash
git clone <repo-url> ~/SpotyBoys
cd ~/SpotyBoys
git checkout feature/navidrome-integration
```

### Step 3 — Configure environment

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

```
AWS_ACCESS_KEY_ID=<your-access-key>
AWS_SECRET_ACCESS_KEY=<your-secret-key>
AWS_ENDPOINT_URL=https://chi.tacc.chameleoncloud.org:7480
S3_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480
S3_NO_VERIFY_SSL=true
ARTIFACT_BUCKET=proj23-mlflow-artifacts
```

### Step 4 — Start the stack

```bash
docker compose up --build -d
```

The first startup is slower because:
- `artifact-fetch-worker` downloads ~500 MB of model artifacts from S3
- `catalog-sync-worker` maps 100K+ tracks from the music library to Navidrome IDs
- `navidrome` builds from source (Navidrome fork with Recommendations page)

### Step 5 — Monitor startup

```bash
docker compose ps
docker compose logs -f artifact-fetch-worker   # one-shot, downloads model
docker compose logs -f catalog-sync-worker      # one-shot, maps music catalog
```

One-shot jobs reach `Exited (0)`:
- `navidrome-bootstrap`, `artifact-fetch-worker`, `catalog-sync-worker`

Long-running services stay `Up`:
- `postgres`, `redis`, `navidrome`, `recommendation-api`, `event-api`, `nginx`
- `serving-monitor-worker`, `rollback-check-worker`, `live-data-monitor-worker`

### Step 6 — Open in browser

```
http://<VM_PUBLIC_IP>:8089/
```

Log in with any Navidrome account. Navigate to **Recommendations** in the left sidebar.

### Step 7 — Add a test user with personalised recommendations

The preference NN branch uses 30Music user centroids (IDs 1–45175). To test personalisation:

```bash
# Create Navidrome account with username "40305" via the UI, then:
docker exec spotyboys_service-postgres-1 psql -U postgres -d spotiboys << 'SQL'
INSERT INTO app.users (user_id, email, password_hash, display_name, user_int_id)
VALUES (
  'user_40305',
  '40305@navidrome.local',
  'pbkdf2_sha256$210000$3q2+796tvu/erb7v3q2+7w==$/nLX3+g4sd48DpwvMtOXxEOMuIgiZDcfJJ0Ez+EXdAE=',
  '30Music Power User (15126 plays)',
  40305
) ON CONFLICT (user_id) DO NOTHING;
SQL
```

Log into Navidrome with username `40305`. The Recommendations page will now use the preference NN branch (15,126 historical plays → fully personalised).

See `docs/navidrome_integration.md` for the full auth/ID mapping explanation.

---

## Service VM — MLflow + Airflow

The Service VM also runs MLflow and Airflow in a separate compose stack at `~/docker/`.

```bash
cd ~/docker
docker compose up -d
```

Services:
- **MLflow** at `:8000` — experiment tracking for training runs
- **Airflow** at `:8080` — orchestrates Phase 2 retraining (SSHOperator → GPU VM)

After startup, configure the `gpu_vm_ssh` connection in Airflow UI (Admin → Connections):

| Field | Value |
|-------|-------|
| Conn Id | `gpu_vm_ssh` |
| Conn Type | SSH |
| Host | `<GPU VM IP>` |
| Username | `cc` |
| Private Key File | `/opt/airflow/ssh/airflow_gpu_key` |

The SSH private key must be at `/home/cc/.ssh/airflow_gpu_key` on the Service VM and its public key must be in `~/.ssh/authorized_keys` on the GPU VM.

---

## GPU VM — Training Pipeline

### Prerequisites

- NVIDIA GPU + driver (`nvidia-smi` works)
- Docker + Docker Compose v2 with NVIDIA container runtime

### Setup

```bash
git clone <repo-url> ~/SpotyBoys
cd ~/SpotyBoys
git checkout feature/gpu-docker-training
```

Edit `docker-compose.yml` — update:
```yaml
- MLFLOW_TRACKING_URI=http://<Service VM IP>:8000/
- AWS_ACCESS_KEY_ID=<key>
- AWS_SECRET_ACCESS_KEY=<secret>
```

Or move credentials to `.env` (recommended — keeps secrets out of the file).

```bash
docker-compose build   # once
```

### Phase 1 — Initial training on 30Music data

```bash
docker-compose run training bash scripts/retrain.sh \
  --phase1 --retrieve-version 20260417_051148
```

Review runs in MLflow UI at `http://<Service VM>:8000/`, experiment `"training before online service"`.

Promote best run manually:
```bash
docker-compose run training python3 scripts/promote.py \
  --mode manual --retrieve-version 20260417_051148
```

This uploads `Real_service/{VERSION}/` to S3 and saves `Real_service/baseline.json`.

### Phase 2 — Retrain after online delta

Triggered automatically by Airflow, or run manually:
```bash
docker-compose run training bash scripts/retrain.sh --phase2
```

Auto-promotion gate:
```
composite_score ≥ baseline × 0.99  AND  val_loss ≤ baseline_val_loss × 1.05
```

---

## Retraining Data Flow

```
app.playback_events (live service)
    │
    ▼
outcome-deriver-worker → app.recommendation_outcomes (derived labels)
    │
    ▼
parser-export-worker → S3: session_tracks_addition.parquet + 3 others
    │
    ▼
delta-trigger-worker → Airflow REST API → retrain_phase2 DAG
    │
    ▼ (GPU VM via SSHOperator)
scripts/retrain.sh --phase2
    ├── rebuild retriever (cooc + pref_nn + popularity)
    ├── train GRU ranker (best Phase 1 hyperparams from MLflow)
    └── promote.py --mode auto → Real_service/{VERSION}/ in S3
    │
    ▼ (Service VM)
artifact-fetch-worker → downloads new bundle
rollback-check-worker → monitors metrics, auto-reverts if degraded
```

---

## Monitoring

| Tool | URL | What it shows |
|------|-----|--------------|
| Grafana | `:3000` | Serving metrics dashboards, data quality, model performance |
| MLflow | `<Service VM>:8000` | Training experiment runs, metrics, artifacts |
| Airflow | `<Service VM>:8080` | DAG run history, Phase 2 retraining jobs |

Grafana credentials: `admin` / `spotiboys` (anonymous viewer access enabled).

---

## Stopping and Restarting

```bash
# Stop (preserves all data on bind mounts)
docker compose down

# Restart with code changes
docker compose up --build -d

# Full reset (wipes DB and artifacts — caution)
rm -rf /mnt/mlflow_persist/spotiboys/
# Re-run Step 1, then docker compose up --build -d
```

---

## Useful Commands

```bash
# Service health
BASE_URL=http://127.0.0.1:8089 bash infra/scripts/healthcheck_demo.sh

# Live logs
docker compose logs -f recommendation-api
docker compose logs -f event-api
docker compose logs -f serving-monitor-worker

# Database
docker exec spotyboys_service-postgres-1 psql -U postgres -d spotiboys -c "\dt app.*"
docker exec spotyboys_service-postgres-1 psql -U postgres -d spotiboys \
  -c "SELECT COUNT(*) FROM app.navidrome_track_mapping;"
docker exec spotyboys_service-postgres-1 psql -U postgres -d spotiboys \
  -c "SELECT COUNT(*) FROM app.playback_events;"

# Check active model version
docker exec spotyboys_service-recommendation-api-1 \
  cat /serving-bundle/Real_service/active/manifest.json

# S3 artifact listing (from GPU VM or Service VM)
aws s3 ls s3://proj23-mlflow-artifacts/ \
  --endpoint-url https://chi.tacc.chameleoncloud.org:7480 \
  --no-verify-ssl
```

---

## Project Structure

```
apps/
  recommendation-api/    C1–C4 pipeline, stream proxy, auth
  event-api/             playback / feedback ingestion
navidrome-patches/       Navidrome fork (patches-only)
  Dockerfile             clone + patch + build
  ui/src/
    recommendations/     RecommendationsPage + useSpotiboysSession
    eventbridge/         SpotiboysEventBridge (event capture)
  patches/               unified diffs for 4 Navidrome source files
packages/
  recommendation_engine/ pipeline orchestration (C1–C4)
  db_access/             PostgreSQL + in-memory repositories
  navidrome_adapter/     internal media access via Subsonic API
  shared_contracts/      API schemas and enums
  config.py              environment-driven config
workers/
  catalog-sync-worker/   maps music library → Navidrome IDs → playable catalog
  artifact-fetch-worker/ downloads serving bundle from S3
  artifact-refresh-worker/ promotes new artifacts to active slot
  parser-export-worker/  exports playback event delta for retraining
  outcome-deriver-worker/ derives training labels from playback events
  delta-trigger-worker/  triggers Airflow retraining DAG
  serving-monitor-worker/ rolls up serving metrics every 5 minutes
  rollback-check-worker/ monitors metrics, auto-reverts bad models
  live-data-monitor-worker/ monitors live data health
src/
  ranker/                GRU ranker: model, training, inference
  retriever/             C2 multi-recall retrieval + pref NN
scripts/                 (GPU VM) retrain.sh, tune_phase1.py, promote.py
db/                      PostgreSQL schema migrations (001–007)
infra/
  nginx/                 reverse proxy config
  grafana/               Grafana dashboards + provisioning
  scripts/               healthcheck, Navidrome bootstrap
service_vm/              Airflow DAG (retrain_phase2) + docker-compose
docs/
  navidrome_integration.md   integration workflow, auth mapping, DB schema
```

---

## Ports

| Port | Service | Accessible from |
|------|---------|----------------|
| **8089** | nginx — public ingress | Internet |
| 8000 | MLflow (Service VM) | GPU VM, team |
| 8080 | Airflow (Service VM) | Team |
| 3000 | Grafana | Team |
| 8001 | recommendation-api | Internal only |
| 8002 | event-api | Internal only |
| 4533 | Navidrome | Internal only |
| 5432 | PostgreSQL | Internal only |
| 6379 | Redis | Internal only |
