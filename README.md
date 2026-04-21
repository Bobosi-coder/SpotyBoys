# SpotyBoys — GPU Training Pipeline

GRU ranker + retriever retraining pipeline. Runs inside a Docker container on the GPU VM (`192.5.87.187`). Logs experiments to MLflow on the Service VM (`129.114.25.207:8000`). Uploads artifacts to S3 (Chameleon Cloud object storage).

---

## Architecture

```
GPU VM (192.5.87.187)
  → docker-compose run training bash scripts/retrain.sh --phase1 ...
  → docker-compose run training bash scripts/retrain.sh --phase2

Training container
  → MLflow HTTP API → Service VM 129.114.25.207:8000
  → S3 (chi.tacc.chameleoncloud.org:7480)  proj23-mlflow-artifacts
```

**Two phases:**
- **Phase 1** — Train on 30Music data only. Two fixed-config runs (3 and 5 epochs). Review MLflow, manually promote best run.
- **Phase 2** — Retrain after online delta arrives. Rebuilds retriever, fetches best Phase 1 hyperparams, trains, auto-promotes if metrics pass threshold.

---

## Prerequisites

- GPU VM with NVIDIA driver installed (`nvidia-smi` works)
- Docker + Docker Compose v2 with NVIDIA runtime (`nvidia-ctk`)
- S3 credentials for `proj23-mlflow-artifacts`
- Service VM running MLflow at `129.114.25.207:8000`
- Retriever artifacts already uploaded under `Retrieve/{VERSION}/` in S3

---

## Setup

### Step 1 — Clone and checkout

```bash
git clone <repo-url> ~/SpotyBoys
cd ~/SpotyBoys
git checkout feature/gpu-docker-training
```

### Step 2 — Set S3 credentials in docker-compose.yml

Edit the `environment:` block in `docker-compose.yml`:

```yaml
environment:
  - AWS_ACCESS_KEY_ID=<your-access-key>
  - AWS_SECRET_ACCESS_KEY=<your-secret-key>
  - AWS_ENDPOINT_URL=https://chi.tacc.chameleoncloud.org:7480
  - MLFLOW_TRACKING_URI=http://129.114.25.207:8000/
```

### Step 3 — Build the container (once)

```bash
docker-compose build
```

---

## Phase 1 — Initial Training

Trains on 30Music data with two fixed-config runs (epochs 3 and 5).

### Run training

```bash
docker-compose run training bash scripts/retrain.sh --phase1 --retrieve-version 20260417_051148
```

Replace `20260417_051148` with the actual retriever version in S3.

**What it does:**
1. Downloads 30Music snapshot from S3 (`scripts/download_data.sh --no-delta`)
2. Builds ranker training data (`src.ranker.data.build`)
3. Runs two fixed-config training jobs via `scripts/tune_phase1.py`
4. Logs both runs to MLflow experiment `"training before online service"`

### Review results

Open MLflow UI: `http://129.114.25.207:8000/`
Find experiment `"training before online service"`, compare runs by `val_ndcg5`.

Composite score: `0.5 × NDCG5 + 0.3 × HR5 + 0.2 × MRR5`

### Promote best run

```bash
docker-compose run training python3 scripts/promote.py \
  --mode manual \
  --retrieve-version 20260417_051148
```

**What it does:**
- Queries MLflow for best run by composite score
- Downloads `gru_ranker.pt` + `gru_ranker_config.json` from MLflow artifact store
- Copies retriever artifacts from `Retrieve/20260417_051148/` in S3
- Uploads everything to `s3://proj23-mlflow-artifacts/Real_service/{VERSION}/`
- Writes `manifest.json`
- Saves `Real_service/baseline.json` (used by Phase 2 promotion gate)

---

## Phase 2 — Retrain After Online Delta

Triggered by Airflow on the Service VM via SSHOperator, or run manually.

```bash
docker-compose run training bash scripts/retrain.sh --phase2
```

**What it does:**
1. Downloads snapshot + online delta from S3
2. Merges delta into training data (`scripts/merge_delta.py`)
3. Rebuilds all retriever artifacts (split → cooc → popularity → pref_nn)
4. Uploads new `Retrieve/{VERSION}/` to S3
5. Fetches best Phase 1 hyperparams from MLflow (`scripts/get_best_params.py`)
6. Trains ranker with those hyperparams (experiment: `"retraining after online service"`)
7. Auto-promotes via `scripts/promote.py --mode auto`

### Promotion gate (auto mode)

```
new_composite >= baseline_composite × 0.99   (within 1% of Phase 1 best)
AND new_val_loss <= baseline_val_loss × 1.05  (loss not degraded >5%)
```

If the gate fails, `promote.py` exits with code 1 — no artifacts are uploaded.

---

## Artifact Layout in S3 (`proj23-mlflow-artifacts`)

```
Retrieve/{VERSION}/
  cooc_session.npz
  cooc_playlist.npz
  user_centroids.pkl
  pop_scores.csv
  split_train.npy  split_val.npy  split_test.npy

Real_service/{VERSION}/
  manifest.json
  gru_ranker.pt
  gru_ranker_config.json
  cooc_session.npz  ...  (retriever artifacts)

Real_service/baseline.json    ← Phase 1 best metrics (Phase 2 gate reference)
Real_service/active/          ← symlink-equivalent; written by artifact-fetch-worker
```

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/retrain.sh` | Main orchestrator — `--phase1` or `--phase2` |
| `scripts/tune_phase1.py` | Two fixed-config training runs, logged to MLflow |
| `scripts/promote.py` | Promote best run to `Real_service/` in S3 |
| `scripts/get_best_params.py` | Query MLflow for best Phase 1 hyperparams (stdout → eval) |
| `scripts/merge_delta.py` | Merge online delta into training snapshot |
| `scripts/download_data.sh` | Download training data from S3 |
| `scripts/upload_results.sh` | Upload extra artifacts if needed |

---

## Local Artifacts (bind-mounted)

```
artifacts/
  item2vec/
  retriever/split/   cooc/   popularity/   pref_nn/
  ranker/
logs/
```

These directories are bind-mounted into the container. Contents are excluded from git (see `.gitignore`).

---

## Logs

Training logs are written to `logs/` and tee'd to stdout:

```bash
# Follow a running job
docker-compose logs -f training

# Check a specific log file
cat logs/ranker_train_20260501_120000.log
```
