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
| `feature/navidrome-integration` | Navidrome replaces frontend-web; Recommendations page built in |
| `feature/service-redesign` | **Current** — single API, minimal schema, Bearer auth, delta export loop |
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
sudo mkdir -p /mnt/mlflow_persist/spotiboys/{postgres,navidrome,serving-bundle}
sudo chown -R $USER:$USER /mnt/mlflow_persist/spotiboys/
```

Music files must be present at `/mnt/mlflow_persist_large/music/*.mp3`.
A `manifest.csv` with columns `track_id,title,artist` should also be present there for metadata.

### Step 2 — Clone and checkout

```bash
git clone <repo-url> ~/SpotyBoys
cd ~/SpotyBoys
git checkout feature/service-redesign
```

### Step 3 — Configure environment

```bash
cat > .env << 'EOF'
AWS_ACCESS_KEY_ID=<your-key>
AWS_SECRET_ACCESS_KEY=<your-secret>
AWS_ENDPOINT_URL=https://chi.tacc.chameleoncloud.org:7480
S3_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480
S3_NO_VERIFY_SSL=true
ARTIFACT_BUCKET=proj23-mlflow-artifacts
SPOTIBOYS_PUBLIC_BASE_URL=http://<VM_PUBLIC_IP>:8089
AIRFLOW_BASE_URL=http://host.docker.internal:8080
AIRFLOW_USERNAME=admin
AIRFLOW_PASSWORD=admin
EOF
chmod 600 .env
```

### Step 4 — Start the stack

```bash
docker compose up --build -d
```

**First-boot startup order:**
1. `postgres` passes healthcheck
2. `navidrome-extauth-bootstrap` creates the internal `spotiboys` ExtAuth user
3. In parallel: `seed-users-worker`, `catalog-sync-worker`, `artifact-fetch-worker`
4. `recommendation-api` starts (waits for the one-shot workers above to exit 0)
5. `delta-export-worker` and `nginx` start

First startup is slower because:
- `artifact-fetch-worker` downloads ~500 MB of model artifacts from S3
- `catalog-sync-worker` maps 100K+ tracks from the music library to Navidrome IDs
- `seed-users-worker` imports ~45k pre-seeded 30Music users from S3
- `navidrome` builds the patched image from a recent ExtAuth-capable upstream tag and authenticates users via nginx `Remote-User`

### Step 5 — Monitor startup

```bash
# Overall status
docker compose ps

# Watch one-shot workers
docker compose logs -f navidrome-extauth-bootstrap # creates internal ExtAuth service user
docker compose logs -f seed-users-worker     # imports ~45k 30Music users from S3
docker compose logs -f catalog-sync-worker   # maps music library → Navidrome IDs
docker compose logs -f artifact-fetch-worker # downloads model artifacts (~500 MB)
docker compose logs -f recommendation-api    # confirm API is up
```

One-shot jobs exit with code 0 when done: `navidrome-extauth-bootstrap`,
`seed-users-worker`, `catalog-sync-worker`, `artifact-fetch-worker`.

Long-running services stay `Up`: `postgres`, `redis`, `navidrome`, `recommendation-api`,
`delta-export-worker`, `nginx`.

### Step 6 — Verify

```bash
# API health
curl http://localhost:8089/health

# Check playable track count and model version
curl http://localhost:8089/ready

# Confirm 30Music users were seeded
docker exec spotyboys_service-postgres-1 psql -U postgres -d spotiboys \
  -c "SELECT COUNT(*) FROM app.users WHERE user_int_id < 100000"
# expect: ~45000

# Confirm music catalog was mapped
docker exec spotyboys_service-postgres-1 psql -U postgres -d spotiboys \
  -c "SELECT COUNT(*) FROM app.playable_tracks WHERE is_playable = true"
```

### Step 7 — Open in browser

```
http://<VM_PUBLIC_IP>:8089/
```

Open `/login`, sign in with a SpotyBoys account, then nginx forwards the authenticated
`Remote-User` header to Navidrome. Navidrome auto-creates its matching local user via ExtAuth.

**Testing personalised recommendations with a 30Music user:**
Sign in at `/login` with `40305@navidrome.local` / `test123`. The pre-seeded row is already
there, and `user_int_id=40305` triggers the prefNN branch (15,126 historical plays → fully
personalised). All ~45k pre-seeded accounts are accessible as `{uid}@navidrome.local` /
`test123`.

---

## Service VM — MLflow + Airflow

The Service VM also runs MLflow and Airflow in a separate compose stack at `~/docker/`
(see `service_vm/docker-compose.yaml`).

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

The SSH private key must be at `/home/cc/.ssh/airflow_gpu_key` on the Service VM and its
public key must be in `~/.ssh/authorized_keys` on the GPU VM.

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

Or move credentials to `.env` (recommended).

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

Triggered automatically by Airflow when `delta-export-worker` accumulates ≥1000 new
complete-play events, or run manually:

```bash
docker-compose run training bash scripts/retrain.sh --phase2
```

`retrain.sh --phase2` downloads **all** `session_event/delta/` partitions from S3 recursively
and merges them with the original 30Music snapshot before retraining.

Auto-promotion gate:
```
composite_score ≥ baseline × 0.99  AND  val_loss ≤ baseline_val_loss × 1.05
```

---

## Retraining Data Flow

```
User plays/skips tracks in Navidrome Recommendations page
    │
    ▼  POST /events/playback (Bearer token)
app.playback_events  ← INSERT on playback_start, UPDATE on skip/complete
    │                   (same playback_id UUID reused across all three events)
    │  label derivation at export time:
    │    playratio > 0.8  → "positive"
    │    playratio > 0.2  → "neutral"
    │    playratio ≤ 0.2  → "skip"
    ▼
delta-export-worker (every 24h):
    ├── sync app.loved_tracks   ← Navidrome stars via Subsonic getStarred2
    ├── sync app.playlist_tracks ← Navidrome playlists via getPlaylists/getPlaylist
    ├── export 5 parquets to S3: session_event/delta/{VERSION}/
    │     session_tracks_addition.parquet   (session_id, user_id, position, track_id, playratio, label)
    │     session_meta_addition.parquet     (session_id, user_id)
    │     love_addition.parquet             (user_id, track_id)
    │     users_addition.parquet            (user_id)
    │     playlist_tracks_addition.parquet  (playlist_id, position, track_id)
    ├── write app.delta_checkpoint (watermark)
    ├── if new complete events ≥ 1000 → POST Airflow retrain_phase2 DAG
    └── if skip_rate > 80% → set app.model_status.degraded = true
            └── recommendation-api reads this flag (Redis-cached, 5min TTL)
                  → bypasses GRU ranker, returns popularity-only queue
    │
    ▼  (Airflow SSHOperator → GPU VM)
scripts/retrain.sh --phase2
    ├── download all delta/ partitions from S3
    ├── merge_delta.py — merge snapshot + all delta versions
    ├── rebuild retriever (cooc_session, cooc_playlist, pref_nn, popularity)
    ├── train GRU ranker
    └── promote.py --mode auto → Real_service/{NEW_VERSION}/ in S3
    │
    ▼  (next Service VM restart or artifact-fetch-worker manual run)
new serving bundle loaded by recommendation-api
```

---

## Useful Commands

```bash
# Service health and status
docker compose ps
curl http://localhost:8089/health
curl http://localhost:8089/ready

# Live logs
docker compose logs -f recommendation-api
docker compose logs -f delta-export-worker

# Database inspection
docker exec spotyboys_service-postgres-1 psql -U postgres -d spotiboys -c "\dt app.*"
docker exec spotyboys_service-postgres-1 psql -U postgres -d spotiboys \
  -c "SELECT COUNT(*) FROM app.playback_events"
docker exec spotyboys_service-postgres-1 psql -U postgres -d spotiboys \
  -c "SELECT degraded, reason, updated_at FROM app.model_status"
docker exec spotyboys_service-postgres-1 psql -U postgres -d spotiboys \
  -c "SELECT version, session_int_id_watermark, rows_exported FROM app.delta_checkpoint ORDER BY id DESC LIMIT 5"

# Manually run delta export (skip the 24h wait)
docker compose run --rm delta-export-worker \
  python workers/delta-export-worker/export_and_trigger.py --once

# Check S3 delta partitions
aws s3 ls s3://proj23-mlflow-artifacts/session_event/delta/ \
  --endpoint-url https://chi.tacc.chameleoncloud.org:7480 --no-verify-ssl

# Manually trigger Airflow retraining (requires Bearer token)
TOKEN=$(curl -s -X POST http://localhost:8089/spotiboys/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"40305@navidrome.local","password":"test123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
curl -s -X POST http://localhost:8089/admin/trigger-retrain \
  -H "Authorization: Bearer $TOKEN"

# Re-seed 30Music users (if seed-users-worker failed at first boot)
docker compose run --rm seed-users-worker python scripts/seed_30music_users.py

# Stopping and restarting
docker compose down                        # stop (data preserved on bind mounts)
docker compose up --build -d               # restart with code changes

# Full reset (wipes DB and artifacts — use with caution)
docker compose down
rm -rf /mnt/mlflow_persist/spotiboys/
# Re-run Step 1, then docker compose up --build -d
```

---

## Monitoring

| Tool | URL | What it shows |
|------|-----|--------------|
| Grafana | `:3000` | Serving metrics dashboards |
| MLflow | `<Service VM>:8000` | Training experiment runs, metrics, artifacts |
| Airflow | `<Service VM>:8080` | DAG run history, Phase 2 retraining jobs |
| `/monitoring/summary` | `:8089/monitoring/summary` | Live event counts, model status, last export |

Grafana credentials: `admin` / `spotiboys` (anonymous viewer access enabled).

---

## Project Structure

```
apps/
  recommendation-api/    auth, session bootstrap, C1–C4 pipeline,
                         playback event recording, stream proxy, cover art
navidrome-patches/       Navidrome fork (patches-only)
  Dockerfile             clone + patch + build
  ui/src/
    recommendations/     RecommendationsPage + useSpotiboysSession
    eventbridge/         SpotiboysEventBridge (playback event capture)
  patches/               unified diffs for 4 Navidrome source files
packages/
  recommendation_engine/ pipeline orchestration (C1–C4)
  db_access/             PostgreSQL + Redis + in-memory repositories
  navidrome_adapter/     internal media access via Subsonic API
  shared_contracts/      API schemas and enums
  auth.py                Bearer token helpers
  config.py              environment-driven config
workers/
  catalog-sync-worker/   maps music library → Navidrome IDs → playable catalog
  artifact-fetch-worker/ downloads serving bundle from S3
  delta-export-worker/   24h loop: sync loved/playlists, export parquets,
                         trigger Airflow, update model health flag
scripts/
  seed_30music_users.py  one-time: seeds ~45k 30Music users from S3 parquet
db/
  001_init.sql           single app schema (9 tables)
src/
  ranker/                GRU ranker: model, training, inference
  retriever/             C2 multi-recall retrieval + pref NN
infra/
  nginx/                 reverse proxy config
  grafana/               Grafana dashboards + provisioning
  scripts/               Navidrome bootstrap
service_vm/              Airflow DAG (retrain_phase2) + docker-compose
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
| 4533 | Navidrome | Internal only |
| 5432 | PostgreSQL | Internal only |
| 6379 | Redis | Internal only |

---

## Database Schema (app schema)

```
app.users              all users: pre-seeded 30Music (user_int_id 1–45175) +
                       online signups (user_int_id ≥ 100000)
app.auth_sessions      Bearer token store (token_hash, expires_at, revoked_at)
app.rec_sessions       recommendation sessions (session_int_id starts at 3000000)
app.playback_events    playback lifecycle: INSERT on start, UPDATE on skip/complete
app.loved_tracks       synced from Navidrome stars via Subsonic getStarred2
app.user_playlists     synced from Navidrome playlists
app.playlist_tracks    synced playlist track entries
app.playable_tracks    catalog: track metadata + navidrome_track_id for stream proxy
app.model_status       single-row soft-rollback flag (degraded, reason)
app.delta_checkpoint   export watermarks (session_int_id, exported_at, rows_exported)
```
