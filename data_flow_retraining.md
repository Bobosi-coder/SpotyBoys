# Retraining-Centered Data Flow Design

## Real-Time Adaptive Music Recommender  
### Team Spoty Boys  

Prepared for Overleaf integration  

---

## Table of Contents

- Retraining-Centered Data Flow Design
- Purpose
- Design Goals
- High-Level Architecture
- Separation of Responsibilities
- Object Storage Layout
- Data Repositories and Their Roles
- Parser Role on VM1
- Delta Generation Trigger
- Retraining Input Assembly on VM2
- Retraining Pipeline on VM2
- Evaluation and Promotion
- Serving Artifact Refresh on VM1
- Internal Model Inference Contracts
- End-to-End Data Flow
- Data Versioning Policy
- Explicit Open Items
- Final Design Stance

---

# Retraining-Centered Data Flow Design

## Purpose

This chapter defines the retraining-centered data flow across three runtime domains:

- **VM1_serving**: user-facing serving, event collection, session logging, parser-based export, and serving artifact refresh  
- **VM2_retraining**: offline retraining, evaluation, and model/artifact promotion  
- **S3-compatible Object Storage**: versioned cross-VM handoff layer for session-event datasets, retriever artifacts, and serving-ready promoted model bundles  

The goal is to support a production-like feedback loop in which new user interactions collected during serving are transformed into retraining-compatible datasets, used to retrain the recommendation pipeline, and then promoted back into serving.

---

## Design Goals

The retraining-centered data flow is designed to satisfy the following goals:

- preserve a clear separation between **online serving** and **offline retraining**
- ensure that serving does not depend on retraining VM availability
- version all data used for retraining
- keep the retraining input format compatible with the existing ML pipeline
- allow retraining to consume both the frozen offline base dataset and newly accumulated online user sessions
- allow serving to refresh to a new promoted model/artifact version through a simple and robust restart-based workflow

---

## High-Level Architecture

The retraining-centered design is divided into three logical domains.

### VM1_serving

VM1 hosts all user-facing and serving-time components:

- frontend / web app  
- recommendation service  
- event ingestion API  
- PostgreSQL  
- Redis  
- parser  
- Navidrome  
- persistent music storage  

VM1 is responsible for:

- collecting online interaction data  
- storing raw user actions and logs in PostgreSQL  
- detecting when sufficient new sessions have accumulated  
- exporting new retraining-compatible parquet batches to object storage  
- checking for promoted serving artifacts  
- restarting the serving process when a new version is available  

---

### VM2_retraining

VM2 is GPU-enabled and is responsible only for offline workflows:

- downloading retraining input data from object storage  
- assembling a full retraining dataset from snapshot and delta batches  
- retraining the retriever and ranker pipeline  
- evaluating new model performance  
- logging run information to MLflow  
- publishing promoted artifacts back to object storage  

VM2 is never part of the user-facing request path.

---

### Object Storage

Object storage is the authoritative exchange layer between VM1 and VM2.

It stores:

- frozen base datasets  
- online delta datasets  
- retriever artifacts  
- promoted serving artifacts  
- manifest files for version readiness  
- MLflow artifact directories  

Object storage is not used as a real-time serving dependency during inference. Instead, the serving VM downloads approved artifacts locally and then loads them into memory during service startup.

---

## Separation of Responsibilities

### VM1_serving owns

- online serving  
- user interaction capture  
- raw session and event storage in PostgreSQL  
- parser execution  
- export of retraining-compatible delta parquet batches  
- serving artifact download and version refresh  
- process restart to apply new artifact versions  

---

### VM2_retraining owns

- retraining orchestration  
- dataset assembly from snapshot and delta  
- retriever artifact regeneration  
- GRU ranker retraining  
- offline evaluation  
- promotion decision  
- publication of serving-ready versions  

---

### Object Storage owns

- cross-VM versioned data exchange  
- retraining input history  
- promoted serving bundles  
- immutable historical artifacts for audit and reproducibility  

---

## Object Storage Layout

```bash
proj23-mlflow-artifacts/
|
|-- mlflow/{experiment_id}/{run_id}/artifacts/
|
|-- Retrieve/{YYYYMMDD_HHMMSS}/
|   |-- cooc_session.npz
|   |-- cooc_playlist.npz
|   |-- user_centroids.pkl
|   `-- pop_scores.csv
|
|-- Real_service/{YYYYMMDD_HHMMSS}/
|   |-- gru_ranker.pt
|   |-- gru_ranker_config.json
|   |-- cooc_session.npz
|   |-- cooc_playlist.npz
|   |-- user_centroids.pkl
|   |-- pop_scores.csv
|   `-- manifest.json
|
|-- Item2vec/
|   |-- item2vec_128d.npy
|   |-- item2vec_track_to_row.json
|   |-- item2vec_catalog.csv
|   |-- item2vec_corpus.parquet
|   |-- playlist_tracks_i2v.parquet
|   `-- playlist_meta_i2v.parquet
|
|-- session_event/
|   |-- snapshot/
|   |   |-- session_tracks_i2v.parquet
|   |   |-- session_meta_i2v.parquet
|   |   |-- love_i2v.parquet
|   |   `-- users_i2v.parquet
|   `-- delta/{YYYYMMDD_HHMMSS}/
|       |-- session_tracks_addition.parquet
|       |-- session_meta_addition.parquet
|       |-- love_addition.parquet
|       |-- users_addition.parquet
|       `-- manifest.json
|
`-- Raw_data/30music_parsed/
```

### Notes

- snapshot/ is frozen and created from the original offline dataset.  
- Each delta/{VERSION}/ directory is append-only and versioned.  
- manifest.json is the readiness signal.  
- The serving VM reads only from Real_service/.  
- The retraining VM reads from snapshot/, delta/, and Item2vec/.  
- Delta file names use the `_addition` suffix to match VM2 merge_delta.py expectations.  

---

## Data Repositories and Their Roles

### PostgreSQL on VM1

PostgreSQL is the online system-of-record for raw user interaction data.

It stores:

- recommendation impressions  
- playback events  
- explicit feedback such as love / dislike  
- session linkage and serving-side metadata  

---

### Redis on VM1

Redis stores hot online state only:

- recent session track history  
- recent labels  
- queue / seen state  
- cached user session context needed for serving  

---

### Persistent Volume on VM1

Persistent storage on VM1 is used for:

- Navidrome music library  
- local downloaded serving artifacts  
- runtime files needed across restarts  

---

### MLflow Persistent Volume on VM2

VM2 stores MLflow tracking metadata and run history.

---

## Parser Role on VM1

### Parser input

- session-level identity  
- user identity  
- playback order  
- track identity  
- user feedback  

---

### Parser output

| file | columns |
|------|---------|
| session_tracks_addition.parquet | session_id, user_id, position, track_id, playratio, label |
| session_meta_addition.parquet | session_id, user_id |
| love_addition.parquet | user_id, track_id |
| users_addition.parquet | user_id |

All IDs are int64 to match snapshot schema.

---

### ID mapping

Online system uses TEXT UUIDs; the ML pipeline requires int64.

- **track_id**: `app.playable_tracks.track_id` is the 30Music integer ID stored as TEXT → `CAST(track_id AS BIGINT)`  
- **user_int_id**: BIGSERIAL column on `app.users`, sequence starts at 100,000 (snapshot max user_id: 45,175)  
- **session_int_id**: BIGSERIAL column on `app.sessions`, sequence starts at 3,000,000 (snapshot max session_id: 2,764,469)  

---

### Label derivation

playratio = playback_ms / (duration_sec × 1000), taken from the terminal event (skip or complete) per track per session.

| playratio | label |
|-----------|-------|
| > 0.8 | positive |
| > 0.2 | neutral |
| ≤ 0.2 | skip |

---

### Schema compatibility requirement

Every delta parquet file must have the exact same schema as snapshot.

---

## Delta Generation Trigger

- default: 1000 sessions  
- checkpoint tracked via `session_int_id` (BIGINT) in `app.delta_export_metadata`

---

## Retraining Input Assembly on VM2

- snapshot + all delta

---

## Retraining Pipeline on VM2

1. load data  
2. rebuild retriever  
3. train GRU  
4. evaluate  
5. log  
6. promote  

---

## Evaluation and Promotion

Real_service/{VERSION}/

---

## Serving Artifact Refresh on VM1

1. download  
2. replace  
3. restart  

---

## Internal Model Inference Contracts

```json
{
  "content_id": "...",
  "user_id": "...",
  "session_prefix": {
    "track_ids": [],
    "labels": [],
    "length": 0
  },
  "candidates": [],
  "ground_truth_id": "..."
}
```

---

## End-to-End Data Flow

Online → Offline → Refresh

---

## Data Versioning Policy

Snapshot: immutable  
Delta: append-only  

---

## Explicit Open Items

- schema  
- labels  
- metrics  

---

## Final Design Stance

- VM1 → serving  
- VM2 → retraining  
- object storage → exchange  
