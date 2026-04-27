# SpotiBoys Serving & Retraining Pipeline Overview

## Architecture Summary

```
VM1 (129.114.25.207)                        VM2 GPU (10.140.83.179)
─────────────────────                        ──────────────────────
PostgreSQL (user sessions)                   Airflow DAG: retrain_phase2
  ↓                                            ↓ (SSH via airflow_gpu_key)
export_delta.py                              retrain.sh --with-delta
  ↓ S3 upload                                  ↓ download_data.sh (S3)
s3://proj23-mlflow-artifacts/                  ↓ merge_delta.py
  session_event/delta/{stamp}/                 ↓ train item2vec / GRU
  ↓ (Airflow REST API trigger)               upload model → S3
Airflow :8080 → DAG run                         ↓
                                           artifact-fetch-worker (VM1)
                                               ↓
                                           recommendation-api restart
```

---

## Components

### 1. Delta Export (`workers/parser-export-worker/export_delta.py`)

Exports 4 parquet files from PostgreSQL and uploads to S3:

| File | Columns |
|------|---------|
| `session_tracks_addition.parquet` | session_id, user_id, position, track_id, playratio, label |
| `session_meta_addition.parquet` | session_id, user_id |
| `love_addition.parquet` | user_id, track_id |
| `users_addition.parquet` | user_id |

**ID Mapping:**
- `track_id` → `CAST(track_id AS BIGINT)` (30Music int stored as TEXT)
- `user_id` → `user_int_id` BIGSERIAL (offset 100,000 above snapshot max 45,175)
- `session_id` → `session_int_id` BIGSERIAL (offset 3,000,000 above snapshot max 2,764,469)

**Label Derivation (playratio = playback_ms / duration_sec*1000):**
- `> 0.8` → `positive`
- `> 0.2` → `neutral`
- `≤ 0.2` → `skip`

**S3 path:** `s3://proj23-mlflow-artifacts/session_event/delta/{YYYYMMDD_HHMMSS}/`

---

### 2. Delta Trigger Worker (`workers/delta-trigger-worker/trigger_delta_export.py`)

- Runs as a **persistent container** (hourly loop, `sleep(3600)`)
- Checks if new sessions since last checkpoint ≥ 1,000
- If threshold reached: export delta → upload S3 → trigger Airflow DAG
- Airflow warning-only on failure (export result is preserved)

---

### 3. Airflow Trigger (`packages/airflow_trigger.py`)

Calls `POST http://localhost:8080/api/v1/dags/retrain_phase2/dagRuns`  
Auth: Basic `admin:admin` (configurable via env vars)  
Airflow runs on VM1 and internally SSHes to VM2 GPU via `airflow_gpu_key`.

---

### 4. Process Restart (`workers/restart-monitor-worker/monitor_restart.py`)

- Checks for `restart_required.json` marker every minute
- Marker path: `$SPOTIBOYS_ACTIVE_ARTIFACT_ROOT/Real_service/vm1_staged_serving/restart_required.json`
- On detection: `docker restart spotyboys_service-recommendation-api-1` → delete marker
- Requires Docker socket mount: `/var/run/docker.sock`

---

### 5. Demo / Manual Trigger

`POST /admin/trigger-retrain` on recommendation-api:
- Bypasses the 1,000-session threshold
- Forces delta export → S3 upload → Airflow DAG trigger
- Returns `export_path`, `dag_run_id`, `dag_state`

---

## DB Schema (`db/005_int_id_mapping.sql`)

```sql
-- app.users
user_int_id BIGINT  (BIGSERIAL starting at 100,000)

-- app.sessions
session_int_id BIGINT  (BIGSERIAL starting at 3,000,000)

-- app.delta_export_metadata
last_exported_session_int_id BIGINT
```

---

## Environment Variables

| Var | Default | Used by |
|-----|---------|---------|
| `AIRFLOW_BASE_URL` | `http://localhost:8080` | delta-trigger-worker, recommendation-api |
| `AIRFLOW_USERNAME` | `admin` | same |
| `AIRFLOW_PASSWORD` | `admin` | same |
| `AWS_ACCESS_KEY_ID` | — | parser-export-worker, delta-trigger-worker, recommendation-api |
| `AWS_SECRET_ACCESS_KEY` | — | same |
| `AWS_ENDPOINT_URL` | `https://chi.tacc.chameleoncloud.org:7480` | same |
| `S3_NO_VERIFY_SSL` | `true` | same |
| `ARTIFACT_BUCKET` | `proj23-mlflow-artifacts` | same |
| `SPOTIBOYS_ACTIVE_ARTIFACT_ROOT` | `/serving-bundle` | artifact-refresh-worker, restart-monitor-worker |
| `RECOMMENDATION_API_CONTAINER` | `spotyboys_service-recommendation-api-1` | restart-monitor-worker |

---

## Full Pipeline Flow (Auto)

1. Users play music → sessions recorded in PostgreSQL
2. `delta-trigger-worker` checks every hour
3. When ≥ 1,000 new sessions → `export_delta()` runs
4. 4 parquet files written locally + uploaded to S3
5. Airflow REST API called → `retrain_phase2` DAG triggered
6. Airflow SSHes to VM2 GPU → `retrain.sh --with-delta`
7. VM2 downloads parquets, merges with snapshot, retrains model
8. VM2 uploads new model to S3
9. `artifact-fetch-worker` on VM1 detects new model, downloads it
10. `artifact-refresh-worker` writes `restart_required.json` marker
11. `restart-monitor-worker` detects marker → restarts `recommendation-api`
12. `recommendation-api` loads new model on startup
