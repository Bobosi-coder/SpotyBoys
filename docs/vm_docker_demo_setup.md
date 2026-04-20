# SpotyBoys VM Docker Demo Setup

This runbook starts from a fresh pull on the serving VM and brings up the SpotyBoys VM1 serving stack without stopping or interfering with other Docker workloads on the same machine.

The demo frontend port for this VM run is `8089`.

## What This Starts

The VM1 serving stack includes:

- nginx same-origin ingress
- frontend web app
- recommendation API
- event API
- PostgreSQL
- Redis
- Navidrome
- catalog sync worker
- artifact fetch worker
- fixture music generation for local validation

The stack is isolated by Compose project name. The commands below use:

```bash
COMPOSE_PROJECT_NAME=spotiboys_vm1_demo
```

That prefixes containers, networks, and named volumes so this setup does not reuse or remove unrelated Docker resources.

## Safety Rules For Shared VMs

Do not run broad Docker cleanup commands on the VM:

```bash
# Do not run these on a shared VM
docker system prune
docker stop $(docker ps -q)
docker compose down
```

Use only the project-scoped commands in this document. They affect the `spotiboys_vm1_demo` stack only.

## Step 1: Pull Latest Code

SSH into the VM and go to the repository checkout:

```bash
cd /path/to/SpotyBoys-or-Mlops-project
git status --short
git branch --show-current
git pull
```

If the VM branch is not the serving branch, switch first:

```bash
git fetch origin
git checkout serving_requirements
git pull origin serving_requirements
```

If `git status --short` shows local VM edits, save or inspect them before pulling:

```bash
git status --short
git diff
```

## Step 2: Confirm Docker Is Available

```bash
docker version
docker compose version
docker ps
```

The final `docker ps` is only for visibility. Do not stop existing containers.

## Step 3: Create The VM `.env`

Create `.env` from the example:

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env`:

```bash
nano .env
```

Set the object storage credentials and endpoint values provided for the project:

```bash
AWS_ACCESS_KEY_ID=<provided access key>
AWS_SECRET_ACCESS_KEY=<provided secret key>
AWS_ENDPOINT_URL=https://chi.tacc.chameleoncloud.org:7480
S3_ENDPOINT=https://chi.tacc.chameleoncloud.org:7480
S3_NO_VERIFY_SSL=true
ARTIFACT_BUCKET=proj23-mlflow-artifacts
```

Do not commit `.env`. It is intentionally ignored by git.

## Step 4: Check Port 8089

Before starting, make sure port `8089` is free:

```bash
sudo lsof -iTCP:8089 -sTCP:LISTEN
```

If this prints nothing, port `8089` is available.

If another service is already listening on `8089`, stop here and choose a different value for `SPOTIBOYS_FRONTEND_PORT`.

## Step 5: Verify The VM Music Library

On VM1, songs are expected at:

```bash
/mnt/mlflow_persist_large/music/
```

Verify the directory exists and contains audio files:

```bash
test -d /mnt/mlflow_persist_large/music
find /mnt/mlflow_persist_large/music -type f | head
```

If the music directory is mounted somewhere else, set:

```bash
export SPOTIBOYS_VM_MUSIC_ROOT=/actual/path/to/music
```

The VM library is mounted read-only into Navidrome at `/music`. The application does not download songs from object storage; object storage is used for model artifacts. Songs are read from the VM persistent music mount and streamed through:

```text
browser -> nginx:8089 -> /stream/<track_id> -> recommendation-api -> internal Navidrome -> /mnt/mlflow_persist_large/music
```

## Step 6: Start The Isolated VM Demo Stack

Use the project name and VM frontend port:

```bash
export COMPOSE_PROJECT_NAME=spotiboys_vm1_demo
export SPOTIBOYS_FRONTEND_PORT=8089
export SPOTIBOYS_VM_MUSIC_ROOT=/mnt/mlflow_persist_large/music
export SPOTIBOYS_REQUIRE_FULL_ML_PIPELINE=true

docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-library.yml \
  up --build -d
```

This command does not stop unrelated containers. It only creates or updates containers in the `spotiboys_vm1_demo` Compose project.

Expected external URL:

```text
http://<VM_PUBLIC_IP_OR_HOSTNAME>:8089/
```

For local SSH/browser access on the VM:

```text
http://127.0.0.1:8089/
```

## Step 7: Watch Startup

The first start can take time because the artifact fetch worker downloads the real serving bundle and Item2vec artifacts.

Check status:

```bash
export COMPOSE_PROJECT_NAME=spotiboys_vm1_demo

docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-library.yml \
  ps
```

Expected long-running services:

- `postgres`
- `redis`
- `navidrome`
- `recommendation-api`
- `event-api`
- `frontend-web`
- `nginx`

Expected completed one-shot jobs:

- `artifact-fetch-worker`
- `fixture-music`
- `navidrome-bootstrap`
- `catalog-sync-worker`

If artifact download is still running, inspect:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-library.yml \
  logs -f artifact-fetch-worker
```

Successful artifact fetch ends with a line similar to:

```text
Staged active serving bundle at /serving-bundle/Real_service/active
```

## Step 8: Run Health Checks On Port 8089

Run the health script with the isolated project name and VM base URL:

```bash
export COMPOSE_PROJECT_NAME=spotiboys_vm1_demo
BASE_URL=http://127.0.0.1:8089 bash infra/scripts/healthcheck_demo.sh
```

Expected checks:

- proxy health: `HTTP 200`
- recommendation health: `HTTP 200`
- recommendation ready: `HTTP 200`
- event health: `HTTP 200`
- auth signup/session cookie
- playable track
- mapped stream
- real fixture audio
- unmapped stream fail-closed: `HTTP 404`
- recommendation caps/defaults
- recommendation next
- event idempotency

## Step 9: Open The Demo

In a browser:

```text
http://<VM_PUBLIC_IP_OR_HOSTNAME>:8089/
```

Demo flow:

1. Sign up with any test email and password.
2. The app redirects into the authenticated SpotyBoys UI.
3. Confirm featured tracks and random songs render.
4. Click play.
5. Confirm playback comes through the app backend stream path, not directly from Navidrome.
6. Open the playlist drawer from the bottom dock.

The browser should only see same-origin URLs under port `8089`, for example:

```text
/session/bootstrap
/recommendations/next
/playable-track/<track_id>
/stream/<track_id>
/events/playback
```

It should not see raw Navidrome credentials or direct internal Navidrome URLs.

## Step 10: Verify Real Serving Artifacts Are Staged

Check the active serving bundle inside the project-scoped Docker volume:

```bash
docker run --rm \
  -v spotiboys_vm1_demo_spotiboys-serving-bundle:/serving-bundle \
  alpine:3.20 \
  find /serving-bundle -maxdepth 4 -type f | sort
```

Expected files include:

```text
/serving-bundle/Real_service/active/manifest.json
/serving-bundle/Real_service/active/gru_ranker.pt
/serving-bundle/Real_service/active/gru_ranker_config.json
/serving-bundle/Real_service/active/cooc_session.npz
/serving-bundle/Real_service/active/cooc_playlist.npz
/serving-bundle/Real_service/active/pop_scores.csv
/serving-bundle/Real_service/active/user_centroids.pkl
/serving-bundle/runtime/item2vec/item2vec_128d.npy
/serving-bundle/runtime/item2vec/item2vec_track_to_row.json
```

Inspect the active manifest:

```bash
docker run --rm \
  -v spotiboys_vm1_demo_spotiboys-serving-bundle:/serving-bundle \
  alpine:3.20 \
  cat /serving-bundle/Real_service/active/manifest.json
```

Expected active model version:

```text
20260419_203835
```

## Step 11: Useful Logs

Recommendation API:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-library.yml \
  logs -f recommendation-api
```

Event API:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-library.yml \
  logs -f event-api
```

Navidrome:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-library.yml \
  logs -f navidrome
```

nginx ingress:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-library.yml \
  logs -f nginx
```

## Step 12: Stop Only SpotyBoys

When the demo is done, stop only this isolated Compose project:

```bash
export COMPOSE_PROJECT_NAME=spotiboys_vm1_demo

docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-library.yml \
  down
```

This stops only containers in the `spotiboys_vm1_demo` project.

To preserve downloaded models and database state, do not pass `-v`.

To reset only SpotyBoys demo state later:

```bash
export COMPOSE_PROJECT_NAME=spotiboys_vm1_demo

docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-library.yml \
  down -v
```

Use `down -v` only when you intentionally want to remove the SpotyBoys project volumes.

## VM Library Mode Notes

The VM demo path validates the serving stack with the real VM music mount and real remote model artifacts. The VM library override file is:

```text
docker-compose.vm-library.yml
```

It does three important things:

- skips fixture music generation
- mounts `${SPOTIBOYS_VM_MUSIC_ROOT}` read-only into Navidrome at `/music`
- sets `SPOTIBOYS_MEDIA_MODE=navidrome_vm_library`
- requires the full C1-C4 serving path with `SPOTIBOYS_REQUIRE_FULL_ML_PIPELINE=true`
- builds the playable canonical catalog from `/music/manifest.csv` when present, otherwise from audio filenames

For the real mounted VM music library:

- set `SPOTIBOYS_MEDIA_MODE=navidrome_vm_library`
- mount the VM music root into the Navidrome `/music` path
- ensure catalog reconciliation maps canonical playable track IDs to Navidrome media IDs
- ensure canonical track IDs match the trained artifact track ID namespace for direct C2/C3 output surfacing
- if `recommendation-api` exits with `oom=true`, increase VM/container memory or stop unrelated workloads; do not disable the model stack for the graded demo

Do not expose Navidrome directly to the browser. Keep nginx as the only public ingress and keep playback going through:

```text
GET /stream/<track_id>
```

## Quick Command Summary

```bash
cd /path/to/SpotyBoys-or-Mlops-project
git fetch origin
git checkout serving_requirements
git pull origin serving_requirements

cp .env.example .env
chmod 600 .env
nano .env

sudo lsof -iTCP:8089 -sTCP:LISTEN

export COMPOSE_PROJECT_NAME=spotiboys_vm1_demo
export SPOTIBOYS_FRONTEND_PORT=8089
export SPOTIBOYS_VM_MUSIC_ROOT=/mnt/mlflow_persist_large/music
export SPOTIBOYS_REQUIRE_FULL_ML_PIPELINE=true
docker compose -f docker-compose.yml -f docker-compose.vm-library.yml up --build -d

BASE_URL=http://127.0.0.1:8089 bash infra/scripts/healthcheck_demo.sh
```

Demo URL:

```text
http://<VM_PUBLIC_IP_OR_HOSTNAME>:8089/
```
