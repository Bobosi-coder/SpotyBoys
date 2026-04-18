# SpotyBoys Iteration 2 Gap Analysis

## Source Of Truth

- `../music_recommender_updated_implementation_plan_agent.md`
- `../MlOps_system_architecture-Technicaldesign.pdf`
- `../MlOps_system_architecture-retraining_data_flow.pdf`
- `../MlOps_system_architecture-UI.pdf`, used for frontend state and honesty guidance only where it does not conflict with the newer SpotiBoys drawer UI.

`spotiboys_implementation_plan_agent.md` and the latest Option B UI redraft files were not present in this checkout. The updated implementation plan is therefore the active execution artifact.

## Current Branch Summary

The `serving_requirements` branch already implemented a useful demo slice:

- FastAPI shells for recommendation and event APIs.
- Shared Pydantic contracts for bootstrap, recommendation, queue, events, playability, and manifests.
- PostgreSQL schema and repository boundary for users, sessions, playable tracks, mappings, impressions, playback events, feedback events, outcomes, model versions, and retraining runs.
- Redis runtime-state adapter for queue and idempotency keys.
- Same-origin nginx routing for frontend, API routes, and stream paths.
- SpotiBoys UI shell with left brand rail, center browse surface, playlist drawer, and bottom dock.
- Fixture catalog, demo runbook, manifest validators, and unit tests.

## Usable As-Is

- `apps/frontend-web/src/app.js`: correct latest SpotiBoys layout behavior. Drawer state is local, queue contents are backend-owned, and playback starts only after stream resolution.
- `packages/shared_contracts/*`: contract caps and enums match the current plan.
- `db/003_option_b_online_contracts.sql`: durable online schema is directionally correct and now includes mapping availability status.
- `packages/db_access/runtime_state.py`: Redis key model matches the approved runtime/dedupe boundary.
- `infra/nginx/default.conf`: same-origin ingress routes are the correct public browser surface.

## Partially Reusable

- `packages/recommendation_engine/service.py`: the service boundary was correct, but iteration 1 ranked fixture tracks directly. It now accepts a manifest-confirmed serving bundle and ranks playable tracks through `pop_scores.csv`.
- `packages/navidrome_adapter/media_access.py`: the playability boundary was correct, but iteration 1 returned generated WAV bytes. It now supports fixture-file and Navidrome proxy modes behind the same `/stream/{track_id}` contract.
- `packages/artifact_runtime/manifest_validator.py`: the manifest validator existed; it now also validates that required bundle files exist on disk.
- `infra/scripts/*`: demo scripts existed; they now target the root VM1 Compose topology.

## Demo-Only Or Mock-Only Before Iteration 2

- Generated WAV bytes were used as the real stream response.
- Recommendation ordering was deterministic fixture order with model version `demo-fixture-v1`.
- No Navidrome service existed in Compose.
- No catalog-sync worker existed.
- Parser export and outcome derivation were README-level scaffolds only.
- VM2 handoff had validators but no local object-storage or MLflow mimic.

## Serving-Critical Mismatches Fixed Or Reduced

- The real media path is no longer hardwired to generated audio. Generated WAVs remain only for test/local fixture generation.
- Recommendation API startup now validates a local serving bundle and registers an active model version.
- Local fixture music generation exists so the same media path can be validated without the real VM library.
- Catalog-sync and outcome-deriver runnable workers now exist.
- Parser export now writes a manifest-backed delta directory under the object-storage contract.
- Root Compose files now represent VM1 serving, VM2 mimic, and full local validation.

## Remaining Iteration 3 Gaps

- Local Compose includes a real Navidrome container, but local fixture streaming currently uses ID-addressable fixture files for deterministic automated validation. Full Navidrome Subsonic user bootstrap and scan-ID reconciliation should be completed next.
- The artifact-backed engine currently consumes `pop_scores.csv` from the approved bundle shape. Loading GRU/co-occurrence artifacts for richer ranking remains next work.
- Parser export writes CSV-compatible data using `.parquet` contract filenames if parquet dependencies are not installed in the service image. Production parquet writing should be added when the VM image includes `pyarrow`.
- VM2 retraining and promotion services are scaffolds; they validate topology and separation, not full GPU retraining.
