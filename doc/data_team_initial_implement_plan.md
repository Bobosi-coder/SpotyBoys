# Data Team Initial Implementation Plan

## Goal

This document describes the initial data-system implementation for the SpotyBoys music recommender project. The goal is to turn an already-parsed 30Music raw snapshot into:

- versioned processed training-ready Item2Vec data
- versioned retriever feature artifacts
- course-facing object-storage releases with manifests and lineage metadata

The current implementation deliberately uses a simple, non-interactive sequence of Python modules and shell runners instead of a workflow orchestrator. This satisfies the initial implementation requirement while keeping the pipeline reproducible from repository artifacts alone.

## Current Status

### Already completed

- Parsed 30Music CSV raw snapshot is stored in Chameleon object storage.
- Local preprocessing code exists for:
  - raw parsing
  - Item2Vec Stage A-D
  - retriever split/co-occurrence/preference-NN/popularity artifacts
- PostgreSQL bootstrap/schema exists for future dataset and serving metadata registration.
- MLflow logging is already used by the local Item2Vec and retriever build scripts.

### Still missing before the initial implementation is course-ready

- a clean object-storage prefix contract
- explicit dataset and feature version naming
- manifests that describe lineage and outputs
- release scripts that publish processed and feature artifacts into versioned prefixes
- a single data-design document that ties storage, transformations, and lineage together

## Storage Repositories

### 1. Chameleon Object Storage

Primary persistent data repository for this stage.

| Prefix | Stored Data | Written By | Update Pattern | Versioning |
|---|---|---|---|---|
| `data/raw/content/30music_parsed/` | Parsed 30Music CSV snapshot (`tracks.csv`, `session_tracks.csv`, `events.csv`, etc.) | External ingest / one-time parsing pipeline | Rare snapshot updates | Currently fixed-path snapshot; tracked as `source_version` in manifests |
| `processed/item2vec/{dataset_version}/` | Item2Vec-ready processed artifacts (`item2vec_corpus.parquet`, filtered parquet tables, embedding files, catalog, manifest) | `publish_item2vec_release.py` | New immutable release per pipeline run | Explicit `dataset_version` in object key |
| `features/retriever/{feature_version}/` | Offline retriever features (`split_*.npy`, `cooc_*.npz`, `user_centroids.pkl`, `pop_scores.csv`, manifest) | `publish_retriever_release.py` | New immutable release per pipeline run | Explicit `feature_version` in object key |
| `manifests/releases/...` | Central copies of release manifests | Release publish scripts | Append-only | Versioned by release id |

### 2. PostgreSQL

Metadata and future production-log repository.

| Schema/Table Group | Stored Data | Written By | Update Pattern | Versioning |
|---|---|---|---|---|
| `processed.*` | dataset artifact metadata, split metadata | Initial implementation: manual/next-step registration | Per release | Version fields already exist in schema |
| `ml.*` | model and dataset version registry | Initial implementation: planned immediate follow-up after manifest publishing | Per release/model publish | Explicit `dataset_version` / `artifact_version` columns |
| `serving.*` | impression/outcome logs for production-like data | Future traffic generator / online path | Continuous append | Request ids and model versions |

### 3. MLflow Artifact Layer

Experiment tracking for local/offline pipeline execution.

| Stored Data | Written By | Update Pattern | Versioning |
|---|---|---|---|
| pipeline metrics, run params, selected artifacts | existing Item2Vec and retriever build modules | Per run | MLflow run id |

## Source Data Snapshot

Current raw parsed CSVs in object storage:

```text
s3://proj23-mlflow-artifacts/data/raw/content/30music_parsed/
  albums.csv
  events.csv
  love.csv
  persons.csv
  playlist_meta.csv
  playlist_tracks.csv
  session_meta.csv
  session_tracks.csv
  tags.csv
  tracks.csv
```

For the initial implementation, this snapshot is treated as:

- `raw_source_uri = s3://proj23-mlflow-artifacts/data/raw/content/30music_parsed/`
- `source_version = source_v1`

## Target Object-Storage Contract

The project currently uses a single bucket for many artifact types. The initial implementation reorganizes it logically by prefix:

```text
s3://proj23-mlflow-artifacts/
  data/raw/content/30music_parsed/
  processed/item2vec/{dataset_version}/
  features/retriever/{feature_version}/
  datasets/ranker/{dataset_version}/
  logs/data_pipelines/{run_id}/
  manifests/releases/{release_name}/{version}.json
```

## Versioning Rules

### Raw source version

- Current raw snapshot is labeled `source_v1`.
- If the parsed CSV snapshot is regenerated or replaced, a new `source_version` must be assigned.

### Dataset version

- Processed Item2Vec-ready release uses `dataset_version`.
- Default rule in code: UTC timestamp-based version such as `v20260405-153012-item2vec`.
- The version is immutable once published.

### Feature version

- Retriever release uses `feature_version`.
- Default rule in code: UTC timestamp-based version such as `v20260405-154455-retriever`.
- Feature manifests must reference the upstream processed dataset version they were derived from.

## Manifest Contract

Every published release stores a `manifest.json` both:

- inside the versioned release prefix
- under `manifests/releases/{release_name}/{version}.json`

Each manifest includes:

- release name and version
- release URI
- source version and raw source URI
- git commit
- pipeline name
- input objects
- output objects
- selected metrics
- upstream release references
- human-readable notes

## Data Flow

### Processed Item2Vec release

```mermaid
flowchart LR
    A[Chameleon raw parsed CSV snapshot] --> B[Local Item2Vec Stage A-D pipeline]
    B --> C[artifacts/item2vec local outputs]
    C --> D[publish_item2vec_release.py]
    D --> E[processed/item2vec/{dataset_version}/]
    D --> F[manifests/releases/processed_item2vec/{dataset_version}.json]
```

### Retriever feature release

```mermaid
flowchart LR
    A[processed Item2Vec artifacts] --> B[split/cooc/pref_nn/popularity build]
    B --> C[artifacts/retriever local outputs]
    C --> D[publish_retriever_release.py]
    D --> E[features/retriever/{feature_version}/]
    D --> F[manifests/releases/retriever_features/{feature_version}.json]
```

## Initial Implementation Files

### New release helpers

- `src/data_release/versioning.py`
- `src/data_release/manifest.py`
- `src/data_release/object_store.py`
- `src/data_release/publish_item2vec_release.py`
- `src/data_release/publish_retriever_release.py`

### New shell entrypoints

- `scripts/run_item2vec_release.sh`
- `scripts/run_retriever_release.sh`

## Immediate TODO Checklist

### Completed

- raw parsed CSV snapshot stored in object storage
- local Item2Vec processing code
- local retriever artifact build code
- PostgreSQL bootstrap/schema for future metadata registry

### In progress now

- versioned processed release publishing
- versioned retriever release publishing
- central manifest generation
- course-facing data design document

### Next after this initial implementation

- register release metadata into PostgreSQL automatically
- build versioned ranker train/eval datasets from production-like logs
- implement traffic generator hitting hypothetical service endpoints
- implement online feature computation path demo

## Suggested Demo Flow

1. Confirm raw CSV snapshot exists in object storage.
2. Run `scripts/run_item2vec_release.sh`.
3. Show newly published `processed/item2vec/{dataset_version}/`.
4. Open the generated manifest in object storage.
5. Run `scripts/run_retriever_release.sh`.
6. Show newly published `features/retriever/{feature_version}/`.
7. Open the feature manifest and highlight upstream dataset linkage.

## Important Initial-Implementation Assumptions

- Raw input in object storage is already parsed CSV, not original idomaar.
- One shared bucket is acceptable for this stage as long as prefixes are clearly separated.
- Manifest-based lineage is the fastest first step; PostgreSQL registration follows immediately after.
- Local pipeline execution is the build environment; object storage is the course-facing source of truth.

