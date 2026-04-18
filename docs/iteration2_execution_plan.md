# SpotyBoys Iteration 2 Execution Plan

## Implementation Order

1. Freeze config for local fixture mode and VM library mode.
2. Add serving-bundle directory validation and active model version registration.
3. Rank recommendations through the serving-bundle boundary while hard-filtering to playable mappings.
4. Replace the real stream path with media modes:
   - `fixture-generated` for unit tests only,
   - `fixture-file` for local deterministic fixture media,
   - `navidrome` for VM/internal Navidrome proxy streaming.
5. Add catalog sync, outcome derivation, parser export, and fixture music generation commands.
6. Add VM1, VM2, and full Compose topology.
7. Update demo scripts, health checks, docs, and tests.

## Compose Topology

`docker-compose.yml` is the VM1 serving mimic:

- nginx reverse proxy, public on `127.0.0.1:5173`
- frontend-web
- recommendation-api
- event-api
- PostgreSQL
- Redis
- Navidrome, internal only
- fixture music generator
- catalog-sync-worker
- outcome-deriver-worker profile job

`docker-compose.vm2.yml` is the VM2 retraining mimic:

- object-storage-compatible MinIO service
- MLflow scaffold
- retraining-runner scaffold
- promotion-gate scaffold

`docker-compose.full.yml` includes both stacks for local validation. VM2 remains outside the request path.

## Environment Contract

- `SPOTIBOYS_RUNTIME_MODE`: `fixture`, `postgres`, `compose`, or `production`.
- `DATABASE_URL`: durable PostgreSQL URL.
- `REDIS_URL`: Redis runtime-state URL.
- `SPOTIBOYS_FIXTURE_PATH`: local seed catalog path.
- `SPOTIBOYS_MEDIA_MODE`: `fixture-generated`, `fixture-file`, or `navidrome`.
- `SPOTIBOYS_MUSIC_ROOT`: local fixture or VM music root.
- `NAVIDROME_BASE_URL`: internal Navidrome URL.
- `NAVIDROME_USERNAME`, `NAVIDROME_PASSWORD`, `NAVIDROME_TOKEN`, `NAVIDROME_SALT`: internal media credentials.
- `SPOTIBOYS_SERVING_BUNDLE_PATH`: local staged `Real_service/<version>` bundle.
- `SPOTIBOYS_OBJECT_STORAGE_ROOT`: local filesystem object-storage root for parser export.
- `MLFLOW_TRACKING_URI`: VM2 MLflow endpoint.

## Local Fixture Mode

Local fixture mode generates tiny WAV files from `fixtures/demo_catalog.json`, stores them under `.local/fixture_music`, and mounts them into the VM1 services. This validates the same first-party `/playable-track/{track_id}` and `/stream/{track_id}` contracts without requiring the full VM music library.

## VM Library Mode

VM mode uses the same code and Compose topology with:

- `SPOTIBOYS_MEDIA_MODE=navidrome`
- `SPOTIBOYS_MUSIC_ROOT` mounted to the real VM1 library
- `NAVIDROME_BASE_URL` pointing to internal Navidrome
- `SPOTIBOYS_SERVING_BUNDLE_PATH` pointing to a manifest-confirmed local `Real_service/<version>` bundle

No frontend changes are required.

## Acceptance Criteria

- Recommendation API readiness fails if the serving bundle is invalid.
- Recommendation responses are capped and playable-only.
- Stream URLs remain first-party same-origin paths.
- Missing or quarantined mappings fail closed.
- Redis remains runtime-only.
- PostgreSQL remains durable truth.
- VM2 services are not required for serving availability.
